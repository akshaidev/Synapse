"""
api/main.py — Module 7: FastAPI Core & Mock Webhook Orchestrator

PRD Reference:
- §5.1 ADM-01 through ADM-09
- §4.5 Confidence Aggregation & Tiered Intervention Decision
- §3.2 Bank Webhook Payload Schema

Pipeline Flow:
    POST /api/v1/ingest
        └─ Dual-Gate Golden Hour Check (Gate 1: Fraud Recency, Gate 2: Payload Freshness)
            └─ Stage 1: graph.isolate_terminal_mule()  → mule account + MPS
                └─ Viability Filter → NO_VIABLE_ATM_MULE guard
                    └─ Stage 2: temporal.compute_drain_time()  → drain_time + urgency
                        └─ Stage 3: cluster.rank_atms()  → Top 3 ATMs + RiskScore
                            └─ Confidence Aggregation [v1.3 FIX 4C] + Tier Assignment
                                └─ Webhook Dispatch (PRIMARY_DIGITAL / SECONDARY_PHYSICAL)

Simulation Mode:
    Pass header  X-Simulation-Mode: true  (or query param ?simulate=true) to use
    payload.ingestion_timestamp as the Golden Hour reference clock instead of datetime.now().
    This lets static demo payloads pass Gate 1 during automated testing without altering timestamps.

Webhook Retry:
    Configurable BASE_RETRY_DELAY_SECONDS (default 5.0s).  Tests inject
    SYNAPSE_WEBHOOK_DELAY env-var (or call _set_retry_delay()) to use 0.01s
    so the retry suite doesn't block for 35+ seconds.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx
import pathlib
from filelock import FileLock
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.schemas import (
    FreezeCardATMRequest,
    IncidentPayload,
    InterventionTier,
    LocationMethod,
    ATMBlock,
    ATMBlockType,
    CardHold,
    HoldType,
    Justification,
    RequestingAuthority,
    DispatchRequest,
    ResolveIncidentRequest,
    LienActionRequest,
)
from core.cluster import rank_atms, Stage3Result
from core.graph import isolate_terminal_mule, Stage1Result
from core.temporal import compute_drain_time, DrainTimeResult

# ─────────────────────────────────────────────────────────────────────────────
# Logging & Constants
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("synapse.api")

# Confidence Aggregation weights per PRD §4.5
GAMMA_MPS: float = 0.20         # γ1  — mule identification certainty
GAMMA_URGENCY: float = 0.35     # γ2  — temporal urgency
GAMMA_SPATIAL: float = 0.45     # γ3  — spatial targeting confidence

# Intervention Tier Thresholds per PRD §4.5 & Assumptions.MD
THRESHOLD_PRIMARY_DIGITAL: float = 0.70
THRESHOLD_SECONDARY_PHYSICAL: float = 0.85

# IFSC Fallback confidence cap per PRD §4.5 & Assumptions.MD
IFSC_FALLBACK_CONFIDENCE_CAP: float = 0.75

# Webhook retry policy per Assumptions.MD [Engineering Trade-off]
MAX_WEBHOOK_RETRIES: int = 3
_RETRY_DELAY_SECONDS: float = float(os.environ.get("SYNAPSE_WEBHOOK_DELAY", 5.0))

# ATM Registry (in-memory, populated at startup)
_ATM_REGISTRY: List[Dict[str, Any]] = []

# In-memory incident log (backed by data/incidents.json)
_incidents: List[Dict[str, Any]] = []
_INCIDENTS_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "incidents.json"
)

def _persist_incidents() -> None:
    """Write the in-memory incidents to disk (data/incidents.json)."""
    try:
        with FileLock(f"{_INCIDENTS_PATH}.lock", timeout=5):
            with open(_INCIDENTS_PATH, "w", encoding="utf-8") as f:
                json.dump(_incidents, f, indent=2, default=str)
    except Exception as exc:
        logger.error(f"[INCIDENTS] Failed to persist registry: {exc}")

# In-memory lien registry (backed by data/lien_registry.json)
_LIEN_REGISTRY: Dict[str, Any] = {"active_liens": {}, "revocation_log": []}
_LIEN_REGISTRY_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "lien_registry.json"
)

# Per-lien async lock — prevents duplicate INITIATE from concurrent requests
# NOTE: Effective only while running single-process (uvicorn --workers 1).
# Multiple workers would each get their own lock + their own _LIEN_REGISTRY.
_LIEN_LOCK: asyncio.Lock = asyncio.Lock()


def _persist_lien_registry() -> None:
    """Write the in-memory lien registry to disk (data/lien_registry.json)."""
    try:
        with FileLock(f"{_LIEN_REGISTRY_PATH}.lock", timeout=5):
            with open(_LIEN_REGISTRY_PATH, "w", encoding="utf-8") as f:
                json.dump(_LIEN_REGISTRY, f, indent=2, default=str)
        logger.info(f"[LIEN] Registry persisted to {_LIEN_REGISTRY_PATH}")
    except Exception as exc:
        logger.error(f"[LIEN] Failed to persist registry: {exc}")

# In-memory dispatch registry (backed by data/sent_crew.json)
# Format: {"{ncrp_ticket_id}:{atm_id}": {"rank": int, "atmId": str, "ncrpId": str, "dispatchedAt": str}}
_DISPATCH_REGISTRY: Dict[str, Any] = {}
_DISPATCH_REGISTRY_PATH: str = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "sent_crew.json"
)

def _persist_dispatch_registry() -> None:
    """Write the in-memory dispatch registry to disk (data/sent_crew.json)."""
    try:
        with FileLock(f"{_DISPATCH_REGISTRY_PATH}.lock", timeout=5):
            with open(_DISPATCH_REGISTRY_PATH, "w", encoding="utf-8") as f:
                json.dump(_DISPATCH_REGISTRY, f, indent=2, default=str)
        logger.info(f"[DISPATCH] Registry persisted to {_DISPATCH_REGISTRY_PATH}")
    except Exception as exc:
        logger.error(f"[DISPATCH] Failed to persist registry: {exc}")

# Mock webhook endpoint (self-referential — receiver on the same server)
MOCK_WEBHOOK_BASE_URL = os.environ.get("SYNAPSE_BASE_URL", "http://localhost:8000")
MOCK_WEBHOOK_PATH = "/api/v1/freeze-card-atm"

# Injectable HTTP client for webhook dispatch.
# Tests override this with the FastAPI TestClient so webhooks stay in-process.
# Default: None → _dispatch_webhook uses a real httpx.Client.
_WEBHOOK_HTTP_CLIENT: Any = None


def _set_webhook_client(client: Any) -> None:
    """Inject a test HTTP client for webhook dispatch (test use only)."""
    global _WEBHOOK_HTTP_CLIENT
    _WEBHOOK_HTTP_CLIENT = client


# ─────────────────────────────────────────────────────────────────────────────
# Configurable delay helper (tests call this to use 0.01 s)
# ─────────────────────────────────────────────────────────────────────────────
def _set_retry_delay(seconds: float) -> None:
    """Override the webhook backoff base delay (intended for test use)."""
    global _RETRY_DELAY_SECONDS
    _RETRY_DELAY_SECONDS = seconds


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Project Synapse — Fraud Interception API",
    description=(
        "Automated spatial-temporal cyber fraud interception framework. "
        "Ingests NCRP/CFCFRMS incident payloads, runs the Stage 1–3 pipeline, "
        "and dispatches card-hold webhooks to bank switches within the Golden Hour."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static file paths ─────────────────────────────────────────────────────────
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
_UI_INDEX = _PROJECT_ROOT / "ui" / "index.html"
_UI_FEED = _PROJECT_ROOT / "ui" / "feed.html"
_SYNTHETIC_DIR = _PROJECT_ROOT / "synthetic"

# Mount /synthetic so the UI's Quick Demo buttons can fetch JSON payloads directly
if _SYNTHETIC_DIR.exists():
    app.mount("/synthetic", StaticFiles(directory=str(_SYNTHETIC_DIR)), name="synthetic")


# ─────────────────────────────────────────────────────────────────────────────
# GET / — Serve Dashboard UI
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
async def serve_ui() -> FileResponse:
    """
    Serves the Project Synapse verification dashboard (ui/index.html).
    Run `uvicorn api.main:app --reload` and open http://localhost:8000.
    """
    if _UI_INDEX.exists():
        return FileResponse(str(_UI_INDEX), media_type="text/html")
    return JSONResponse(
        {"error": "UI not found. Place ui/index.html in the ui/ directory."},
        status_code=404,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /feed — Serve Bank Feed Simulator
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/feed", include_in_schema=False)
async def serve_feed() -> FileResponse:
    """Serves the Bank Feed Simulator portal (ui/feed.html) at /feed."""
    if _UI_FEED.exists():
        return FileResponse(str(_UI_FEED), media_type="text/html")
    return JSONResponse(
        {"error": "Feed UI not found. Place ui/feed.html in the ui/ directory."},
        status_code=404,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Startup — Load ATM Registry
# ─────────────────────────────────────────────────────────────────────────────
@app.on_event("startup")
def load_atm_registry() -> None:
    """Load the internal ATM registry from JSON into memory at server startup."""
    global _ATM_REGISTRY
    registry_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "atm_registry.json"
    )
    if not os.path.exists(registry_path):
        logger.error(f"ATM registry not found at {registry_path}. Spatial pipeline will fail.")
        return

    with open(registry_path, "r", encoding="utf-8") as f:
        _ATM_REGISTRY = json.load(f)

    logger.info(f"[STARTUP] ATM registry loaded: {len(_ATM_REGISTRY)} records from {registry_path}")


@app.on_event("startup")
def load_lien_registry() -> None:
    """Load the lien registry from JSON into memory at server startup."""
    global _LIEN_REGISTRY
    if os.path.exists(_LIEN_REGISTRY_PATH):
        try:
            with open(_LIEN_REGISTRY_PATH, "r", encoding="utf-8") as f:
                _LIEN_REGISTRY = json.load(f)
            active_count = len(_LIEN_REGISTRY.get("active_liens", {}))
            revoke_count = len(_LIEN_REGISTRY.get("revocation_log", []))
            logger.info(
                f"[STARTUP] Lien registry loaded: {active_count} active liens, "
                f"{revoke_count} revocation log entries from {_LIEN_REGISTRY_PATH}"
            )
        except Exception as exc:
            logger.error(f"[STARTUP] Failed to load lien registry: {exc}. Starting empty.")
            _LIEN_REGISTRY = {"active_liens": {}, "revocation_log": []}
    else:
        logger.info(f"[STARTUP] Lien registry not found at {_LIEN_REGISTRY_PATH}. Creating empty.")
        _LIEN_REGISTRY = {"active_liens": {}, "revocation_log": []}
        _persist_lien_registry()


@app.on_event("startup")
def load_dispatch_registry() -> None:
    """Load the dispatch registry from JSON into memory at server startup."""
    global _DISPATCH_REGISTRY
    if os.path.exists(_DISPATCH_REGISTRY_PATH):
        try:
            with open(_DISPATCH_REGISTRY_PATH, "r", encoding="utf-8") as f:
                _DISPATCH_REGISTRY = json.load(f)
            logger.info(
                f"[STARTUP] Dispatch registry loaded: {len(_DISPATCH_REGISTRY)} records from {_DISPATCH_REGISTRY_PATH}"
            )
        except Exception as exc:
            logger.error(f"[STARTUP] Failed to load dispatch registry: {exc}. Starting empty.")
            _DISPATCH_REGISTRY = {}
    else:
        logger.info(f"[STARTUP] Dispatch registry not found at {_DISPATCH_REGISTRY_PATH}. Creating empty.")
        _DISPATCH_REGISTRY = {}
        _persist_dispatch_registry()


@app.on_event("startup")
def load_incidents() -> None:
    """Load the incidents from JSON into memory at server startup."""
    global _incidents
    if os.path.exists(_INCIDENTS_PATH):
        try:
            with open(_INCIDENTS_PATH, "r", encoding="utf-8") as f:
                _incidents = json.load(f)
            logger.info(
                f"[STARTUP] Incidents loaded: {len(_incidents)} records from {_INCIDENTS_PATH}"
            )
        except Exception as exc:
            logger.error(f"[STARTUP] Failed to load incidents: {exc}. Starting empty.")
            _incidents = []
    else:
        logger.info(f"[STARTUP] Incidents not found at {_INCIDENTS_PATH}. Creating empty.")
        _incidents = []
        _persist_incidents()


async def incident_garbage_collector() -> None:
    """
    Background task that sweeps the incidents array every 60 seconds
    to enforce 6-hour tactical timeouts and 30-day admin closures.
    """
    while True:
        await asyncio.sleep(60)
        
        now_dt = datetime.now(timezone.utc)
        changed = False
        
        for inc in _incidents:
            ingestion_str = inc.get("payload_snapshot", {}).get("ingestion_timestamp")
            if not ingestion_str:
                # Fallback to complaint_timestamp for legacy payloads
                ingestion_str = inc.get("payload_snapshot", {}).get("complaint_timestamp")
            if not ingestion_str:
                continue
                
            try:
                ingestion_dt = datetime.fromisoformat(ingestion_str)
            except ValueError:
                continue
                
            # 6-Hour Tactical Timeout — only if not already resolved by a human
            if inc.get("status") == "ACTIVE" and not inc.get("resolved"):
                if (now_dt - ingestion_dt) > timedelta(hours=6):
                    inc["status"] = "TACTICAL_TIMEOUT"
                    changed = True
                    logger.info(f"[SWEEPER] {inc['ncrp_ticket_id']} -> TACTICAL_TIMEOUT (6h elapsed)")
            
            # 30-Day Administrative Closure — distinct from resolved; Synapse disengages,
            # case deferred to NCRP standard procedure. Does NOT set resolved = True.
            if inc.get("status") not in ["RESOLVED", "ADMINISTRATIVELY_CLOSED"] and not inc.get("resolved"):
                if (now_dt - ingestion_dt) > timedelta(days=30):
                    inc["status"] = "ADMINISTRATIVELY_CLOSED"
                    inc["synapse_disengaged_at"] = now_dt.isoformat()
                    changed = True
                    logger.info(f"[SWEEPER] {inc['ncrp_ticket_id']} -> ADMINISTRATIVELY_CLOSED (30d elapsed)")
                    
        if changed:
            _persist_incidents()


@app.on_event("startup")
def start_garbage_collector() -> None:
    """Launch the background garbage collector task."""
    logger.info("[STARTUP] Launching Incident Garbage Collector...")
    asyncio.create_task(incident_garbage_collector())


# ─────────────────────────────────────────────────────────────────────────────
# Response Models
# ─────────────────────────────────────────────────────────────────────────────
class PipelineStageStatus(BaseModel):
    """Per-stage execution summary returned in the ingest response."""
    stage: str
    status: str
    detail: Optional[str] = None


class IngestResponse(BaseModel):
    """Structured response for POST /api/v1/ingest."""
    synapse_incident_id: str
    ncrp_ticket_id: str
    status: str                         # PROCESSED | GOLDEN_HOUR_EXPIRED | STALE_PAYLOAD | NO_VIABLE_ATM_MULE | PIPELINE_ERROR
    intervention_tier: Optional[str] = None   # TIER_1_LOG_ONLY | PRIMARY_DIGITAL | SECONDARY_PHYSICAL
    confidence_score: Optional[float] = None
    mps_score: Optional[float] = None
    urgency_score: Optional[float] = None
    top_atm_risk_score: Optional[float] = None
    drain_time_remaining_minutes: Optional[float] = None
    drainable_today_inr: Optional[float] = None
    mule_location_method: Optional[str] = None
    mule_estimated_lat: Optional[float] = None   # Mule estimated position for UI map
    mule_estimated_lon: Optional[float] = None   # Mule estimated position for UI map
    ifsc_fallback_cap_applied: Optional[bool] = None
    top_atms: Optional[List[Dict[str, Any]]] = None
    webhook_dispatched: bool = False
    webhook_status: Optional[str] = None    # SUCCESS | FAILED | NOT_APPLICABLE
    stages: List[PipelineStageStatus] = Field(default_factory=list)
    simulation_mode: bool = False
    payload_snapshot: Optional[Dict[str, Any]] = None  # Phase 09 F03: raw intelligence for UI panel


# ─────────────────────────────────────────────────────────────────────────────
# Confidence Aggregation (§4.5)
# ─────────────────────────────────────────────────────────────────────────────
def _compute_confidence(
    mps_score: float,
    urgency_score: float,
    top_atm_risk_score: float,
    daily_limit_exhausted: bool,
    confidence_cap_applied: bool,
) -> float:
    """
    Composite Confidence: C = γ1×MPS + γ2×Urgency + γ3×RiskScore_top1

    [v1.3 FIX 4C] Urgency Guard:
        - If daily_limit_exhausted: urgency_score MUST be 0.0 (passed in from temporal.py)
    IFSC Fallback Cap:
        - If confidence_cap_applied: C = min(C, 0.75)
    """
    # Urgency is already guarded to 0.0 by temporal.py when daily_limit_exhausted.
    # We enforce the guard again here as a defense-in-depth safety net.
    effective_urgency = 0.0 if daily_limit_exhausted else urgency_score

    raw_confidence = (
        GAMMA_MPS * mps_score
        + GAMMA_URGENCY * effective_urgency
        + GAMMA_SPATIAL * top_atm_risk_score
    )
    # Clamp to [0.0, 1.0]
    confidence = min(1.0, max(0.0, raw_confidence))

    # IFSC Fallback confidence cap (max 0.75 → PRIMARY_DIGITAL only, never SECONDARY_PHYSICAL)
    if confidence_cap_applied:
        confidence = min(confidence, IFSC_FALLBACK_CONFIDENCE_CAP)

    return round(confidence, 4)


def _assign_tier(confidence: float) -> str:
    """Assign intervention tier based on composite confidence per PRD §4.5."""
    if confidence >= THRESHOLD_SECONDARY_PHYSICAL:
        return "SECONDARY_PHYSICAL"
    elif confidence >= THRESHOLD_PRIMARY_DIGITAL:
        return "PRIMARY_DIGITAL"
    else:
        return "TIER_1_LOG_ONLY"


# ─────────────────────────────────────────────────────────────────────────────
# Webhook Dispatcher
# ─────────────────────────────────────────────────────────────────────────────
def _build_webhook_payload(
    incident_id: str,
    payload: IncidentPayload,
    stage1: Stage1Result,
    stage2: DrainTimeResult,
    stage3: Stage3Result,
    confidence: float,
    tier: str,
    reference_time: datetime,
) -> FreezeCardATMRequest:
    """Constructs FreezeCardATMRequest from pipeline outputs per PRD §3.2."""
    mule = payload.terminal_mule
    last_txn_ts = max(t.txn_timestamp for t in payload.fund_flow.transactions)
    golden_hour_expiry = last_txn_ts + timedelta(minutes=120)

    atm_blocks = [
        ATMBlock(
            atm_id=atm.atm_id,
            bank_name=atm.bank_name,
            block_type=ATMBlockType.CARD_SPECIFIC_BLOCK,
            risk_rank=atm.risk_rank,
            risk_score=atm.risk_score,
        )
        for atm in stage3.top_atms
    ]

    return FreezeCardATMRequest(
        webhook_version="1.1.0",
        request_id=uuid.uuid4(),
        synapse_incident_id=uuid.UUID(incident_id),
        ncrp_ticket_id=payload.ncrp_ticket.ticket_id,
        request_timestamp=reference_time,
        requesting_authority=RequestingAuthority(
            authorized_officer_id="I4C-SYS-AUTO-001"
        ),
        golden_hour_expiry=golden_hour_expiry,
        confidence_score=confidence,
        intervention_tier=InterventionTier(tier),
        card_hold=CardHold(
            card_number_hash=mule.linked_card_number_hash,
            hold_type=HoldType.ATM_WITHDRAWAL_BLOCK,
            hold_duration_minutes=120,
            mule_account_number=mule.mule_account_number,
            mule_ifsc=mule.mule_ifsc,
        ),
        atm_blocks=atm_blocks,
        justification=Justification(
            drain_time_remaining_minutes=stage2.drain_time_remaining_minutes,
            drainable_today_inr=stage2.drainable_today_inr,
            fund_flow_depth=payload.fund_flow.total_hops,
            total_amount_inr=payload.ncrp_ticket.amount_inr,
            mule_location_method=stage3.location_method,
        ),
        callback_url=f"{MOCK_WEBHOOK_BASE_URL}/api/v1/webhook-callback/{incident_id}",
    )


async def _dispatch_webhook_async(webhook_payload: FreezeCardATMRequest) -> str:
    """
    Dispatches webhook to the mock /api/v1/freeze-card-atm endpoint.
    Uses exponential backoff: base_delay → 2×base_delay → 4×base_delay (max 3 retries).
    Returns: "SUCCESS" | "FAILED"

    ASYNC FIX: Previously used httpx.Client (sync) inside an async def endpoint,
    which froze the event loop — the self-referential POST to localhost:8000 could
    never be accepted while the loop was blocked → all 4 attempts timed out.
    Now uses httpx.AsyncClient + await so the event loop stays free to accept
    the incoming webhook connection during the await.

    Injectable client:
        If _WEBHOOK_HTTP_CLIENT is set (e.g. FastAPI TestClient in tests), it is used
        synchronously (TestClient is sync-only) so tests remain unaffected.
    """
    target_url = f"{MOCK_WEBHOOK_BASE_URL}{MOCK_WEBHOOK_PATH}"
    payload_dict = webhook_payload.model_dump(mode="json", exclude_none=True)

    for attempt in range(1, MAX_WEBHOOK_RETRIES + 2):  # attempts 1..4 (1 original + 3 retries)
        try:
            logger.info(
                f"[WEBHOOK] Attempt {attempt}/{MAX_WEBHOOK_RETRIES + 1} → POST {target_url} "
                f"(incident: {webhook_payload.synapse_incident_id})"
            )
            # Injected test client is sync (TestClient); real path is async.
            if _WEBHOOK_HTTP_CLIENT is not None:
                response = _WEBHOOK_HTTP_CLIENT.post(MOCK_WEBHOOK_PATH, json=payload_dict)
            else:
                async with httpx.AsyncClient(timeout=10.0) as http:
                    response = await http.post(target_url, json=payload_dict)

            if response.status_code == 200:
                logger.info(
                    f"[WEBHOOK] SUCCESS on attempt {attempt} — "
                    f"ncrp={webhook_payload.ncrp_ticket_id}, "
                    f"confidence={webhook_payload.confidence_score:.4f}, "
                    f"tier={webhook_payload.intervention_tier}"
                )
                return "SUCCESS"
            else:
                logger.warning(
                    f"[WEBHOOK] Attempt {attempt} returned HTTP {response.status_code}. "
                    f"Body: {response.text[:200]}"
                )
        except Exception as exc:
            logger.warning(f"[WEBHOOK] Attempt {attempt} raised exception: {exc}")

        if attempt <= MAX_WEBHOOK_RETRIES:
            delay = _RETRY_DELAY_SECONDS * (2 ** (attempt - 1))  # 5 → 10 → 20
            logger.info(f"[WEBHOOK] Backing off {delay:.2f}s before retry {attempt + 1}…")
            await asyncio.sleep(delay)  # non-blocking — event loop stays free during backoff

    logger.error(
        f"[WEBHOOK] All {MAX_WEBHOOK_RETRIES + 1} attempts FAILED for incident "
        f"{webhook_payload.synapse_incident_id}. Marking WEBHOOK_FAILED."
    )
    return "FAILED"


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/ingest — Pipeline Orchestrator
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/ingest",
    response_model=IngestResponse,
    summary="Ingest Incident Payload & Run Full Pipeline",
    tags=["Core Pipeline"],
)
async def ingest_incident(
    payload: IncidentPayload,
    x_simulation_mode: Optional[str] = Header(
        default=None,
        alias="X-Simulation-Mode",
        description="Set to 'true' to evaluate Golden Hour gates against ingestion_timestamp instead of NOW().",
    ),
    simulate: Optional[bool] = Query(
        default=False,
        description="Alias for X-Simulation-Mode header. Evaluate Golden Hour against ingestion_timestamp.",
    ),
) -> IngestResponse:
    """
    Main ingestion endpoint. Runs the full Synapse pipeline synchronously:

    1. Dual-Gate Golden Hour validation.
    2. Stage 1 — Terminal Mule Isolation & Viability Filter.
    3. Stage 2 — Capped Drain Time Engine.
    4. Stage 3 — Haversine Spatial Ranker.
    5. Confidence Aggregation + Tiered Intervention Decision.
    6. Webhook Dispatch (if C ≥ 0.70).

    Simulation Mode: Header `X-Simulation-Mode: true` or `?simulate=true` sets reference clock
    to `payload.ingestion_timestamp` so static demo payloads always pass Gate 1.
    """
    incident_id = str(uuid.uuid4())
    simulation_active = (x_simulation_mode or "").strip().lower() == "true" or (simulate is True)
    stages: List[PipelineStageStatus] = []

    if simulation_active:
        logger.info(
            f"[INGEST {incident_id}] ⚡ Simulation mode ACTIVE — "
            f"Golden Hour gates BYPASSED. T_ref = ingestion_timestamp."
        )

    # ── Reference clock ───────────────────────────────────────────────────────
    # Simulation mode: use ingestion_timestamp → fully deterministic pipeline output
    #   (same MPS, drain time, and ATM scores on every run regardless of wall clock).
    # Live mode: use datetime.now(UTC) → gates are enforced and τ reflects true elapsed time.
    now_utc = datetime.now(timezone.utc)

    if simulation_active:
        reference_time = payload.ingestion_timestamp
    else:
        reference_time = now_utc

        # ── Gate 1: Fraud Recency — NOW() − max(txn_timestamp) ≤ 120 min ─────
        t_latest = max(t.txn_timestamp for t in payload.fund_flow.transactions)
        if t_latest.tzinfo is None:
            t_latest = t_latest.replace(tzinfo=timezone.utc)
        gate1_delta = now_utc - t_latest

        if gate1_delta.total_seconds() > 120 * 60:
            stages.append(PipelineStageStatus(
                stage="DUAL_GATE_GOLDEN_HOUR",
                status="FAIL",
                detail=(
                    f"Gate 1 FAILED: Fraud recency {gate1_delta.total_seconds() / 60:.1f} min "
                    f"> 120 min. Enable Simulation Mode to bypass."
                ),
            ))
            logger.warning(
                f"[INGEST {incident_id}] GOLDEN_HOUR_EXPIRED — "
                f"recency={gate1_delta.total_seconds() / 60:.1f} min"
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    f"GOLDEN_HOUR_EXPIRED: Fraud recency exceeds 120 mins "
                    f"(Delta: {gate1_delta.total_seconds() / 60:.1f} mins)"
                ),
            )

        # ── Gate 2: Payload Freshness — NOW() − complaint_timestamp ≤ 240 min ─
        complaint_ts = payload.ncrp_ticket.complaint_timestamp
        if complaint_ts.tzinfo is None:
            complaint_ts = complaint_ts.replace(tzinfo=timezone.utc)
        gate2_delta = now_utc - complaint_ts

        if gate2_delta.total_seconds() > 240 * 60:
            stages.append(PipelineStageStatus(
                stage="DUAL_GATE_GOLDEN_HOUR",
                status="FAIL",
                detail=(
                    f"Gate 2 FAILED: Complaint {gate2_delta.total_seconds() / 60:.1f} min "
                    f"> 240 min. Enable Simulation Mode to bypass."
                ),
            ))
            logger.warning(
                f"[INGEST {incident_id}] STALE_PAYLOAD — "
                f"complaint_age={gate2_delta.total_seconds() / 60:.1f} min"
            )
            raise HTTPException(
                status_code=422,
                detail=(
                    f"STALE_PAYLOAD: Complaint is older than 240 mins "
                    f"(Delta: {gate2_delta.total_seconds() / 60:.1f} mins)"
                ),
            )

    # Ensure timezone-aware
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)

    # ── Gates passed or bypassed — log stage ─────────────────────────────────
    if simulation_active:
        gate_detail = (
            "⚡ Simulation mode — Golden Hour gates BYPASSED. "
            "T_ref = ingestion_timestamp (deterministic pipeline output)."
        )
    else:
        gate_detail = (
            f"Gate 1 (Fraud Recency ≤ 120 min, δ={gate1_delta.total_seconds() / 60:.1f} min) and "
            f"Gate 2 (Payload Freshness ≤ 240 min, δ={gate2_delta.total_seconds() / 60:.1f} min) "
            f"both passed."
        )

    stages.append(PipelineStageStatus(
        stage="DUAL_GATE_GOLDEN_HOUR",
        status="PASS",
        detail=gate_detail,
    ))
    logger.info(
        f"[INGEST {incident_id}] ncrp={payload.ncrp_ticket.ticket_id} — "
        f"{'Gates BYPASSED (sim)' if simulation_active else 'Gates PASSED'}. "
        f"Reference clock: {reference_time.isoformat()}"
    )

    # ── Stage 1: Terminal Mule Isolation ─────────────────────────────────────
    try:
        stage1: Stage1Result = isolate_terminal_mule(
            fund_flow=payload.fund_flow,
            terminal_mule=payload.terminal_mule,
            current_time=reference_time,
        )
    except Exception as exc:
        stages.append(PipelineStageStatus(stage="STAGE_1_GRAPH", status="ERROR", detail=str(exc)))
        logger.error(f"[INGEST {incident_id}] Stage 1 EXCEPTION: {exc}")
        result = IngestResponse(
            synapse_incident_id=incident_id,
            ncrp_ticket_id=payload.ncrp_ticket.ticket_id,
            status="PIPELINE_ERROR",
            stages=stages,
            simulation_mode=simulation_active,
        )
        _incidents.append(result.model_dump())
        _persist_incidents()
        return result

    if not stage1.is_viable:
        stages.append(PipelineStageStatus(
            stage="STAGE_1_GRAPH",
            status="NO_VIABLE_ATM_MULE",
            detail=stage1.disqualification_reason,
        ))
        logger.warning(f"[INGEST {incident_id}] {stage1.disqualification_reason}")
        result = IngestResponse(
            synapse_incident_id=incident_id,
            ncrp_ticket_id=payload.ncrp_ticket.ticket_id,
            status="NO_VIABLE_ATM_MULE",
            mps_score=stage1.mps_score,
            stages=stages,
            simulation_mode=simulation_active,
        )
        _incidents.append(result.model_dump())
        _persist_incidents()
        return result

    stages.append(PipelineStageStatus(
        stage="STAGE_1_GRAPH",
        status="VIABLE_ATM_MULE",
        detail=(
            f"Selected mule: {stage1.selected_mule_account} ({stage1.selected_mule_bank}) | "
            f"MPS={stage1.mps_score:.4f}"
            + (f" | ⚠ MISMATCH: {stage1.warning_message}" if stage1.mismatch_warning else "")
        ),
    ))
    logger.info(
        f"[INGEST {incident_id}] Stage 1 ✓ — Mule={stage1.selected_mule_account} "
        f"({stage1.selected_mule_bank}) MPS={stage1.mps_score:.4f}"
    )

    # ── Stage 2: Drain Time Engine ───────────────────────────────────────────
    try:
        stage2: DrainTimeResult = compute_drain_time(
            terminal_mule=payload.terminal_mule,
            fund_flow=payload.fund_flow,
            current_time=reference_time,
        )
    except Exception as exc:
        stages.append(PipelineStageStatus(stage="STAGE_2_TEMPORAL", status="ERROR", detail=str(exc)))
        logger.error(f"[INGEST {incident_id}] Stage 2 EXCEPTION: {exc}")
        result = IngestResponse(
            synapse_incident_id=incident_id,
            ncrp_ticket_id=payload.ncrp_ticket.ticket_id,
            status="PIPELINE_ERROR",
            mps_score=stage1.mps_score,
            stages=stages,
            simulation_mode=simulation_active,
        )
        _incidents.append(result.model_dump())
        _persist_incidents()
        return result

    drain_tag = "DAILY_LIMIT_EXHAUSTED" if stage2.daily_limit_exhausted else f"{stage2.drain_time_remaining_minutes:.1f} min remaining"
    stages.append(PipelineStageStatus(
        stage="STAGE_2_TEMPORAL",
        status="COMPLETE",
        detail=(
            f"Drainable: ₹{stage2.drainable_today_inr:,.2f} | "
            f"τ={stage2.elapsed_minutes_tau:.2f} min | {drain_tag} | "
            f"Urgency={stage2.urgency_score:.4f}"
        ),
    ))
    logger.info(
        f"[INGEST {incident_id}] Stage 2 ✓ — Drain={stage2.drain_time_remaining_minutes:.1f}min "
        f"Urgency={stage2.urgency_score:.4f} Exhausted={stage2.daily_limit_exhausted}"
    )

    # ── Stage 3: Spatial Ranker ───────────────────────────────────────────────
    if not _ATM_REGISTRY:
        stages.append(PipelineStageStatus(
            stage="STAGE_3_SPATIAL", status="ERROR", detail="ATM registry is empty."
        ))
        result = IngestResponse(
            synapse_incident_id=incident_id,
            ncrp_ticket_id=payload.ncrp_ticket.ticket_id,
            status="PIPELINE_ERROR",
            mps_score=stage1.mps_score,
            stages=stages,
            simulation_mode=simulation_active,
        )
        _incidents.append(result.model_dump())
        _persist_incidents()
        return result

    try:
        stage3: Stage3Result = rank_atms(
            terminal_mule=payload.terminal_mule,
            atm_registry=_ATM_REGISTRY,
            current_time=reference_time,
            victim_district=payload.ncrp_ticket.victim_district,
        )
    except Exception as exc:
        stages.append(PipelineStageStatus(stage="STAGE_3_SPATIAL", status="ERROR", detail=str(exc)))
        logger.error(f"[INGEST {incident_id}] Stage 3 EXCEPTION: {exc}")
        result = IngestResponse(
            synapse_incident_id=incident_id,
            ncrp_ticket_id=payload.ncrp_ticket.ticket_id,
            status="PIPELINE_ERROR",
            mps_score=stage1.mps_score,
            stages=stages,
            simulation_mode=simulation_active,
        )
        _incidents.append(result.model_dump())
        _persist_incidents()
        return result

    top_atm_risk = stage3.top_atms[0].risk_score if stage3.top_atms else 0.0
    stages.append(PipelineStageStatus(
        stage="STAGE_3_SPATIAL",
        status="COMPLETE",
        detail=(
            f"Method={stage3.location_method.value} | "
            f"r_active={stage3.active_search_radius_km:.2f}km | "
            f"Candidates={stage3.total_candidates_found} | "
            f"Top ATM={stage3.top_atms[0].atm_id if stage3.top_atms else 'NONE'} "
            f"(score={top_atm_risk:.4f})"
            + (" | ⚠ IFSC_CAP_APPLIED" if stage3.confidence_cap_applied else "")
        ),
    ))
    logger.info(
        f"[INGEST {incident_id}] Stage 3 ✓ — Method={stage3.location_method.value} "
        f"Candidates={stage3.total_candidates_found} "
        f"Top1={stage3.top_atms[0].atm_id if stage3.top_atms else 'N/A'} ({top_atm_risk:.4f})"
    )

    # ── Confidence Aggregation & Tier Assignment ─────────────────────────────
    confidence = _compute_confidence(
        mps_score=stage1.mps_score,
        urgency_score=stage2.urgency_score,
        top_atm_risk_score=top_atm_risk,
        daily_limit_exhausted=stage2.daily_limit_exhausted,
        confidence_cap_applied=stage3.confidence_cap_applied,
    )
    tier = _assign_tier(confidence)

    stages.append(PipelineStageStatus(
        stage="CONFIDENCE_AGGREGATION",
        status="COMPLETE",
        detail=(
            f"C = γ1({GAMMA_MPS})×MPS({stage1.mps_score:.4f}) + "
            f"γ2({GAMMA_URGENCY})×Urgency({stage2.urgency_score:.4f}) + "
            f"γ3({GAMMA_SPATIAL})×RiskScore({top_atm_risk:.4f}) = {confidence:.4f} → {tier}"
            + (f" [IFSC_CAP @ {IFSC_FALLBACK_CONFIDENCE_CAP}]" if stage3.confidence_cap_applied else "")
        ),
    ))
    logger.info(
        f"[INGEST {incident_id}] Confidence={confidence:.4f} → Tier={tier}"
    )

    # ── Webhook Dispatch ──────────────────────────────────────────────────────
    webhook_dispatched = False
    webhook_status: Optional[str] = None

    if tier in ("PRIMARY_DIGITAL", "SECONDARY_PHYSICAL"):
        try:
            wh_payload = _build_webhook_payload(
                incident_id=incident_id,
                payload=payload,
                stage1=stage1,
                stage2=stage2,
                stage3=stage3,
                confidence=confidence,
                tier=tier,
                reference_time=reference_time,
            )
            logger.info(
                f"[INGEST {incident_id}] Dispatching webhook for tier={tier} "
                f"to {MOCK_WEBHOOK_BASE_URL}{MOCK_WEBHOOK_PATH}"
            )
            wh_result = await _dispatch_webhook_async(wh_payload)
            webhook_dispatched = True
            webhook_status = wh_result

            # ── Auto-seed lien registry for terminal mule on successful webhook ──
            if wh_result == "SUCCESS":
                mule_acct = payload.terminal_mule.mule_account_number
                if mule_acct not in _LIEN_REGISTRY["active_liens"]:
                    _LIEN_REGISTRY["active_liens"][mule_acct] = {
                        "account_number": mule_acct,
                        "bank_name": payload.terminal_mule.mule_bank,
                        "ifsc": payload.terminal_mule.mule_ifsc,
                        "incident_id": payload.ncrp_ticket.ticket_id,
                        "initiated_at": datetime.now(timezone.utc).isoformat(),
                        "source": "PIPELINE",
                        "webhook_status": "SUCCESS",
                    }
                    _persist_lien_registry()
                    logger.info(
                        f"[LIEN] Auto-seeded lien for terminal mule {mule_acct} "
                        f"(pipeline webhook SUCCESS)"
                    )
        except Exception as exc:
            webhook_status = "FAILED"
            logger.error(f"[INGEST {incident_id}] Webhook build/dispatch error: {exc}")

        stages.append(PipelineStageStatus(
            stage="WEBHOOK_DISPATCH",
            status=webhook_status or "UNKNOWN",
            detail=f"Tier={tier} | URL={MOCK_WEBHOOK_BASE_URL}{MOCK_WEBHOOK_PATH}",
        ))
    else:
        webhook_status = "NOT_APPLICABLE"
        stages.append(PipelineStageStatus(
            stage="WEBHOOK_DISPATCH",
            status="NOT_APPLICABLE",
            detail=f"Confidence {confidence:.4f} < {THRESHOLD_PRIMARY_DIGITAL} threshold. Logged only.",
        ))
        logger.info(f"[INGEST {incident_id}] Confidence below intervention threshold — TIER_1_LOG_ONLY.")

    # ── Build & Store Final Response ─────────────────────────────────────────
    top_atms_summary = [
        {
            "rank": atm.risk_rank,
            "atm_id": atm.atm_id,
            "bank_name": atm.bank_name,
            "address": atm.address,
            "lat": atm.lat,
            "lon": atm.lon,
            "distance_km": atm.distance_km,
            "risk_score": atm.risk_score,
            "is_onsite": atm.is_onsite,
            "cash_status": atm.cash_replenishment_status,
        }
        for atm in stage3.top_atms
    ]

    result = IngestResponse(
        synapse_incident_id=incident_id,
        ncrp_ticket_id=payload.ncrp_ticket.ticket_id,
        status="PROCESSED",
        intervention_tier=tier,
        confidence_score=confidence,
        mps_score=stage1.mps_score,
        urgency_score=stage2.urgency_score,
        top_atm_risk_score=top_atm_risk,
        drain_time_remaining_minutes=stage2.drain_time_remaining_minutes,
        drainable_today_inr=stage2.drainable_today_inr,
        mule_location_method=stage3.location_method.value,
        mule_estimated_lat=stage3.estimated_position[0],
        mule_estimated_lon=stage3.estimated_position[1],
        ifsc_fallback_cap_applied=stage3.confidence_cap_applied,
        top_atms=top_atms_summary,
        webhook_dispatched=webhook_dispatched,
        webhook_status=webhook_status,
        stages=stages,
        simulation_mode=simulation_active,
    )
    # Build payload_snapshot — structured subset of original payload for the UI Incident Intelligence panel.
    # complainant_name is Optional in the schema; use getattr with None fallback so legacy payloads work.
    result.payload_snapshot = {
        "ingestion_timestamp": payload.ingestion_timestamp.isoformat(),
        "fraud_type":          payload.ncrp_ticket.fraud_type.value,
        "victim_state":        payload.ncrp_ticket.victim_state,
        "victim_district":     payload.ncrp_ticket.victim_district,
        "complainant_name":    getattr(payload.ncrp_ticket, "complainant_name", None),
        "complaint_timestamp": payload.ncrp_ticket.complaint_timestamp.isoformat(),
        "amount_inr":          payload.ncrp_ticket.amount_inr,
        "source_account": {
            "account_number": payload.ncrp_ticket.source_account.account_number,
            "ifsc":           payload.ncrp_ticket.source_account.ifsc,
            "bank_name":      payload.ncrp_ticket.source_account.bank_name,
        },
        "transactions": [
            {
                "hop_index":       t.hop_index,
                "txn_id":          t.txn_id,
                "txn_timestamp":   t.txn_timestamp.isoformat(),
                "sender_account":  t.sender_account,
                "sender_ifsc":     t.sender_ifsc,
                "sender_bank":     t.sender_bank,
                "receiver_account":t.receiver_account,
                "receiver_ifsc":   t.receiver_ifsc,
                "receiver_bank":   t.receiver_bank,
                "amount_inr":      t.amount_inr,
                "channel":         t.channel.value,
            }
            for t in payload.fund_flow.transactions
        ],
        "terminal_mule": {
            "account_number":        payload.terminal_mule.mule_account_number,
            "ifsc":                  payload.terminal_mule.mule_ifsc,
            "bank":                  payload.terminal_mule.mule_bank,
            "account_type":          payload.terminal_mule.account_type.value,
            "balance_inr":           payload.terminal_mule.current_balance_inr,
            "daily_limit_inr":       payload.terminal_mule.daily_withdrawal_limit_inr,
            "withdrawals_today_inr": payload.terminal_mule.withdrawals_today_inr,
        },
        "ip_cluster": [
            {
                "ip_address": ip.ip_address,
                "asn":        ip.asn,
                "geo_lat":    ip.geo_lat,
                "geo_lon":    ip.geo_lon,
                "last_seen":  ip.last_seen.isoformat(),
            }
            for ip in (payload.terminal_mule.ip_cluster or [])
        ],
    }
    # Phase 10 Feature: Automatic Simulated Withdrawal
    # The scammer naturally withdraws in 10,000 brackets when the window reaches zero.
    import math
    if result.drain_time_remaining_minutes is not None and result.drainable_today_inr is not None and result.drainable_today_inr > 0:
        simulated_amt = math.floor(result.drainable_today_inr / 10000.0) * 10000.0
        if simulated_amt > 0 and result.drain_time_remaining_minutes <= 0.0:
            logger.info(f"[INGEST {incident_id}] Window elapsed on arrival. Simulating auto-withdrawal of ₹{simulated_amt:,.2f}")
            mule_snap = result.payload_snapshot["terminal_mule"]
            mule_snap["withdrawals_today_inr"] += simulated_amt
            mule_snap["balance_inr"] = max(0.0, mule_snap["balance_inr"] - simulated_amt)
            
            new_drainable = max(0.0, min(
                mule_snap["balance_inr"],
                mule_snap["daily_limit_inr"] - mule_snap["withdrawals_today_inr"]
            ))
            result.drainable_today_inr = new_drainable
            
            if new_drainable <= 100:
                for s in result.stages:
                    if s.stage == "STAGE_2_TEMPORAL":
                        if mule_snap["withdrawals_today_inr"] >= mule_snap["daily_limit_inr"]:
                            s.detail += " | DAILY_LIMIT_EXHAUSTED"
                        else:
                            s.detail += " | BALANCE_EXHAUSTED"

    _incidents.append(result.model_dump())
    _persist_incidents()

    logger.info(
        f"[INGEST {incident_id}] ✅ COMPLETE — "
        f"ncrp={payload.ncrp_ticket.ticket_id} | tier={tier} | C={confidence:.4f} | "
        f"webhook={webhook_status}"
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/freeze-card-atm — Mock Bank Webhook Receiver
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/freeze-card-atm",
    summary="Mock Bank Switch Webhook Receiver",
    tags=["Webhook"],
)
async def mock_freeze_card_atm(request: Request) -> JSONResponse:
    """
    Mock implementation of the bank card management switch webhook endpoint.
    Logs the full incoming freeze request payload to stdout for demo visibility
    and returns HTTP 200 OK with an acknowledgement body.

    In production, this endpoint would be hosted by the participating bank's
    card management system, not by Synapse itself.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    incident_id = body.get("synapse_incident_id", "UNKNOWN")
    ncrp_id = body.get("ncrp_ticket_id", "UNKNOWN")
    confidence = body.get("confidence_score", "N/A")
    tier = body.get("intervention_tier", "N/A")
    atm_count = len(body.get("atm_blocks", []))

    logger.info(
        f"[MOCK WEBHOOK RECEIVER] ✅ Received freeze request:\n"
        f"  incident_id   = {incident_id}\n"
        f"  ncrp_ticket   = {ncrp_id}\n"
        f"  confidence    = {confidence}\n"
        f"  tier          = {tier}\n"
        f"  atm_blocks    = {atm_count} ATM(s) targeted\n"
        f"  raw_payload   = {json.dumps(body, indent=2, default=str)}"
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ACKNOWLEDGED",
            "message": "Card hold request received and queued for processing.",
            "synapse_incident_id": incident_id,
            "ncrp_ticket_id": ncrp_id,
            "ack_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/mock/ncrp-intimation — Mock NCRP Server Intimation
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/mock/ncrp-intimation",
    summary="Mock NCRP Intimation Webhook",
    tags=["Webhook"],
)
async def mock_ncrp_intimation(request: Request) -> JSONResponse:
    """
    Mock endpoint simulating the external NCRP server.
    Receives final case resolution details.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    
    logger.info(
        f"[MOCK NCRP] 🚔 Received Case Resolution Intimation:\n"
        f"{json.dumps(body, indent=2, default=str)}"
    )
    
    return JSONResponse(status_code=200, content={"status": "ACKNOWLEDGED"})


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/incidents — Incident Log for UI
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/api/v1/incidents",
    summary="Retrieve All Processed Incidents",
    tags=["UI Support"],
)
async def get_incidents() -> JSONResponse:
    """
    Returns the in-memory list of all processed incidents in reverse chronological order.
    Consumed by the View A (Strategic Command) and View B (Tactical Interception) dashboard.
    """
    return JSONResponse(
        content={
            "total": len(_incidents),
            "incidents": list(reversed(_incidents)),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/atm-registry — ATM Registry for UI
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/api/v1/atm-registry",
    summary="Retrieve Internal ATM Registry",
    tags=["UI Support"],
)
async def get_atm_registry() -> JSONResponse:
    """
    Returns the loaded internal ATM registry for inspection.
    Consumed by the admin panel and map visualization components in the dashboard.
    """
    return JSONResponse(
        content={
            "total": len(_ATM_REGISTRY),
            "atms": _ATM_REGISTRY,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/webhook-callback/{request_id} — Placeholder callback receiver
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/webhook-callback/{request_id}",
    summary="Bank Webhook Callback Receiver (Placeholder)",
    tags=["Webhook"],
)
async def webhook_callback(request_id: str, request: Request) -> JSONResponse:
    """
    Placeholder endpoint where banks POST hold confirmation/rejection callbacks.
    Logs the callback and returns 200 OK.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    logger.info(f"[WEBHOOK CALLBACK] request_id={request_id} body={json.dumps(body, default=str)}")
    return JSONResponse(
        status_code=200,
        content={"status": "CALLBACK_RECEIVED", "request_id": request_id},
    )



# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/health — Health Check
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
@app.get("/api/v1/health", tags=["System"])
async def health_check() -> JSONResponse:
    """Quick health check — returns server status and ATM registry size."""
    return JSONResponse(
        content={
            "status": "OK",
            "atm_registry_loaded": len(_ATM_REGISTRY),
            "incidents_processed": len(_incidents),
            "server_time_utc": datetime.now(timezone.utc).isoformat(),
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/incidents/{ncrp_ticket_id}/resolve — Mark Case Resolved
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/incidents/{ncrp_ticket_id}/resolve",
    summary="Mark an Incident as Resolved",
    tags=["Case Management"],
)
async def resolve_incident(ncrp_ticket_id: str, payload: ResolveIncidentRequest) -> JSONResponse:
    """
    Marks a case as resolved. Accepted body:
        {
            "operator_id": "ID of the operator resolving the case",
            "reason": "FUNDS_FROZEN" | "MULE_APPREHENDED" | "FUNDS_RECOVERED" |
                      "WINDOW_ELAPSED_CASE_CLOSED" | "FALSE_POSITIVE",
            "note": "optional free-text note"
        }
    The resolved flag is returned in GET /api/v1/incidents so both portals
    can filter and display the resolved section.
    """
    reason = payload.reason
    note   = payload.note
    operator_id = payload.operator_id

    target = None
    for inc in _incidents:
        if inc.get("ncrp_ticket_id") == ncrp_ticket_id:
            target = inc
            break

    if target is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Incident '{ncrp_ticket_id}' not found in active session."},
        )

    # Idempotency guard — reject a second resolve without an explicit /unresolve first.
    # Without this, two resolve calls could overwrite each other's reason/operator_id
    # and fire the NCRP intimation webhook twice with conflicting outcomes.
    if target.get("resolved") is True:
        return JSONResponse(
            status_code=409,
            content={
                "error": f"Incident '{ncrp_ticket_id}' is already resolved. "
                         f"Call /unresolve first if you need to change the resolution."
            },
        )

    ts = datetime.now(timezone.utc).isoformat()
    target["resolved"]           = True
    target["resolved_at"]        = ts
    target["resolution_reason"]  = reason
    target["resolution_note"]    = note
    target["operator_id"]        = operator_id

    _persist_incidents()

    logger.info(
        f"[RESOLVE] ncrp={ncrp_ticket_id} | operator={operator_id} | reason={reason} | note='{note}' | at={ts}"
    )

    # ── Fire outbound webhook to NCRP ────────────────────────────────────
    webhook_payload = {
        "ncrp_ticket_id": ncrp_ticket_id,
        "status": "RESOLVED",
        "operator_id": operator_id,
        "resolution_reason": reason,
        "resolution_note": note,
        "resolved_at": ts
    }
    target_url = f"{MOCK_WEBHOOK_BASE_URL}/api/v1/mock/ncrp-intimation"
    try:
        if _WEBHOOK_HTTP_CLIENT is not None:
            _WEBHOOK_HTTP_CLIENT.post("/api/v1/mock/ncrp-intimation", json=webhook_payload)
        else:
            async with httpx.AsyncClient(timeout=5.0) as http:
                await http.post(target_url, json=webhook_payload)
    except Exception as exc:
        logger.error(f"[RESOLVE] Failed to dispatch NCRP intimation for {ncrp_ticket_id}: {exc}")

    return JSONResponse(
        status_code=200,
        content={
            "status": "RESOLVED",
            "ncrp_ticket_id": ncrp_ticket_id,
            "resolution_reason": reason,
            "resolution_note": note,
            "operator_id": operator_id,
            "resolved_at": ts,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/incidents/{ncrp_ticket_id}/unresolve — Undo Resolution (safety valve)
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/incidents/{ncrp_ticket_id}/unresolve",
    summary="Reopen a Resolved Incident",
    tags=["Case Management"],
)
async def unresolve_incident(ncrp_ticket_id: str) -> JSONResponse:
    """Safety valve — reopens a previously resolved incident."""
    for inc in _incidents:
        if inc.get("ncrp_ticket_id") == ncrp_ticket_id:
            inc.pop("resolved", None)
            inc.pop("resolved_at", None)
            inc.pop("resolution_reason", None)
            inc.pop("resolution_note", None)
            inc.pop("operator_id", None)
            _persist_incidents()
            logger.info(f"[UNRESOLVE] ncrp={ncrp_ticket_id}")
            return JSONResponse(status_code=200, content={"status": "REOPENED", "ncrp_ticket_id": ncrp_ticket_id})
    return JSONResponse(status_code=404, content={"error": f"Incident '{ncrp_ticket_id}' not found."})


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /api/v1/incidents/{ncrp_ticket_id}/live-update — Bank Feed Live Update
# ─────────────────────────────────────────────────────────────────────────────
@app.patch(
    "/api/v1/incidents/{ncrp_ticket_id}/live-update",
    summary="Push a Live Withdrawal Update to an Incident (Bank Feed Simulator)",
    tags=["Bank Feed"],
)
async def live_update_incident(ncrp_ticket_id: str, request: Request) -> JSONResponse:
    """
    Simulates a real-time bank data push: adds a withdrawal amount to the terminal
    mule's account in the incident's payload_snapshot.

    Accepted body (all fields optional):
        {
            "additional_withdrawal_inr": 10000,
            "note": "ATM withdrawal observed at 10:00 AM"
        }

    Updates in payload_snapshot.terminal_mule:
      - withdrawals_today_inr  += additional_withdrawal_inr
      - balance_inr            -= additional_withdrawal_inr (floor 0)
      - drainable_today_inr    = max(0, min(balance_inr, daily_limit - withdrawn_today))

    The Synapse dashboard polls GET /api/v1/incidents every 5 seconds and will
    reflect the change automatically.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    additional_withdrawal = float(body.get("additional_withdrawal_inr", 0.0))
    note = body.get("note", "")

    # Find incident in-memory
    target = None
    for inc in _incidents:
        if inc.get("ncrp_ticket_id") == ncrp_ticket_id:
            target = inc
            break

    if target is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Incident '{ncrp_ticket_id}' not found in active session."},
        )

    snap = target.get("payload_snapshot")
    if snap is None:
        return JSONResponse(
            status_code=422,
            content={"error": "Incident has no payload_snapshot — ingested before Phase 10. Re-submit payload."},
        )

    mule = snap.get("terminal_mule", {})

    # Apply withdrawal
    prev_withdrawn = float(mule.get("withdrawals_today_inr", 0.0))
    prev_balance   = float(mule.get("balance_inr", 0.0))
    daily_limit    = float(mule.get("daily_limit_inr", 100000.0))

    new_withdrawn = prev_withdrawn + additional_withdrawal
    new_balance   = max(0.0, prev_balance - additional_withdrawal)
    new_drainable = max(0.0, min(new_balance, daily_limit - new_withdrawn))

    mule["withdrawals_today_inr"] = round(new_withdrawn, 2)
    mule["balance_inr"]           = round(new_balance, 2)
    snap["terminal_mule"]         = mule

    # Also patch the top-level drainable_today_inr used by the confidence/urgency display
    target["drainable_today_inr"] = round(new_drainable, 2)

    ts = datetime.now(timezone.utc).isoformat()

    _persist_incidents()

    logger.info(
        f"[LIVE-UPDATE] ncrp={ncrp_ticket_id} | "
        f"+withdrawal=₹{additional_withdrawal:,.2f} | "
        f"withdrawn_total=₹{new_withdrawn:,.2f} | "
        f"balance_remaining=₹{new_balance:,.2f} | "
        f"note='{note}'"
    )

    return JSONResponse(
        status_code=200,
        content={
            "status": "UPDATED",
            "ncrp_ticket_id": ncrp_ticket_id,
            "applied_at_utc": ts,
            "note": note,
            "terminal_mule": {
                "withdrawals_today_inr": round(new_withdrawn, 2),
                "balance_inr":           round(new_balance, 2),
                "drainable_today_inr":   round(new_drainable, 2),
                "daily_limit_inr":       daily_limit,
            },
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/liens — Retrieve Digital Lien Registry
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/api/v1/liens",
    summary="Retrieve Digital Lien Registry",
    tags=["Lien Management"],
)
async def get_liens() -> JSONResponse:
    """
    Returns the full lien registry: active liens (keyed by account number) and
    the append-only revocation log. The UI fetches this on page load and after
    every lien action to render correct button states.
    """
    return JSONResponse(content=_LIEN_REGISTRY)


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/liens — Initiate or Revoke a Digital Lien
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/liens",
    summary="Initiate or Revoke a Digital Lien",
    tags=["Lien Management"],
)
async def manage_lien(payload: LienActionRequest) -> JSONResponse:
    """
    Manages per-account digital liens.

    Actions:
        INITIATE — Dispatches a freeze webhook to /api/v1/freeze-card-atm for the
                   specified account, then records the lien in the persistent registry.
        REVOKE   — Removes an active lien and appends to the revocation log.
                   Requires a mandatory 'reason' and 'operator_id' field.

    Body (INITIATE):
        {
            "action": "INITIATE",
            "account_number": "50100287654321",
            "operator_id": "Officer Name",
            "reason": "Investigating suspicious transfers",
            "bank_name": "HDFC Bank",
            "ifsc": "HDFC0001729",
            "incident_id": "NCRP-2026-0045781"
        }

    Body (REVOKE):
        {
            "action": "REVOKE",
            "account_number": "50100287654321",
            "operator_id": "Officer Name",
            "reason": "Account verified as non-mule by branch manager"
        }
    """
    action = payload.action.upper()
    account_number = payload.account_number.strip()
    operator_id = payload.operator_id
    reason = payload.reason

    if not account_number:
        return JSONResponse(status_code=400, content={"error": "account_number is required."})

    if action == "INITIATE":
        # _LIEN_LOCK prevents a race where two concurrent INITIATE requests for the
        # same account both pass the duplicate check before either has written.
        async with _LIEN_LOCK:
            # ── Check for duplicate ──────────────────────────────────────────────
            if account_number in _LIEN_REGISTRY["active_liens"]:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": f"Account {account_number} already has an active lien.",
                        "existing_lien": _LIEN_REGISTRY["active_liens"][account_number],
                    },
                )

            bank_name = payload.bank_name or "Unknown Bank"
            ifsc = payload.ifsc or "UNKNOWN"
            incident_id = payload.incident_id or "MANUAL"

            # ── Build and dispatch freeze webhook ────────────────────────────────
            # Construct a lightweight webhook payload for this specific account.
            ts_now = datetime.now(timezone.utc)
            webhook_body = {
                "webhook_version": "1.1.0",
                "request_id": str(uuid.uuid4()),
                "synapse_incident_id": incident_id,
                "ncrp_ticket_id": incident_id,
                "request_timestamp": ts_now.isoformat(),
                "requesting_authority": {
                    "authority_name": "Indian Cyber Crime Coordination Centre (I4C), MHA",
                    "authority_code": "I4C-MHA",
                    "authorized_officer_id": "I4C-HANDLER-MANUAL",
                },
                "golden_hour_expiry": (ts_now + timedelta(minutes=120)).isoformat(),
                "confidence_score": 1.0,
                "intervention_tier": "PRIMARY_DIGITAL",
                "card_hold": {
                    "card_number_hash": "0" * 64,
                    "hold_type": "ATM_WITHDRAWAL_BLOCK",
                    "hold_duration_minutes": 120,
                    "mule_account_number": account_number,
                    "mule_ifsc": ifsc,
                },
                "atm_blocks": [],
                "justification": {
                    "drain_time_remaining_minutes": 0,
                    "drainable_today_inr": 0,
                    "fund_flow_depth": 0,
                    "total_amount_inr": 0,
                    "mule_location_method": "MANUAL_LIEN",
                },
                "callback_url": f"{MOCK_WEBHOOK_BASE_URL}/api/v1/webhook-callback/{incident_id}",
            }

            target_url = f"{MOCK_WEBHOOK_BASE_URL}{MOCK_WEBHOOK_PATH}"
            wh_status = "FAILED"
            try:
                if _WEBHOOK_HTTP_CLIENT is not None:
                    resp = _WEBHOOK_HTTP_CLIENT.post(MOCK_WEBHOOK_PATH, json=webhook_body)
                else:
                    async with httpx.AsyncClient(timeout=10.0) as http:
                        resp = await http.post(target_url, json=webhook_body)
                if resp.status_code == 200:
                    wh_status = "SUCCESS"
                logger.info(
                    f"[LIEN] Manual webhook dispatched for {account_number} → "
                    f"HTTP {resp.status_code} ({wh_status})"
                )
            except Exception as exc:
                logger.error(f"[LIEN] Manual webhook dispatch FAILED for {account_number}: {exc}")

            # ── Record in registry ───────────────────────────────────────────────
            _LIEN_REGISTRY["active_liens"][account_number] = {
                "account_number": account_number,
                "bank_name": bank_name,
                "ifsc": ifsc,
                "incident_id": incident_id,
                "initiated_at": ts_now.isoformat(),
                "source": "MANUAL",
                "webhook_status": wh_status,
                "operator_id": operator_id,
                "reason": reason,
            }
            _persist_lien_registry()

            logger.info(
                f"[LIEN] INITIATED for {account_number} ({bank_name}) by {operator_id} | "
                f"reason='{reason}' | incident={incident_id} | webhook={wh_status}"
            )

            return JSONResponse(
                status_code=201,
                content={
                    "status": "LIEN_INITIATED",
                    "account_number": account_number,
                    "webhook_status": wh_status,
                    "lien": _LIEN_REGISTRY["active_liens"][account_number],
                },
            )

    elif action == "REVOKE":
        # ── Validate active lien exists ──────────────────────────────────────
        if account_number not in _LIEN_REGISTRY["active_liens"]:
            return JSONResponse(
                status_code=404,
                content={"error": f"No active lien found for account {account_number}."},
            )

        if not reason:
            return JSONResponse(
                status_code=400,
                content={"error": "A 'reason' is mandatory when revoking a digital lien."},
            )

        # ── Move from active to revocation log ───────────────────────────────
        existing_lien = _LIEN_REGISTRY["active_liens"].pop(account_number)
        revocation_entry = {
            "account_number": account_number,
            "bank_name": existing_lien.get("bank_name", "Unknown"),
            "ifsc": existing_lien.get("ifsc", ""),
            "incident_id": existing_lien.get("incident_id", ""),
            "was_source": existing_lien.get("source", "UNKNOWN"),
            "original_initiated_at": existing_lien.get("initiated_at", ""),
            "revoked_at": datetime.now(timezone.utc).isoformat(),
            "revoked_by": operator_id,
            "reason": reason,
        }
        _LIEN_REGISTRY["revocation_log"].append(revocation_entry)
        _persist_lien_registry()

        logger.info(
            f"[LIEN] REVOKED for {account_number} by {operator_id} | reason='{reason}' | "
            f"was_source={existing_lien.get('source')}"
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "LIEN_REVOKED",
                "account_number": account_number,
                "reason": reason,
                "revocation": revocation_entry,
            },
        )

    else:
        return JSONResponse(
            status_code=400,
            content={"error": f"Unknown action '{action}'. Use 'INITIATE' or 'REVOKE'."},
        )



# ─────────────────────────────────────────────────────────────────────────────
# GET /api/v1/dispatch-registry — Retrieve Dispatch Registry
# ─────────────────────────────────────────────────────────────────────────────
@app.get(
    "/api/v1/dispatch-registry",
    summary="Retrieve Dispatch Registry",
    description="Returns the current state of all acknowledged ATM crew dispatches."
)
async def get_dispatch_registry() -> Dict[str, Any]:
    return _DISPATCH_REGISTRY

# ─────────────────────────────────────────────────────────────────────────────
# POST /api/v1/dispatch — Acknowledge & Dispatch Crew
# ─────────────────────────────────────────────────────────────────────────────
@app.post(
    "/api/v1/dispatch",
    summary="Acknowledge and Dispatch Crew",
    description="Records a crew dispatch for a specific incident and ATM."
)
async def dispatch_crew(body: DispatchRequest) -> JSONResponse:
    disp_key = f"{body.ncrp_ticket_id}:{body.atm_id}"
    
    if disp_key in _DISPATCH_REGISTRY:
        return JSONResponse(
            status_code=200,
            content={
                "status": "ALREADY_DISPATCHED",
                "dispatch": _DISPATCH_REGISTRY[disp_key]
            }
        )
        
    dispatch_entry = {
        "rank": body.rank,
        "atmId": body.atm_id,
        "ncrpId": body.ncrp_ticket_id,
        "dispatchedAt": datetime.now(timezone.utc).isoformat()
    }
    
    _DISPATCH_REGISTRY[disp_key] = dispatch_entry
    _persist_dispatch_registry()
    
    logger.info(f"[DISPATCH] ✓ Crew sent to Rank #{body.rank} ({body.atm_id}) for incident {body.ncrp_ticket_id}")
    
    return JSONResponse(
        status_code=201,
        content={
            "status": "DISPATCHED",
            "dispatch": dispatch_entry
        }
    )

