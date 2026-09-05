# HANDOVER.md — Project Synapse Session Briefing

> **Project:** Synapse (SIH26184) — Automated Spatial-Temporal Cyber Fraud Interception  
> **PRD Version:** v1.3.0 (16 fixes across 3 review rounds)  
> **Date:** 05 September 2026  
> **Repository State:** Clean, all 7 test suites passing 100%, Phases 0–3 Complete, Phase 4 Active.

---

## 1. Project Overview & Architectural Milestones

Synapse transforms the reactive I4C fraud response model (*log → investigate → freeze*) into a proactive interception pipeline (*detect → predict → intercept*) operating within the **Golden Hour** (120 minutes from fraud recency).

### Completed & Verified Modules (Phases 0–3):

1. **Module 1 & 2: Schemas & ATM Registry ([`/api/schemas.py`](file:///Users/akshai/Developer/Synapse/api/schemas.py), [`/data/atm_registry.json`](file:///Users/akshai/Developer/Synapse/data/atm_registry.json))**
   - Pydantic v2 schemas for `Incident_Payload` and `FreezeCardATM` webhook.
   - Dual-Gate Golden Hour validation: Gate 1 (`NOW() - max(txn_timestamp) <= 120m`) & Gate 2 (`NOW() - complaint <= 240m`).
   - 200 synthetic ATM records across Pune, Bengaluru, and Delhi.
   - `CNRB-ATM-PNE-0042` set to `is_onsite: false` (`[v1.3 FIX 4D]`).
   - Verified via [`/tests/verify_schemas_and_registry.py`](file:///Users/akshai/Developer/Synapse/tests/verify_schemas_and_registry.py).

2. **Module 3: Synthetic Data Generator ([`/synthetic/generator.py`](file:///Users/akshai/Developer/Synapse/synthetic/generator.py))**
   - Generates valid demo payloads: Pune (`payload_pune.json`), Bengaluru (`payload_bengaluru.json`), and Delhi (`payload_delhi.json`).
   - Fixed temporal paradox: complaint filed 1 min after last hop (`[v1.3 FIX 4A]`).
   - Verified via [`/tests/verify_generator.py`](file:///Users/akshai/Developer/Synapse/tests/verify_generator.py).

3. **Module 4: Stage 1 — Graph DAG & Viability Filter ([`/core/graph.py`](file:///Users/akshai/Developer/Synapse/core/graph.py))**
   - NetworkX `DiGraph` leaf isolation with `[v1.2 FIX 1D]` bounded exponential decay MPS:
     $$\text{MPS}(v) = 0.30 \cdot (A_v / A_{\max}) + 0.30 \cdot e^{-0.1 \cdot \Delta t} + 0.40 \cdot \mathbb{I}[\text{match}]$$
   - 3-point ATM viability filter (debit card present, eligible account type, non-payment bank).
   - Verified via [`/tests/verify_graph.py`](file:///Users/akshai/Developer/Synapse/tests/verify_graph.py).

4. **Module 5: Stage 2 — Capped Drain Time Engine ([`/core/temporal.py`](file:///Users/akshai/Developer/Synapse/core/temporal.py))**
   - $B_{\text{accessible}} = \min(B, W_{\text{limit}} - W_{\text{today}})$ with `[v1.3 FIX 4B]` elapsed $\tau$ subtraction:
     $$\hat{D} = \max(0.0, \; \lceil B_{\text{accessible}} / W_{\text{txn}} \rceil \times \bar{\Delta t} - \tau)$$
   - Exactly matches PRD worked example: $₹241,350$ balance $\to \mathbf{18.6\text{ min}}$ remaining.
   - `[v1.3 FIX 4C]` Urgency guard clamps to $0.0$ when `DAILY_LIMIT_EXHAUSTED`.
   - Verified via [`/tests/verify_temporal.py`](file:///Users/akshai/Developer/Synapse/tests/verify_temporal.py).

5. **Module 6: Stage 3 — Haversine Spatial Ranker ([`/core/cluster.py`](file:///Users/akshai/Developer/Synapse/core/cluster.py))**
   - Priority cascade positioning: `COMBINED` $\to$ `CELL_TOWER` $\to$ `IP_GEOLOCATION` $\to$ `IFSC_BRANCH_FALLBACK`.
   - Haversine candidate retrieval with $1.5\times$ radius expansion ($r_{\text{active}} \in \{5.0, 7.5, 11.25\}\text{ km}$).
   - Multi-factor scoring with $r_{\text{active}}$ normalization (`[v1.3 FIX 4E]`) and offsite preference (`[v1.3 FIX 4D]`).
   - Ranks `CNRB-ATM-PNE-0042` as Rank 1 (Score: $0.9738$).
   - Verified via [`/tests/verify_cluster.py`](file:///Users/akshai/Developer/Synapse/tests/verify_cluster.py).

6. **Module 7: FastAPI Core & Mock Webhook ([`/api/main.py`](file:///Users/akshai/Developer/Synapse/api/main.py))**
   - Endpoints: `POST /api/v1/ingest`, `POST /api/v1/freeze-card-atm`, `GET /api/v1/incidents`, `GET /api/v1/atm-registry`.
   - Composite confidence calculation with `[v1.3 FIX 4C]` urgency guard and IFSC fallback cap ($C \le 0.75$).
   - Tiered intervention: $C \ge 0.85 \implies \text{SECONDARY\_PHYSICAL}$, $0.70 \le C < 0.85 \implies \text{PRIMARY\_DIGITAL}$.
   - Simulation Mode (`X-Simulation-Mode: true` or `?simulate=true`) anchors reference clock to `ingestion_timestamp`.
   - Verified via [`/tests/verify_api.py`](file:///Users/akshai/Developer/Synapse/tests/verify_api.py) (26/26 PASS).

7. **Module 8: Barebones Verification Interface ([`/ui/index.html`](file:///Users/akshai/Developer/Synapse/ui/index.html))**
   - Single-page interface with Admin Drawer, View A (Strategic Command), and View B (Tactical Interception).
   - Leaflet map with pulsing blue mule dot, 2 km accuracy ring, 3 numbered ATM pins, and auto-`fitBounds`.
   - Live JavaScript countdown timer ($mm:ss$) updating remaining drain time each second.
   - Mounted directly via FastAPI (`GET /` serves [`ui/index.html`](file:///Users/akshai/Developer/Synapse/ui/index.html), `/synthetic` mounts demo payloads).
   - Verified via [`/tests/verify_ui_smoke.py`](file:///Users/akshai/Developer/Synapse/tests/verify_ui_smoke.py) (35/35 PASS).

---

## 2. Complete Regression Test Command

To independently verify repository health across all modules in one pass:

```bash
python3 tests/verify_schemas_and_registry.py && \
python3 tests/verify_generator.py && \
python3 tests/verify_graph.py && \
python3 tests/verify_temporal.py && \
python3 tests/verify_cluster.py && \
python3 tests/verify_api.py && \
python3 tests/verify_ui_smoke.py
```

*Expected Result:* All 7 standalone test suites exit with code 0.

---

## 3. Active Milestone: Phase 4 (Synthetic Data & End-to-End Demo)

### Live Server Command:
```bash
uvicorn api.main:app --reload --port 8000
```
- Dashboard UI: `http://localhost:8000`
- API Documentation: `http://localhost:8000/docs`

### Active Tasks:
1. **90-Second Evaluation Demo Rehearsal (Pune Scenario)**: Run end-to-end rehearsal per PRD §8.2:
   - Ingest `payload_pune.json` via Admin Drawer
   - Inspect Strategic Command (View A)
   - Inspect Tactical Interception (View B): Leaflet pins, live drain countdown, Rank 1 `CNRB-ATM-PNE-0042` (Offsite ATM, 0.97 RiskScore)
   - Trigger "Acknowledge & Dispatch"
   - Confirm terminal webhook receipt log
2. **Secondary Scenarios**: Verify Bengaluru and Delhi demo payloads through the pipeline.

---

## 4. Operational Protocols

Per `Instructions.MD`:
- **Gatekeeper Protocol**: Always present a clear 3–5 bullet point plan and await explicit user confirmation ("Proceed" / "Approved") before modifying any code.
- **Verification Gate**: Before marking any task completed in `PROJECT_STATE.md`, run a standalone verification script, print terminal output proof, and obtain explicit sign-off.
- **Living Rationale**: Log any technical, algorithmic, or architectural decisions in `ASSUMPTIONS.md`.
