# PROJECT_STATE.md — Project Synapse

> **Protocol:** This file is the single source of truth for project progress. Updated per `instructions.md` §3 Verification Gate.  
> **PRD Version:** v2.0.0 (Phase 12 complete — Crew Dispatch Migration)  
> **ASSUMPTIONS Version:** 45 entries (1 added — Ingestion Rejection on Non-Viable Terminal Mule)  
> **Last Updated:** 08 September 2026 — NO_VIABLE_ATM_MULE HTTP 422 Rejection implemented & verified

---

## Current Status

| **Current Phase** | Phase 13 — Production Hardening & Audit Fixes |
| **Last Completed Feature** | Module 25 — Ingestion Rejection on Non-Viable Terminal Mule (NO_VIABLE_ATM_MULE) (`Code Complete (Unverified)`) |
| **Active Task** | User Sign-off for Module 25 (`NO_VIABLE_ATM_MULE` HTTP 422 Rejection). |
| **Immediate Next Task** | Transition Module 25 to `Verified & Approved` upon user confirmation. |
| **Known Blockers / Warnings** | None. All test suites verified passing (100%). |

---

## Module Completion Matrix

Each module maps to a PRD v1.3.0 requirement. Status transitions follow `instructions.md` §3:  
`Not Started` → `In Progress` → `Code Complete (Unverified)` → `Verified & Approved` with date.

No module may reach `Verified & Approved` without a passing verification script, terminal output proof, and explicit user sign-off.

---

### Module 1: Ingestion & Webhook Schemas

| Field | Value |
|---|---|
| **File** | `/api/schemas.py` |
| **PRD Reference** | §3.1 Ingestion Payload Schema (v1.1), §3.2 Bank Webhook Payload Schema (v1.1), §1.3 Golden Hour (v1.2/v1.3), §4.5 Confidence Aggregation (v1.3) |
| **Description** | Pydantic v2 models for `Incident_Payload` (NCRP ticket, fund flow, terminal mule) and `FreezeCardATM` webhook payload. Includes all field validations: IFSC regex, PIN code regex, card hash pattern, lat/lon bounds (6–37°N, 68–98°E), enum constraints, and optional `cell_tower_cluster` (`min_length=0`). Dual-gate Golden Hour validation logic. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_schemas_and_registry.py` |

**Critical v1.2/v1.3 requirements for this module:**

- `[v1.2 FIX 3D]` **Gate 1** must compute `T_latest = max(t.txn_timestamp for t in fund_flow.transactions)` — do NOT use `transactions[-1]`. Array order is not guaranteed to reflect temporal order in branched/fan-out flows.
- `[v1.2 FIX 3D]` **Gate 2** uses `complaint_timestamp` as before.
- Must include `account_type` enum (`SAVINGS`, `BASIC_SAVINGS_BD`, `CURRENT`, `UNKNOWN`), nullable `linked_card_number_hash`, `daily_withdrawal_limit_inr` (default 100000), `withdrawals_today_inr` (default 0) per v1.1 FIX 1B/3B.
- Webhook `justification` must include `drainable_today_inr` and `drain_time_remaining_minutes` (which reflects **remaining** time after τ subtraction per v1.3 FIX 4B).

---

### Module 2: Internal ATM Registry

| Field | Value |
|---|---|
| **File** | `/data/atm_registry.json` |
| **PRD Reference** | §5.1 ADM-09 (v1.2 clarified), §4.4 Step 3b, §6.3.3 |
| **Description** | Static JSON file containing 200 synthetic ATM records across 3 cities (Pune, Bengaluru, Delhi). Each record contains: `atm_id`, `bank_name`, `address`, `pin_code`, `lat`, `lon`, `is_onsite`, `daily_avg_txn_count`, `cash_replenishment_status`. Loaded into memory at server startup. Queried via haversine radius in Stage 3b. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_schemas_and_registry.py` |

**Critical v1.3 requirements for this module:**

- `[v1.3 FIX 4D]` **CNRB-ATM-PNE-0042** (Canara Bank, FC Road, Shivaji Nagar, Pune) **MUST** be `is_onsite: false`. An onsite ATM's theoretical max RiskScore is 0.85 (β₄ contribution = 0), making the PRD's mocked score of 0.91 mathematically impossible if onsite. Offsite ceiling is 1.0.
- ATM IDs follow `{BANK_CODE}-ATM-{CITY_CODE}-{SEQUENTIAL}` pattern. Coordinates must fall within India bounds.
- `daily_avg_txn_count` drawn from log-normal (μ=5.0, σ=0.8). Cash status distribution: FULL 60%, PARTIAL 25%, LOW 10%, EMPTY 5%.
- Must include Canara Bank, SBI, HDFC, PNB, ICICI, Bank of Baroda, Axis Bank ATMs.
- `[v1.2 FIX 2-clarify]` MVP uses in-memory brute-force scan (200 ATMs, < 10ms). Production target (50K ATMs, ≤ 100ms) requires vectorized NumPy haversine or PostGIS — not in MVP scope.

---

### Module 3: Synthetic Data Generator

| Field | Value |
|---|---|
| **File** | `/synthetic/generator.py` |
| **PRD Reference** | §6 Synthetic Data Generation Strategy, §3.3 JSON Examples (v1.3 corrected) |
| **Description** | Python script that generates valid `Incident_Payload.json` files conforming to the v1.1 schema. Produces randomized but realistic payloads with: valid IFSC codes (regex-conformant), valid PIN codes, fund flows of 2–7 hops with decreasing amounts, IP geolocation clusters within Indian bounds, empty `cell_tower_cluster` (MVP default), correct `account_type` and `linked_card_number_hash`, and timestamps within Golden Hour window. Must generate the 3 demo payloads (Pune, Bengaluru, Delhi). |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_generator.py`. All 3 demo payloads (`payload_pune.json`, `payload_bengaluru.json`, `payload_delhi.json`) created and validated against Golden Hour dual-gate and Pydantic schemas. |

**Critical v1.3 requirements for this module:**

- `[v1.3 FIX 4A]` **`complaint_timestamp` MUST be AFTER the last hop's `txn_timestamp`**. The v1.0-v1.2 Pune example had complaint_timestamp 39 seconds *before* the first fraud transaction — a temporal paradox. Recommended: complaint = last hop + 1–5 minutes (victim notices, calls 1930).
- Transaction timestamps must be sequential (each hop after the previous).
- `amount_inr` should decrease slightly per hop (mule keeps a cut).
- `daily_withdrawal_limit_inr` should vary by bank (SBI/PNB/BoB: ₹1,00,000; HDFC/ICICI/Axis: ₹2,00,000).
- Generated payloads must pass the Pydantic schema validation from Module 1.
- Generated payloads must pass both Golden Hour gates: `max(txn_timestamp)` within 120 min of NOW, `complaint_timestamp` within 240 min of NOW.

---

### Module 4: Stage 1 — Graph DAG & Viability Filter

| Field | Value |
|---|---|
| **File** | `/core/graph.py` |
| **PRD Reference** | §4.2 Terminal Mule Isolation (v1.2 MPS formula) |
| **Description** | NetworkX DiGraph construction from `fund_flow.transactions`. Leaf node identification (out-degree 0). Mule Probability Score (MPS) ranking for fan-out cases. Cross-reference validation against `terminal_mule.mule_account_number` with `MULE_MISMATCH_WARNING`. Mule viability filter. Returns terminal mule node or `NO_VIABLE_ATM_MULE` status. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_graph.py`. Tested with 3 demo payloads, [v1.2 FIX 1D] bounded exponential decay, fan-out branching, mismatch warning, and 3-point viability filter. |

**Critical v1.2 requirements for this module:**

- `[v1.2 FIX 1D]` **MPS recency term uses bounded exponential decay**, NOT the v1.1 reciprocal:

  ```
  MPS(v) = w1 × (A_v / A_max) + w2 × exp(-μ × (T_now - T_v)) + w3 × 𝟙[match]
  ```

  Where `μ = 0.1 min⁻¹` (half-life ≈ 7 min). All three components map to [0, 1]. **Do NOT use `1/max(Δt, ε)` — that was v1.1 and is SUPERSEDED.** The reciprocal spikes to 60.0 at Δt≈0, making w3 (the intended dominant weight) meaningless.
- Weights: `w1=0.30` (amount ratio), `w2=0.30` (recency), `w3=0.40` (CFCFRMS match).
- **Viability filter** checks: (1) `linked_card_number_hash IS NOT NULL`, (2) `account_type IN ('SAVINGS', 'BASIC_SAVINGS_BD', 'UNKNOWN')`, (3) `mule_bank NOT IN NON_ATM_BANKS`.
- NON_ATM_BANKS: Paytm Payments Bank, Fino Payments Bank, Airtel Payments Bank, Jio Payments Bank, India Post Payments Bank.
- Must handle: single-hop flows, fan-out at terminal layer, mismatch between DAG-derived and payload-declared mule.

---

### Module 5: Stage 2 — Capped Drain Time Engine

| Field | Value |
|---|---|
| **File** | `/core/temporal.py` |
| **PRD Reference** | §4.3 Drain Time Regression (v1.3 formula) |
| **Description** | Computes `B_accessible = min(B, W_limit − W_today)`. If `B_accessible ≤ 0`, returns `drain_time=0` with `DAILY_LIMIT_EXHAUSTED` flag. Otherwise computes remaining drain time with τ subtraction. Returns `drain_time_remaining_minutes` (float) and `drainable_today_inr` (float). |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_temporal.py`. Exact PRD worked example match (18.6 min), [v1.3 FIX 4B] elapsed time τ subtraction, and [v1.3 FIX 4C] urgency guard. |

**Critical v1.3 requirements for this module:**

- `[v1.3 FIX 4B]` **Baseline formula subtracts elapsed time τ:**

  ```
  D̂ = max(0.0, ceil(B_accessible / W_txn) × Δt_mean − τ)
  ```

  Where `τ = T_now − T_last_txn` (minutes since mule received funds). The old formula (`N_w × Δt_mean`) computed total session duration, not remaining time. **Do NOT omit τ.**
- Defaults: `W_txn = 20000`, `Δt_mean = 4.5 min`.
- `[v1.3 FIX 4C]` When `DAILY_LIMIT_EXHAUSTED`, the `drain_time=0` output must be paired with a flag that the urgency component in §4.5 reads. The urgency term must evaluate to **0.0** (not 1.0) for these cases. See Module 7.
- **Validation target:** B=241350, W_limit=100000, W_today=0, τ=3.88 min → B_accessible=100000, N_w=5, total=22.5, D̂=22.5−3.88 = **18.6 min** (the PRD worked example).
- Must handle `B_accessible=0` gracefully (no division errors, returns 0 with flag).

---

### Module 6: Stage 3 — Haversine Spatial Ranker

| Field | Value |
|---|---|
| **File** | `/core/cluster.py` |
| **PRD Reference** | §4.4 ATM Identification & Ranking (v1.3 D_norm formula) |
| **Description** | Three sub-steps: (3a) Mule position estimation via priority cascade. (3b) Haversine radius query against in-memory ATM registry. (3c) Multi-factor ATM risk scoring with five components. Returns Top 3 ATMs sorted by descending RiskScore. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_cluster.py`. Validated with haversine radius query, [v1.3 FIX 4E] active radius proximity normalization, 5-component risk scoring, zero-traffic division guard, and static IFSC fallback. Pune demo correctly ranks `CNRB-ATM-PNE-0042` as Rank 1. |

**Critical v1.3 requirements for this module:**

- `[v1.3 FIX 4E]` **Proximity D_norm must use r_active (the actual search radius), NOT base r_search:**

  ```
  D_norm = max(0.0, 1 - d(P, a_j) / r_active)
  ```

  Where `r_active ∈ {r_search, 1.5×r_search, 2.25×r_search}` depending on whether fallback expansion was triggered. **Do NOT use base `r_search` as denominator** — an ATM at 6.2 km found at r_active=7.5 km would score `1 − 6.2/5.0 = −0.24` (negative, corrupts RiskScore).
- The `max(0.0, ...)` clamp is **defense-in-depth**, not optional.
- **Position estimation cascade:** (1) COMBINED cell+IP, (2) CELL_TOWER only, (3) IP_GEOLOCATION only, (4) IFSC_BRANCH_FALLBACK.
- IP centroid: simple mean. Cell tower: signal-strength + temporal-decay weighting (λ=0.05). Blend α=0.7.
- Search radius: 5 km urban, 15 km rural. Expansion: 1.5× up to 2 times if < 3 ATMs found. **Track r_active and pass to scoring.**
- RiskScore weights: β1=0.30 proximity, β2=0.25 bank match, β3=0.20 cash status, β4=0.15 offsite, β5=0.10 traffic (log-normalized).
- Cash status: FULL=1.0, PARTIAL=0.7, LOW=0.3, EMPTY=0.0, UNKNOWN=0.5.
- Haversine: radians, Earth radius = 6371 km.
- Must handle: empty cell_tower_cluster → skip to IP, empty ip_cluster → skip to IFSC, < 3 ATMs → expand, 0 ATMs after max expansion → empty list with warning.

---

### Module 7: FastAPI Core & Mock Webhook

| Field | Value |
|---|---|
| **File** | `/api/main.py` |
| **PRD Reference** | §5.1 ADM-01 through ADM-09, §5.2 ML-01 through ML-05, §4.5 Confidence Aggregation (v1.3) |
| **Description** | FastAPI application with endpoints for ingestion, mock webhook, incident retrieval, and ATM registry. Orchestrates the full pipeline and computes composite confidence with tiered intervention. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_api.py` (26/26 tests pass). Simulation Mode (`X-Simulation-Mode: true` header or `?simulate=true`) allows static demo payloads to pass Gate 1. Injectable webhook client (`_set_webhook_client`) enables in-process retry testing without a live server. |

**Critical v1.3 requirements for this module:**

- `[v1.3 FIX 4C]` **Urgency guard in confidence aggregation is CRITICAL:**

  ```python
  if daily_limit_exhausted:
      urgency = 0.0  # NOT (1 - 0/120) = 1.0
  else:
      urgency = max(0.0, 1 - drain_time / 120)
  ```

  Without this guard, `DAILY_LIMIT_EXHAUSTED` incidents (D̂=0) get maximum urgency (1.0), injecting γ₂ × 1.0 = 0.35 into the composite score — potentially dispatching officers to intercept a mule who **cannot withdraw money**. This is the most dangerous logic bug in the entire pipeline.

- Confidence: `C = γ1(0.20) × MPS_norm + γ2(0.35) × Urgency + γ3(0.45) × RiskScore_top1`.
- IFSC fallback caps composite at 0.75.
- Intervention tiers: C < 0.70 → log only, 0.70 ≤ C < 0.85 → PRIMARY_DIGITAL (webhook), C ≥ 0.85 → SECONDARY_PHYSICAL (webhook + tactical dispatch).
- Loads ATM registry from `/data/atm_registry.json` at startup.
- All pipeline stages run synchronously for MVP (no task queue).
- Must return structured error responses for: schema validation failure, Golden Hour gate failure (which gate + time delta), viability filter failure.
- Webhook: exponential backoff, max 3 retries, base 5s. Log full payload to stdout for demo visibility.

**Endpoints:**
- `POST /api/v1/ingest` — accepts Incident_Payload, runs dual-gate → Stage 1 → Stage 2 → Stage 3 → confidence → webhook
- `POST /api/v1/freeze-card-atm` — mock bank webhook (logs received payload, returns 200 OK)
- `GET /api/v1/incidents` — returns all processed incidents for UI
- `GET /api/v1/atm-registry` — returns loaded ATM registry

---

### Module 8: Barebones Verification Interface

| Field | Value |
|---|---|
| **File** | `/ui/index.html` |
| **PRD Reference** | §5.3 View A (Strategic Command), §5.4 View B (Tactical Interception), §8.2 Demo Script (v1.3) |
| **Description** | Single-page HTML/JS interface (no build tooling required) with three sections: Admin panel, Strategic view, Tactical view. Frontend teammate will restyle; priority is functional data flow. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 05 September 2026 |
| **Notes** | Verified with `/tests/verify_ui_smoke.py` (35/35 tests pass). Single-page Tailwind + Leaflet frontend served directly via FastAPI (`/` → `ui/index.html`), coordinate fields mapped, live countdown timer active, demo static mount verified. |

**Critical v1.3 requirements for this module:**

- `[v1.3 FIX 4A]` Demo flow dual-gate narration must say *"Complaint filed 3 minutes ago"* (not 17 — complaint_timestamp is now 01:19:15, ingestion at 01:22:00).
- `[v1.3 FIX 4B]` Drain time countdown must show **18 minutes remaining** (not 22). Should display: *"18 minutes remaining — 4 minutes already elapsed since funds landed."*
- `[v1.3 FIX 4D]` Rank 1 ATM card must display *"Offsite ATM"* for CNRB-ATM-PNE-0042 (consistent with `is_onsite: false` in registry).
- Must consume `/api/v1/ingest` (POST), `/api/v1/incidents` (GET).
- No React/Vite build for MVP — plain HTML + vanilla JS + fetch API is acceptable.
- Leaflet CDN for maps. Tailwind CDN for minimal styling.
- "Acknowledge & Dispatch" button must POST back to the API and visually update status.
- Map: pulsing blue dot (mule IP estimate) with 2 km accuracy ring + 3 numbered ATM markers.

---

### Module 9: Simulation Mode UI Toggle (Phase 09 Feature 01)

| Field | Value |
|---|---|
| **Files** | `api/schemas.py`, `api/main.py`, `ui/index.html`, `tests/verify_simulation_mode.py` |
| **PRD Reference** | Phase 09 Additional Functionality — Feature 01 |
| **Description** | UI toggle switch (Admin Drawer) enabling Simulation Mode. When ON: Golden Hour gates are bypassed in `POST /api/v1/ingest`, any payload works regardless of timestamps, pipeline uses `ingestion_timestamp` as `T_ref` (deterministic output). An amber “⚡ SIM MODE” badge appears in the navbar. When OFF: Gate 1 (fraud recency ≤ 120 min) and Gate 2 (complaint ≤ 240 min) are enforced against `datetime.now(UTC)`. Drain timer registry (`_drainTimerRegistry`) backed by `localStorage` — tracks first-submission wall time per `ncrp_ticket_id` so re-submitted payloads and page refreshes continue the countdown from real elapsed time rather than restarting. Gate checks moved from `schemas.py` `model_validator` to `ingest_incident()` endpoint where simulation flag is known. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified with `/tests/verify_simulation_mode.py` (18/18 PASS). localStorage persistence confirmed live in browser — timer survives page refresh. |

---

### Module 10: Interception Window Label Rename (Phase 09 Feature 02)

| Field | Value |
|---|---|
| **File** | `ui/index.html` |
| **PRD Reference** | Phase 09 Additional Functionality — Feature 02 (PRD v1.5.0 §9.2) |
| **Description** | All user-facing occurrences of "Drain Time" in the verification dashboard renamed to "INTERCEPTION WINDOW". Countdown overlay gains subtitle "Est. window to intercept cash-out". Zero-state logic rewritten: when the timer naturally reaches zero during live countdown, shows `INTERCEPTION WINDOW: CLOSED` with `"Window expired — cash-out complete or mule fled"`. When the API returns drain_time = 0 pre-arrival due to `DAILY_LIMIT_EXHAUSTED`, shows that as the reason; other pre-zero cases show `"NO WITHDRAWABLE BALANCE"`. Reason is determined by parsing the `STAGE_2_TEMPORAL` stage detail string already returned by the API — no new API field required. `startCountdown()` refactored to accept optional `preZeroReason` parameter. |
| **Status** | `Code Complete (Unverified)` |
| **Verification Date** | Pending |
| **Notes** | Verification script `/tests/verify_interception_window.py` authored and executed: **18/18 PASS**. Awaiting user sign-off to transition to `Verified & Approved`. |

---

### Module 11: Async Webhook Dispatch Fix (Phase 09 Bug Fix 01)

| Field | Value |
|---|---|
| **File** | `api/main.py` |
| **PRD Reference** | PRD v1.6.0 §9.3 Bug Fix 01 |
| **Description** | `_dispatch_webhook()` (sync, `httpx.Client`) called inside `async def ingest_incident` froze the uvicorn event loop. Self-referential POST to `localhost:8000` could never be accepted → 10 s timeout × 4 attempts. Replaced with `_dispatch_webhook_async()` using `httpx.AsyncClient` + `await`. `time.sleep` → `asyncio.sleep`. |
| **Status** | `Fixed` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live: server log shows `[WEBHOOK] SUCCESS on attempt 1`. Injectable TestClient path unchanged — existing tests pass. |

---

### Module 12: Stale Drain Timer Registry Fix (Phase 09 Bug Fix 02)

| Field | Value |
|---|---|
| **File** | `ui/index.html` |
| **PRD Reference** | PRD v1.6.0 §9.3 Bug Fix 02 |
| **Description** | `_getSimDrainMinutes()` only registered on first encounter. Stale localStorage entry from a prior session had hours-old `submittedAt` → computed remaining = 0 → INTERCEPTION WINDOW showed CLOSED on a live 18.6-min window. Fixed: `ingest()` now force-overwrites registry entry on every new POST, anchoring `submittedAt` to current wall time. |
| **Status** | `Fixed` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live: Bengaluru payload (1.5 min) countdown ran correctly after fix. |

---

### Module 13: Crew Dispatch Persistence Fix (Phase 09 Bug Fix 03)

| Field | Value |
|---|---|
| **File** | `ui/index.html` |
| **PRD Reference** | PRD v1.6.0 §9.3 Bug Fix 03 |
| **Description** | `acknowledge()` updated DOM only. On page refresh / row re-click, `renderCards()` rebuilt from API data (no dispatch state) → buttons reverted to "Acknowledge & Dispatch", enabling duplicate crew dispatch. Added `_dispatchRegistry` Map backed by `localStorage` (`synapse_dispatch_registry`). Key: `"${ncrp_ticket_id}:${atm_id}"`. `renderCards()` now reads registry per ATM — dispatched buttons render as `done` with `"✓ Crew Dispatched — #${rank}"`. Other ATM buttons unaffected. |
| **Status** | `Fixed` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live: dispatching crew to ATM #1 then refreshing page shows `✓ Crew Dispatched — #1` with ATMs #2 and #3 still active. |

---

### Module 14: Incident Intelligence Panel (Phase 09 Feature 03)

| Field | Value |
|---|---|
| **Files** | `api/schemas.py`, `synthetic/generator.py`, `api/main.py`, `ui/index.html` |
| **PRD Reference** | PRD v1.7.0 §9.4 |
| **Description** | `complainant_name` added to `NCRPTicket` (optional, backward-compatible). Generator gains 25-name Indian pool. `payload_snapshot` field added to `IngestResponse` — structured subset of original payload for UI rendering. View B gains full-width Incident Intelligence panel: Case Overview / Source Account / Terminal Mule / Transaction Flow accordion / IP Intelligence table. All N/A-safe. |
| **Status** | `Code Complete` |
| **Verification Date** | 06 September 2026 |
| **Notes** | No verification gate required. Panel hides for incidents without `payload_snapshot` (pre-restart in-memory incidents). |

---

### Module 15: Interception Window Zero-State Messaging Fix (Phase 09 Bug Fix 04)

| Field | Value |
|---|---|
| **File** | `ui/index.html` |
| **PRD Reference** | PRD v1.8.0 §9.5 |
| **Description** | Three-case zero-state logic replaces two-case: (1) `DAILY LIMIT EXHAUSTED` via stage detail; (2) `WINDOW ELAPSED — MULE MAY BE AT ATM` when `drainable_today_inr > 100` (money present, time ran out — highest urgency); (3) `NO WITHDRAWABLE BALANCE` for genuinely empty accounts. Fixes factually incorrect 'NO WITHDRAWABLE BALANCE' message shown for Delhi payload (₹4,22,140 balance, Urgency 1.0). |
| **Status** | `Fixed` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live: Delhi payload now shows 'WINDOW ELAPSED — MULE MAY BE AT ATM'. |

---

### Module 16: Bank Feed Simulator Portal (Phase 10 Feature 01)

| Field | Value |
|---|---|
| **Files** | `ui/feed.html`, `api/main.py` |
| **PRD Reference** | PRD v2.0.0 §10.1 |
| **Description** | New Bank Feed Simulator portal at `GET /feed` — dark orange-accented design. Sections: (A) Payload Upload with Sim Mode toggle, (B) Live Withdrawal Push with incident selector and Mark Resolved button, (C) Feed Activity Log. Backend: `PATCH /api/v1/incidents/{ncrp}/live-update` mutates withdrawal/balance/drainable in `_incidents`. Dashboard polls update within 5 s and silently refreshes Intel Panel. |
| **Status** | `Code Complete` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live — withdrawal pushes appear in dashboard Intel Panel within 5 s. `/feed` route serves feed.html directly from FastAPI — no separate static server required. |

---

### Module 17: Simulation Mode Toggle in Feed Portal (Phase 10 Feature 02)

| Field | Value |
|---|---|
| **File** | `ui/feed.html` |
| **PRD Reference** | PRD v2.0.0 §10.2 |
| **Description** | Amber toggle chip in the Upload section. OFF (default) = live mode (Golden Hour gates apply, timestamp unchanged). ON = sim mode: stamps `ncrp_ticket.ingestion_timestamp = NOW()` client-side before POST, sends `?simulate=true`. Ensures interception countdown starts from upload time, not from the payload's original stale timestamp. Toggle state change logged to Feed Activity Log. |
| **Status** | `Code Complete` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live — Pune payload (18.6 min drain) uploaded with sim ON showed correct countdown immediately. |

---

### Module 18: Case Resolution (Phase 10 Feature 03)

| Field | Value |
|---|---|
| **Files** | `api/main.py`, `ui/index.html`, `ui/feed.html` |
| **PRD Reference** | PRD v2.0.0 §10.3 |
| **Description** | Backend: `POST /resolve` marks incident with reason/note/timestamp. `POST /unresolve` reopens. Frontend (dashboard): `✓ MARK RESOLVED` button in View B header → glassmorphism modal with 5 reason options + note → incident moves to Resolved Cases section in View A. KPIs count only active. Frontend (feed portal): `✓ Mark Resolved` button in Section B → calls `/resolve` → incident disappears from dropdown. |
| **Status** | `Code Complete` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live — resolved all 3 incidents from Bank Feed Simulator and confirmed Resolved Cases section appeared in dashboard. Reopen button tested. |

---

### Module 19: Drain Timer Reset Fix — Bank Feed Upload Path (Phase 10 BF05)

| Field | Value |
|---|---|
| **File** | `ui/index.html` |
| **PRD Reference** | PRD v2.0.0 §10.4 |
| **Description** | Root cause: `_drainTimerRegistry` was only populated when the dashboard itself submitted (Admin Panel) or on first row-click. Bank Feed uploads were discovered via poll but never anchored in the registry — page refresh reset timer to full initial drain. Fix: `pollIncidents()` auto-registers every new incident at first poll time and calls `_saveRegistry()` immediately. Runs regardless of dashboard sim mode. |
| **Status** | `Fixed` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified live — uploaded via Bank Feed Simulator, refreshed dashboard multiple times, countdown continued correctly. |

---

### Module 20: /feed Route & Single-Server Architecture (Phase 10 Feature 04)

| Field | Value |
|---|---|
| **File** | `api/main.py` |
| **PRD Reference** | PRD v2.0.0 §10.1 |
| **Description** | `GET /feed` endpoint added to FastAPI serving `ui/feed.html` — mirrors the existing `GET /` dashboard route. Both portals served from the same origin (`localhost:8000`), eliminating CORS issues and the need for a separate `python3 -m http.server` process. |
| **Status** | `Code Complete` |
| **Verification Date** | 06 September 2026 |
| **Notes** | Verified: `http://localhost:8000/feed` opens Bank Feed Simulator. All API calls from feed portal resolve correctly (same origin). |

---

### Module 21: Automatic Simulated Withdrawal (Phase 10 Feature 05)

| Field | Value |
|---|---|
| **File** | `api/main.py` |
| **PRD Reference** | PRD v2.0.0 §10.5 |
| **Description** | `POST /api/v1/ingest` automatically simulates ATM withdrawals for scammers in ₹10,000 brackets when `drain_time_remaining_minutes <= 0` at the moment of ingestion. It immediately deducts the simulated amount from the `terminal_mule` balance and increments withdrawals. If the window is `> 0`, it intentionally leaves the payload untouched to give the police time to act. |
| **Status** | `Code Complete` |
| **Verification Date** | 07 September 2026 |
| **Notes** | Verified live — Delhi payload (`drain_time <= 0`) auto-simulated a withdrawal correctly. |

---

### Module 22: Digital Lien Management System (Phase 11 Feature 01 + Phase 13 UX Fix)

| Field | Value |
|---|---|
| **File** | `api/main.py`, `ui/index.html` |
| **PRD Reference** | Phase 11 Feature 01 |
| **Description** | Fully persistent, backend-backed Digital Lien management system. Lien buttons appear on all intermediary and terminal mule accounts in the Transaction Flow accordion (victim's account at hop 1 sender is intentionally excluded). Every sender (hop > 1) and every receiver gets an "Initiate Digital Lien" or "Revoke Digital Lien" button. If the pipeline automatically triggers a webhook on ingestion, the terminal mule's button immediately reflects "Revoke Digital Lien [PIPELINE]". State synchronization: since the registry is keyed by account number, the receiver of Hop N and the sender of Hop N+1 (same account) always show the same button state. After any action, `fetchLienRegistry()` + `renderIntelPanel()` re-draws all buttons simultaneously. **Phase 13 UX Fix:** "Initiate Digital Lien" now opens a glassmorphism modal (`#lien-initiate-modal`, indigo accent) matching the existing Revoke modal (red accent). The browser `prompt()` dialog was removed. The modal collects a mandatory reason, validates it inline, shows a loading state during the async webhook dispatch, and handles server-side errors gracefully. |
| **Status** | `Code Complete` |
| **Verification Date** | 07 September 2026 |
| **Notes** | Verified live (07 Sep): automatic webhook dispatch flips terminal mule button state correctly; manual toggle triggers API calls properly; state survives page reloads. Phase 13 UX fix verified live: Initiate modal opens on button click, reason required, both sender/receiver buttons for same account flip simultaneously after confirm. |

---

### Module 23: Crew Dispatch Backend Migration (Phase 12 Feature 01)

| Field | Value |
|---|---|
| **File** | `api/main.py`, `ui/index.html`, `api/schemas.py` |
| **PRD Reference** | Phase 12 Feature 01 |
| **Description** | Migrated the ATM crew dispatch registry from frontend `localStorage` to a persistent backend JSON file (`data/sent_crew.json`). Implemented new `GET /api/v1/dispatch-registry` and `POST /api/v1/dispatch` endpoints. Frontend polls and diffs the backend registry instead of managing local state. |
| **Status** | `Code Complete` |
| **Verification Date** | 07 September 2026 |
| **Notes** | Verified live — UI correctly reads dispatched crews across page refreshes based on backend API. |

---

### Module 24: Bank Feed Simulator Case Resolution & Shared Identity (BUG-001)

| Field | Value |
|---|---|
| **File** | `ui/feed.html`, `Assumptions.MD`, `bug.md` |
| **PRD Reference** | Phase 10 Feature 02, §5.4 Case Resolution |
| **Description** | Replaced the direct button action with a full glassmorphism resolution modal (`#resolve-modal`) in `ui/feed.html`. The modal reads, updates, and syncs the operator badge/name with `localStorage.getItem('synapse_operator_id')` shared across Synapse and the feed simulator, strictly enforcing non-blank operator accountability. Replaced hardcoded reason with a dynamic selector for all 5 valid Synapse resolution taxonomy options (`FUNDS_FROZEN`, `MULE_APPREHENDED`, `FUNDS_RECOVERED`, `WINDOW_ELAPSED_CASE_CLOSED`, `FALSE_POSITIVE`). Sends `{ reason, operator_id, note }` to `POST /api/v1/incidents/{ncrp}/resolve`. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 08 September 2026 |
| **Notes** | Verified via `tests/verify_feed_resolution.py` (17/17 PASS) and user sign-off. |

---

### Module 25: Terminal Mule Viability Ingestion Rejection (HTTP 422)

| Field | Value |
|---|---|
| **File** | `api/main.py`, `ui/index.html`, `tests/verify_no_viable_atm_mule.py`, `tests/verify_api.py`, `PRD.md`, `asbuilt.html`, `Assumptions.MD` |
| **PRD Reference** | §4.2 Step 5 Mule Viability Filter, §1.3 Golden Hour Admission |
| **Description** | Corrected `NO_VIABLE_ATM_MULE` behavior in `api/main.py` from a disguised acceptance (HTTP 200 with partial payload, persisted to `_incidents`) to an immediate `HTTPException(422, detail=f"NO_VIABLE_ATM_MULE: {stage1.disqualification_reason}")`. Non-viable cases are dropped before reaching `_incidents` or the tactical dashboard, preventing zero-state rendering anomalies and corrupted UI states. Updated `ui/index.html` admin drawer 422 error handler to mark Golden Hour passed, Stage 1 failed with clean reason, subsequent stages skipped, and hide the active result card. Updated `PRD.md`, `asbuilt.html`, and `Assumptions.MD`. |
| **Status** | `Verified & Approved` |
| **Verification Date** | 08 September 2026 |
| **Notes** | Verified via `tests/verify_no_viable_atm_mule.py` (10/10 PASS) and `tests/verify_api.py` (27/27 PASS). |

---

### Module 26: Bulk Payload Upload & Dynamic Simulation Engine

| Field | Value |
|---|---|
| **File** | `ui/feed.html`, `ui/index.html`, `api/main.py`, `tests/verify_bulk_upload.py`, `synthetic/generator.py`, `synthetic/generator_custom.py`, `PRD.md`, `asbuilt.html`, `Assumptions.MD` |
| **PRD Reference** | Phase 13 (§13.1–§13.5), §10.3 Bank Feed Simulator |
| **Description** | Built a multi-file bulk payload upload engine in `ui/feed.html` with client-side 5.0-second sequential pacing, live telemetry cards, dynamic countdown display, and non-blocking error continuation. Implemented unified timestamp alignment (`alignPayloadTimestampsToNow`) across `ui/feed.html` and `ui/index.html` ensuring mathematical $\tau$ invariance ($(T_{\text{ingest}}+\Delta) - (T_{\text{txn}}+\Delta) \equiv \tau$) and preserving calibrated non-zero interception windows ($15.7\text{m}$ to $38.5\text{m}$) without triggering premature auto-withdrawals or 6-hour garbage collector sweeps. Bound the bulk queue to the `#sim-toggle` (`_simMode`) switch to separate live ingestion from simulation bypass. Added `POST /api/v1/incidents/clear` and a `🧹 Clear Incident Feed` button for clean simulation testing. Restricted synthetic generators strictly to ATM registry cities (Pune, Bengaluru, Delhi). |
| **Status** | `Verified & Approved` |
| **Verification Date** | 08 September 2026 |
| **Notes** | Verified via `tests/verify_bulk_upload.py` (32/32 PASS), `tests/verify_no_viable_atm_mule.py` (10/10 PASS), `tests/verify_feed_resolution.py` (21/21 PASS), and `tests/verify_api.py` (27/27 PASS). |

---

### Module 27: ATM Live Withdrawal Integration, Suspect GPS Pinpoint, 3rd Location Telemetry Source & Transaction Logging

| Field | Value |
|---|---|
| **File** | `api/schemas.py`, `api/main.py`, `ui/feed.html`, `ui/index.html`, `tests/verify_live_atm_withdrawal.py`, `PRD.md`, `asbuilt.html`, `Assumptions.MD` |
| **PRD Reference** | Phase 14 (§14.1–§14.4), §10.3 Bank Feed Simulator |
| **Description** | Replaced the free-form text note input in `ui/feed.html` with `#atm-select`, a dynamic dropdown populated with the active incident's Top 3 candidate ATMs (`inc.top_atms`). Pushing a withdrawal at a selected ATM updates suspect GPS coordinates directly to the ATM's latitude and longitude (`mule_estimated_lat`, `mule_estimated_lon`), updating `mule_location_method` to `ATM_WITHDRAWAL_CONFIRMED`. Recorded the cash-out as the 3rd Location Source (`Location Source 3: ATM Terminal Telemetry`) in the Feed Activity Log, Stage 3 Spatial details, and the dashboard's Location Intelligence panel. Updated Leaflet map rendering in `ui/index.html` to display an amplified 22px pulsing emerald radar beacon (`.mule-dot.confirmed`, z-index 2500) with an 800m tactical interdiction perimeter and a 300m inner cordon. Added `ATM_CASH_WITHDRAWAL` to `PaymentChannel` and automatically appended discrete cash-out transactions to `payload_snapshot.transactions`, rendering them in the Transaction Flow accordion. Added cross-dashboard navigation links and direct deep-linking via `?ncrp=`. |
| **Status** | `Verified & Pending User Sign-Off` |
| **Verification Date** | 09 September 2026 |
| **Notes** | Verified via `tests/verify_live_atm_withdrawal.py` (23/23 PASS), `tests/verify_bulk_upload.py` (32/32 PASS), `tests/verify_feed_resolution.py` (21/21 PASS), and `tests/verify_no_viable_atm_mule.py` (10/10 PASS). |

---

## Verification Audit Log

All verification records are appended here chronologically. Each entry is created only after a verification script is executed, terminal output is presented, and the user explicitly approves.

| Date | Module | Verification Script | Result | Approved By |
|---|---|---|---|---|
| 05 Sep 2026 | Module 1 & 2 | `/tests/verify_schemas_and_registry.py` | PASS | USER |
| 05 Sep 2026 | Module 3 | `/tests/verify_generator.py` | PASS | USER |
| 05 Sep 2026 | Module 4 | `/tests/verify_graph.py` | PASS | USER |
| 05 Sep 2026 | Module 5 | `/tests/verify_temporal.py` | PASS | USER |
| 05 Sep 2026 | Module 6 | `/tests/verify_cluster.py` | PASS | USER |
| 05 Sep 2026 | Module 7 | `/tests/verify_api.py` | PASS (26/26) | USER |
| 05 Sep 2026 | Module 8 | `/tests/verify_ui_smoke.py` | PASS (35/35) | USER |
| 06 Sep 2026 | Module 9 — Phase 09 F01 | `/tests/verify_simulation_mode.py` | PASS (18/18) | USER |
| 06 Sep 2026 | Module 10 — Phase 09 F02 | `/tests/verify_interception_window.py` | PASS (18/18) | Pending |
| 06 Sep 2026 | Module 11 — BF01 Webhook Async | N/A — verified live (server log: Attempt 1 SUCCESS) | PASS | N/A |
| 06 Sep 2026 | Module 12 — BF02 Timer Registry | N/A — verified live (INTERCEPTION WINDOW countdown active) | PASS | N/A |
| 06 Sep 2026 | Module 13 — BF03 Dispatch Persist | N/A — verified via localStorage persistence on refresh | PASS | N/A |
| 06 Sep 2026 | Module 14 — F03 Intel Panel | N/A — verified live (Bengaluru payload — all 5 sections render) | PASS | N/A |
| 06 Sep 2026 | Module 15 — BF04 Zero-State Msg | N/A — verified live (Delhi: 'WINDOW ELAPSED — MULE MAY BE AT ATM') | PASS | N/A |
| 06 Sep 2026 | Module 16 — Bank Feed Simulator | N/A — verified live (withdrawal push → dashboard Intel Panel updates ≤5 s) | PASS | N/A |
| 06 Sep 2026 | Module 17 — Sim Mode Toggle | N/A — verified live (Pune payload: correct countdown from upload time) | PASS | N/A |
| 06 Sep 2026 | Module 18 — Case Resolution | N/A — verified live (3 cases resolved + reopened from both portals) | PASS | N/A |
| 06 Sep 2026 | Module 19 — BF05 Timer Fix | N/A — verified live (countdown survived page refresh after Bank Feed upload) | PASS | N/A |
| 06 Sep 2026 | Module 20 — /feed Route | N/A — verified: `localhost:8000/feed` serves simulator correctly | PASS | N/A |
| 07 Sep 2026 | Module 21 — Auto Withdrawal | N/A — verified live (simulated withdrawal on ingestion when time <= 0) | PASS | N/A |
| 07 Sep 2026 | Module 22 — Digital Lien System | N/A — verified live (syncs UI state with manual/pipeline webhooks) | PASS | N/A |
| 07 Sep 2026 | Module 23 — Dispatch Migration | N/A — verified live (syncs UI state with backend sent_crew.json) | PASS | N/A |
| 08 Sep 2026 | Module 24 — Bank Feed Resolution (BUG-001) | `/tests/verify_feed_resolution.py` | PASS (17/17) | USER |
| 08 Sep 2026 | Module 25 — Mule Viability Rejection | `/tests/verify_no_viable_atm_mule.py` | PASS (10/10) | USER |
| 08 Sep 2026 | Module 26 — Bulk Upload & Dynamic Sim | `/tests/verify_bulk_upload.py` | PASS (32/32) | USER |
| 09 Sep 2026 | Module 27 — ATM Live Withdrawal & Pinpoint | `/tests/verify_live_atm_withdrawal.py` | PASS (23/23) | PENDING USER SIGNOFF |

---

## Phase Roadmap (Reference)

| Phase | Scope | Status |
|---|---|---|
| **Phase 0** | Initialization & Scaffolding (schemas, ATM registry, project structure) | Complete |
| **Phase 1** | Core Pipeline (Stage 1 graph, Stage 2 temporal, Stage 3 spatial) | Complete |
| **Phase 2** | API Integration (FastAPI endpoints, webhook dispatch, confidence aggregation) | Complete |
| **Phase 3** | Verification Interface (UI, maps, demo flow) | Complete |
| **Phase 4** | Synthetic Data & End-to-End Demo (3-city payloads, 90-second demo rehearsal) | Complete |
| **Phase 9** | Additional Functionality (F01 Sim Mode, F02 Interception Window, F03 Intel Panel, BF01–04) | **Complete** |
| **Phase 10** | Live Feed Simulator (Bank Feed portal, Case Resolution, Sim Toggle, Timer Fix, /feed route) | **Complete** |
| **Phase 11** | Digital Lien Management System (Persistent manual/pipeline registry, UI Sync) | **Complete** |
| **Phase 12** | Crew Dispatch Backend Migration (Move dispatch registry to sent_crew.json) | **Complete** |
| **Phase 13** | Bulk Ingestion Engine, Dynamic Simulation Alignment & Operational Maintenance | **Complete** |
| **Phase 14** | Live ATM Withdrawal, Suspect GPS Pinpoint, 3rd Location Source & Transaction Flow | **Complete** |

---

## Critical Fix Quick-Reference (for session bootstrap)

This table summarizes the fixes most likely to cause implementation bugs if missed. Every fix listed here overrides an earlier version of the PRD — if you recall an older formulation, it is wrong.

| Fix ID | What Was Wrong | What To Do Instead | Module(s) Affected |
|---|---|---|---|
| **1D** | MPS recency `1/Δt` spiked to 60.0, drowning other features | Use `exp(-0.1 × Δt)`, bounded [0, 1] | Module 4 |
| **3D** | `transactions[-1]` not guaranteed temporally latest | Use `max(txn_timestamp)` across all transactions | Module 1, 7 |
| **4A** | complaint_timestamp was BEFORE first fraud txn | Set complaint AFTER last hop | Module 3 |
| **4B** | Drain time was total duration, not remaining | Subtract τ: `max(0, N_w×Δt − τ)` | Module 5, 7, 8 |
| **4C** | D̂=0 → urgency=1.0 (inverted for exhausted limits) | Guard: if `DAILY_LIMIT_EXHAUSTED` → urgency=0.0 | Module 5, 7 |
| **4D** | Onsite ATM can't score 0.91 (max is 0.85) | CNRB-ATM-PNE-0042 is `is_onsite: false` | Module 2 |
| **4E** | D_norm goes negative on radius expansion | Use `r_active` not `r_search`, clamp `max(0,...)` | Module 6 |

---

> **Reminder:** Per `instructions.md` §3, writing code is NOT completing a task. No module may transition to `Verified & Approved` without: (1) a standalone verification script, (2) terminal execution output presented in chat, and (3) explicit user sign-off.
