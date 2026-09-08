# ASSUMPTIONS.md — Project Synapse Living Rationale Log

> **Protocol:** Every entry follows the schema mandated by `instructions.md` §2.  
> **Source Document:** PRD v1.3.0 (05 Sep 2026)  
> **Last Updated:** 05 September 2026 — v1.3 fixes applied (timestamp paradox, drain time τ, urgency guard, onsite ATM ceiling, r_active proximity)  

---

## Category 1: ML & Algorithmic Heuristic

---

### [ML & Algorithmic Heuristic] Mule Probability Score (MPS) Weights
* **Context / Decision:** When multiple leaf nodes exist in the fund flow DAG, they are ranked using MPS with weights `w1=0.30` (amount ratio), `w2=0.30` (recency), `w3=0.40` (CFCFRMS pre-identification match).
* **Why This Over Alternatives:** `w3` is intentionally the heaviest because CFCFRMS has already performed upstream triage — if the payload explicitly names a terminal mule, that signal should dominate over purely derived features. Equal-weight (0.33/0.33/0.33) was rejected because it under-values the pre-identification signal. Amount-dominant weighting was rejected because a small-amount leaf can still be the cash-out node (mule keeps a cut).
* **System Impact:** Affects Stage 1 output. If CFCFRMS pre-identification is wrong, `w3=0.40` will bias toward a bad node. Mitigated by the `MULE_MISMATCH_WARNING` log when DAG-derived and payload-declared mules diverge.

---

### ~~[ML & Algorithmic Heuristic] MPS Recency Term Epsilon Guard~~ → SUPERSEDED by v1.2 FIX 1D
* **Context / Decision:** ~~The MPS recency term uses `1 / max(T_now − T_v, ε)` where `ε = 1/60 min` (1 second) to prevent divide-by-zero.~~
* **Why This Over Alternatives:** ~~A hard `ε` floor is simpler and more debuggable than alternatives.~~
* **System Impact:** **SUPERSEDED.** The epsilon guard prevented the divide-by-zero crash but did NOT fix the underlying scaling imbalance: the recency term could spike to 60.0 while amount and match terms max at 1.0. This meant `w2 × 60 = 18.0` dominated the MPS, making the intended weight hierarchy (`w3 > w1 = w2`) meaningless. See replacement entry below.

---

### [ML & Algorithmic Heuristic] MPS Recency Term — Bounded Exponential Decay (v1.2 FIX 1D)
* **Context / Decision:** The MPS recency term uses `e^(−μ × (T_now − T_v))` where `μ = 0.1 min⁻¹` (half-life ≈ 7 minutes). This naturally maps to [0, 1]: a transaction at `Δt = 0` scores 1.0, at `Δt = 7 min` scores ~0.50, at `Δt = 30 min` scores ~0.05. All three MPS components (amount, recency, match) now share the same [0, 1] range.
* **Why This Over Alternatives:** (1) The v1.1 reciprocal `1/Δt` was unbounded — it spiked to 60.0 at `Δt = ε`, producing a recency contribution of `w2 × 60 = 18.0` that completely dominated amount (max 0.3) and CFCFRMS match (max 0.4). The weights were decorative, not functional. (2) Alternative `1 / (1 + Δt)` was considered but has a non-linear compression that over-weights the first few minutes and under-weights everything beyond ~5 min. (3) A linear decay `max(0, 1 − Δt/T_max)` was rejected because it requires choosing an arbitrary `T_max` cutoff and goes to zero abruptly. Exponential decay degrades smoothly with a single interpretable parameter (half-life).
* **System Impact:** All three MPS components are now in [0, 1]. The weight vector `w1=0.30, w2=0.30, w3=0.40` behaves as designed: CFCFRMS match is the dominant signal (max contribution 0.40), and recency/amount are secondary (max 0.30 each). MPS ∈ [0, 1.0]. The `μ = 0.1` decay constant means transactions older than ~30 min have near-zero recency contribution, which is appropriate for Golden Hour urgency. No division-by-zero risk — exponential decay is defined for all `Δt ≥ 0`.

---

### [ML & Algorithmic Heuristic] Drain Time Analytical Baseline Formula (v1.3 FIX 4B update)
* **Context / Decision:** The MVP fallback drain time is computed as `D̂ = max(0, ⌈B_accessible / W_txn⌉ × Δt_mean − τ)` where `B_accessible = min(B, W_limit − W_today)`, `W_txn = ₹20,000`, `Δt_mean = 4.5 min`, and `τ = T_now − T_last_txn` (minutes elapsed since the mule received funds). `[v1.3 FIX 4B]` — The formula now subtracts elapsed time τ to produce a **remaining** estimate rather than total session duration.
* **Why This Over Alternatives:** The v1.1 formula `N_w × Δt_mean` computed the *total* withdrawal session duration (22.5 min in the Pune example). But the PRD defines drain time as "minutes remaining," and by the time Synapse processes the payload, τ minutes have already elapsed. The mule may have already begun withdrawing during τ. Not subtracting τ over-estimates the remaining window. Alternative: only subtract τ when τ < total session time. This is what the `max(0, ...)` clamp achieves — if τ exceeds the total session estimate (mule was slow to start or didn't start), remaining time floors at 0.
* **System Impact:** Pune worked example: total = 22.5 min, τ = 3.88 min, remaining = 18.6 min. This is a 17% reduction in the drain time estimate, which increases the urgency component in confidence scoring (shorter remaining → higher urgency). Downstream: webhook, View B countdown, explainability report all show 18.6 min instead of 22.5 min.

---

### [ML & Algorithmic Heuristic] Mean Inter-Withdrawal Interval (Δt = 4.5 min)
* **Context / Decision:** The average time between successive ATM withdrawals by a mule is set to 4.5 minutes.
* **Why This Over Alternatives:** Calibrated against synthetic mule trajectories from localized AMLSim. Real-world ATM transaction telemetry from RBI suggests average legitimate withdrawal takes ~2 min at the terminal + ~2.5 min travel to next ATM in urban areas. Mules are observed to be faster than average (no queue waiting, pre-planned routes). Values tested: 3.0 min (too aggressive, assumes zero travel), 4.5 min (balanced), 7.0 min (too conservative, matches semi-urban patterns). 4.5 min selected as urban default.
* **System Impact:** Directly scales drain time linearly. If this is too low, we over-estimate urgency (higher false positive rate for tactical dispatch). If too high, we under-estimate urgency (miss the interception window). Revisit when real outcome data is available.

---

### [ML & Algorithmic Heuristic] Temporal Decay Constant for Cell Tower Weighting (λ = 0.05 min⁻¹)
* **Context / Decision:** Cell tower signal weights decay exponentially with `λ = 0.05 min⁻¹`, meaning a tower observation 20 minutes old is weighted at `e^(−0.05 × 20) ≈ 0.37` of a current observation.
* **Why This Over Alternatives:** `λ = 0.05` provides a half-life of ~14 minutes, which aligns with typical urban pedestrian/vehicle movement patterns within the Golden Hour. `λ = 0.01` (half-life ~69 min) was too slow — old tower pings would dominate even after the mule has moved. `λ = 0.20` (half-life ~3.5 min) was too aggressive — would discard useful observations in sparse-ping scenarios. Note: this parameter is **Future State only** since cell tower data is not used in MVP.
* **System Impact:** Affects mule position estimation accuracy when cell tower data is available. No MVP impact (cell_tower_cluster is empty in MVP payloads).

---

### [ML & Algorithmic Heuristic] Cell Tower vs IP Geolocation Blend Weight (α = 0.7)
* **Context / Decision:** When both cell tower and IP geolocation are available, the final position estimate blends them as `P_final = 0.7 × P_cell + 0.3 × P_IP`.
* **Why This Over Alternatives:** Cell tower trilateration is generally more spatially precise than IP geolocation (sub-km vs city-level). The 70/30 split reflects this accuracy differential. Equal weighting (50/50) was rejected because it would let noisy IP geo drag the estimate away from the better cell tower signal. 90/10 was rejected because IP geo can correct for cell tower edge cases (e.g., when only 1 tower is observed, trilateration degrades to a single-point estimate).
* **System Impact:** No MVP impact (cell tower is Future State). In production, this weight should be dynamically adjusted based on the number of cell tower observations (more towers → higher α).

---

### [ML & Algorithmic Heuristic] ATM Search Radius (5 km urban, 15 km rural)
* **Context / Decision:** The haversine radius query uses `r_search = 5.0 km` in urban areas and `15.0 km` in semi-urban/rural areas, classified by PIN code density.
* **Why This Over Alternatives:** Urban ATM density is ~2.5 per 10K population; 5 km captures ~30–80 ATMs in a metro, which is sufficient for Top-3 ranking without excessive noise. Rural ATM density is ~0.4 per 10K; a 5 km radius would often return < 3 ATMs, triggering unnecessary fallback expansions. The 15 km rural radius typically captures 5–15 ATMs. A uniform 10 km radius was rejected as a poor compromise (too wide for metros = noisy, too narrow for rural = sparse).
* **System Impact:** Directly determines the candidate set size for Stage 3c scoring. Larger radius → more candidates → more computation but better coverage. The fallback expansion (1.5× up to 2 times) handles edge cases where even the configured radius returns < 3 ATMs.

---

### [ML & Algorithmic Heuristic] ATM Risk Score Component Weights
* **Context / Decision:** `β1=0.30` (proximity), `β2=0.25` (bank match), `β3=0.20` (cash status), `β4=0.15` (offsite preference), `β5=0.10` (traffic anonymity).
* **Why This Over Alternatives:** Proximity is the strongest predictor (mules minimize travel time) but capped at 0.30 (reduced from v1.0.0's 0.40) to accommodate the traffic factor. Bank match is second because same-bank ATMs have higher withdrawal limits and no interbank fees — a strong mule preference documented in I4C case studies. Cash status and offsite preference are supporting signals. Traffic anonymity (β5=0.10) is the weakest weight because it's a secondary behavioral hypothesis (mules blend into busy ATMs) with less empirical support than proximity or bank match.
* **System Impact:** All five weights sum to 1.0. RiskScore ∈ [0, 1]. Changing any weight requires revalidating the Top-3 ranking against synthetic test cases.

---

### [ML & Algorithmic Heuristic] Cash Replenishment Status Scoring
* **Context / Decision:** Cash availability is mapped to a linear score: `FULL=1.0`, `PARTIAL=0.7`, `LOW=0.3`, `EMPTY=0.0`, `UNKNOWN=0.5`.
* **Why This Over Alternatives:** Linear mapping is simple and interpretable. A binary (has cash / no cash) mapping was rejected because it loses the distinction between FULL and LOW — a mule needing 5 withdrawals is more likely to target a FULL ATM. `UNKNOWN=0.5` is a neutral default — neither penalizes nor rewards uncertainty. An alternative of `UNKNOWN=0.0` (conservative) was rejected because it would exclude ATMs with stale replenishment data, reducing the candidate pool unnecessarily.
* **System Impact:** Contributes to ATM ranking via `β3=0.20`. An ATM with EMPTY status gets zero from this component and is unlikely to rank in Top 3 unless it dominates on other factors.

---

### [ML & Algorithmic Heuristic] Traffic Anonymity Log Normalization
* **Context / Decision:** The traffic component uses `log(1 + count) / log(1 + max_count)` rather than raw linear normalization.
* **Why This Over Alternatives:** Log normalization compresses the dynamic range. Raw `count / max_count` would make the scoring dominated by outlier ATMs (e.g., airport ATMs with 500+ txn/day vs residential ATMs with 50 txn/day). With log normalization, an ATM with 150 txn/day and one with 300 txn/day score much closer together, which matches the behavioral hypothesis (mules need "busy enough," not "the busiest"). The `+1` prevents `log(0)`.
* **System Impact:** Prevents outlier ATMs from dominating the traffic component. An ATM with 500 daily txns scores ~1.0, while one with 100 txns scores ~0.74 — a much tighter spread than the 5:1 ratio under linear normalization.

---

### [ML & Algorithmic Heuristic] Confidence Aggregation Weights & Urgency Guard (v1.3 FIX 4C update)
* **Context / Decision:** The composite confidence score weights spatial targeting (γ3=0.45) highest, urgency (γ2=0.35) second, and mule identification certainty (γ1=0.20) lowest. `[v1.3 FIX 4C]` — The urgency component has an explicit guard: when `DAILY_LIMIT_EXHAUSTED` is flagged, urgency is hard-clamped to 0.0 regardless of D̂. This prevents the D̂=0 inversion where the formula `(1 − 0/120)⁺ = 1.0` would yield *maximum* urgency for a mule who *cannot* withdraw.
* **Why This Over Alternatives:** Spatial confidence is weighted highest because the primary output of Synapse is *where* to intercept. The urgency guard was necessary because the standard formula treats D̂=0 as "drain is imminent" (correct for a mule about to finish their last withdrawal) but `DAILY_LIMIT_EXHAUSTED` sets D̂=0 for the opposite reason (mule has *no* remaining capacity). Without the guard, a DAILY_LIMIT_EXHAUSTED incident injects γ2 × 1.0 = 0.35 into the composite score — enough to push a non-threat over the 0.70 digital intervention threshold. Alternative: use D̂ = ∞ (or 120) for exhausted limits. Rejected because it overloads the semantics of drain time. The explicit guard is clearer and more auditable.
* **System Impact:** DAILY_LIMIT_EXHAUSTED incidents now produce urgency = 0.0, which means the composite score drops by up to 0.35. These incidents are very unlikely to trigger automated interventions (PRIMARY_DIGITAL requires C ≥ 0.70), which is the correct behavior — the mule can't withdraw, so card holds are unnecessary.

---

### [ML & Algorithmic Heuristic] Intervention Tier Thresholds (0.70, 0.85, 0.92)
* **Context / Decision:** Three thresholds govern escalation: `C ≥ 0.70` triggers PRIMARY_DIGITAL (automated card hold), `C ≥ 0.85` triggers SECONDARY_PHYSICAL (tactical dispatch recommendation), `C ≥ 0.92` allows ALL_CHANNELS_BLOCK (broadest hold scope).
* **Why This Over Alternatives:** The 0.70 floor is set to allow some tolerance for model uncertainty while preventing action on weak signals. Industry fraud detection systems typically use 0.60–0.80 for automated actions. The 0.85 physical threshold adds a 15-point gap to ensure physical LEA deployment only occurs on high-confidence predictions, reducing false dispatches. The 0.92 all-channels threshold is deliberately extreme because blocking all debit transactions (not just ATM) has high collateral risk if the account is legitimately owned.
* **System Impact:** These are ADMIN-configurable (ADM-06). Lowering thresholds increases interception rate but also false-positive rate. The auto-release timer at Golden Hour expiry mitigates damage from false positives at any threshold.

---

### [ML & Algorithmic Heuristic] IFSC Branch Fallback Confidence Cap (0.75)
* **Context / Decision:** When the mule's location is estimated via IFSC branch coordinates (lowest accuracy method), the composite confidence score is capped at 0.75 regardless of other components.
* **Why This Over Alternatives:** IFSC branch location is the mule's *account opening address*, not their current physical location. The mule could be anywhere in the city (or country). Allowing a high confidence score based on this imprecise location would trigger physical dispatch to ATMs near the branch — which is essentially random targeting. The 0.75 cap ensures IFSC fallback can trigger a card hold (PRIMARY_DIGITAL at 0.70) but **never** a tactical dispatch (SECONDARY_PHYSICAL at 0.85).
* **System Impact:** Incidents with `mule_location_method = IFSC_BRANCH_FALLBACK` can only reach PRIMARY_DIGITAL tier. This is a deliberate safety constraint — physical LEA dispatch requires at minimum IP geolocation accuracy.

---

## Category 2: Architectural & Design Choice

---

### [Architectural & Design Choice] Single Consolidated JSON Payload Ingestion
* **Context / Decision:** All incident data (NCRP ticket, fund flow, terminal mule intelligence) is submitted as a single `Incident_Payload.json` document via the Admin Drawer, rather than via separate API endpoints or a multi-step form.
* **Why This Over Alternatives:** A single payload ensures atomic ingestion — either all data arrives or none does. Separate endpoints (e.g., POST /ticket, POST /fund-flow, POST /mule) would require session-based correlation, introduce partial-state bugs (fund flow arrives but mule data doesn't), and complicate the Admin Drawer UX. The upstream system (CFCFRMS) can assemble the full payload before submission, so splitting provides no operational benefit. Trade-off: larger single payload (~5–15 KB) vs multiple smaller requests.
* **System Impact:** The JSON Schema validator (ADM-02) must validate the entire payload atomically. Partial payloads are rejected. Max file size capped at 5 MB to prevent abuse.

---

### [Architectural & Design Choice] Internal ATM Registry (Not in Ingestion Payload)
* **Context / Decision:** Synapse maintains its own geospatial ATM registry (ADM-09), queried after mule position estimation in Stage 3b. ATM data is NOT included in the ingestion payload.
* **Why This Over Alternatives:** v1.0.0-RC1 included `candidate_atms` in the payload, which created a circular dependency: the upstream system would need to know the mule's location (which Synapse computes) to filter ATMs by radius. An internal registry eliminates this dependency. The upstream system only provides incident + mule data; Synapse handles all spatial reasoning. Alternative: sending ALL ATMs in the mule's district was rejected because it inflates payload size (potentially 500+ ATMs per district) and shifts spatial logic to the upstream system.
* **System Impact:** Requires ADM-09 (ATM registry CRUD interface). Requires pre-loading ATM data before the pipeline can run. For MVP, this is an in-memory JSON file loaded at server startup. For production, this would be PostGIS with a spatial index.

---

### [Architectural & Design Choice] Haversine Radius Query Instead of ST-DBSCAN
* **Context / Decision:** Stage 3b uses a simple haversine distance formula to find ATMs within a configurable radius of the mule's estimated position, not ST-DBSCAN clustering.
* **Why This Over Alternatives:** ST-DBSCAN (Spatial-Temporal Density-Based Clustering) is designed for finding dense clusters of *moving* points over time — e.g., tracking vehicle fleets or migratory patterns. ATMs are fixed, stationary coordinates. Applying ST-DBSCAN to one estimated mule point + a grid of static ATMs with `min_samples=1` degenerates into a radius query with extra overhead. Haversine radius query is O(n) over the ATM registry, conceptually honest, and trivially debuggable. ST-DBSCAN could be revisited if Synapse later ingests mule *trajectory* data (a time-series of location pings showing movement direction), where clustering the trajectory's projected endpoint against ATMs would be meaningful.
* **System Impact:** Simpler implementation, no `scikit-learn-extra` dependency for ST-DBSCAN. The multi-factor risk scoring formula (Step 3c) is the real analytical workhorse — the radius query is just candidate retrieval.

---

### [Architectural & Design Choice] Mule Position Estimation Priority Cascade
* **Context / Decision:** Mule location is estimated via a 4-level priority cascade: (1) COMBINED cell+IP, (2) CELL_TOWER_TRILATERATION only, (3) IP_GEOLOCATION only, (4) IFSC_BRANCH_FALLBACK. The system uses the highest-priority method for which data is available.
* **Why This Over Alternatives:** A single fixed method would fail when data is unavailable (cell tower data requires CDR authorization; IP data depends on recent banking sessions). The cascade ensures graceful degradation. An alternative "require all data" approach was rejected because it would reject most real-world payloads (cell tower is rarely available in the Golden Hour). The cascade also enables the confidence cap mechanism: IFSC_BRANCH_FALLBACK gets capped at 0.75 because its accuracy is inherently poor.
* **System Impact:** Every pipeline output includes `mule_location_method` for auditability. The method used directly affects confidence scoring (IFSC fallback caps at 0.75). For MVP, priority level 3 (IP_GEOLOCATION) is the expected primary path.

---

### [Architectural & Design Choice] Dual-Gate Golden Hour Admission (v1.2 FIX 3D update)
* **Context / Decision:** Incident admission requires passing two gates: Gate 1 (Fraud Recency) computes `T_latest = max(txn_timestamp for all t in fund_flow.transactions)` and checks `NOW() − T_latest ≤ 120 min`. Gate 2 (Payload Freshness) checks `NOW() − complaint_timestamp ≤ 240 min`. `[v1.2 FIX 3D]` Gate 1 now explicitly scans ALL transactions to find the maximum timestamp, rather than using the fragile `transactions[-1]` array index.
* **Why This Over Alternatives:** v1.1.0 used `fund_flow.transactions[-1].txn_timestamp` which assumes the last element in the JSON array is the most recent transaction by time. In branched/fan-out fund flows, this is NOT guaranteed — the JSON array may be serialized by branch order, hop_index order, or arbitrarily. Example failure: in a fan-out flow `[hop1, hop2, hop4, hop3b]`, `transactions[-1]` returns `hop3b` (01:14:22) instead of the actual latest `hop4` (01:18:07) — a 4-minute error that could incorrectly fail Gate 1. Using `max()` is O(n) but n ≤ 20 (max hops), so negligible cost for guaranteed correctness. v1.0.0-RC1's single gate (`NOW() − complaint_timestamp ≤ 120 min`) had the additional loophole of allowing stale fraud with fresh complaints. The dual-gate approach with max-timestamp scanning addresses both issues.
* **System Impact:** Gate 1 failure → `GOLDEN_HOUR_EXPIRED` (funds likely cashed out). Gate 2 failure → `STALE_PAYLOAD` (route to manual review). Both gates must pass before the ML pipeline processes the incident. The `max()` computation adds negligible overhead (microseconds for ≤ 20 transactions). Implementation must NOT assume any ordering of the transactions array.

---

### [Architectural & Design Choice] Mule Viability Filter Before Prediction
* **Context / Decision:** After identifying the terminal mule in Stage 1, a viability filter checks: (1) `linked_card_number_hash` is not null, (2) `account_type` is ATM-withdrawal-capable, (3) `mule_bank` is not in the NON_ATM_BANKS exclusion list. Non-viable mules produce `NO_VIABLE_ATM_MULE` and skip the pipeline.
* **Why This Over Alternatives:** v1.0.0-RC1 assumed every leaf node with out-degree 0 was an ATM cash-out candidate. In reality, funds may have moved to crypto exchanges, digital wallets, or banks without ATM networks. Predicting ATM behavior for a non-ATM-capable account would produce meaningless results and waste intervention resources. The filter runs *before* Stage 2 and Stage 3, preventing wasted computation. An alternative "score penalty" approach (let non-viable mules proceed but penalize their confidence) was rejected because it would still produce ATM rankings for accounts that *cannot* use ATMs — misleading operators.
* **System Impact:** Adds a `NO_VIABLE_ATM_MULE` exit path from the pipeline. These incidents are logged, surfaced in View A KPIs (viability rejection rate), and routed to CFCFRMS manual review. The NON_ATM_BANKS list must be maintained (initially: Paytm Payments Bank, Fino Payments Bank, Airtel Payments Bank, Jio Payments Bank, India Post Payments Bank).

---

### [Architectural & Design Choice] Webhook Retry Policy
* **Context / Decision:** Outbound webhooks to bank switches use exponential backoff: max 3 retries, base delay 5 seconds (5s → 10s → 20s). Non-2xx after all retries triggers an alert.
* **Why This Over Alternatives:** Fire-and-forget (no retry) was rejected because a transient 503 from the bank would silently drop a critical card hold. Immediate retry (no backoff) was rejected because it risks overwhelming a bank switch that's already under load. Linear backoff (5s, 10s, 15s) was considered but exponential is industry standard for API resilience and less aggressive under sustained failure. More than 3 retries was rejected because the Golden Hour is time-critical — spending > 35s on retries delays the pipeline for other incidents.
* **System Impact:** Worst-case webhook latency is ~38 seconds (initial + 5s + 10s + 20s + 3s processing overhead). If all retries fail, the incident is marked `WEBHOOK_FAILED` and an alert is raised. The card hold is NOT issued, so the mule's card remains active — this is a known risk that requires operational monitoring.

---

## Category 3: Engineering Trade-off

---

### [Engineering Trade-off] MVP Uses Analytical Baseline, Not Trained GBR
* **Context / Decision:** For MVP / hackathon, Stage 2 drain time uses the deterministic formula `D̂ = ⌈B_accessible / W_txn⌉ × 4.5` instead of a trained GradientBoostingRegressor model.
* **Why This Over Alternatives:** Training a GBR requires: (a) a complete synthetic dataset with labeled mule trajectories, (b) feature engineering and hyperparameter tuning, (c) model validation (RMSE ≤ 12 min target). This is achievable but not within a 24–48 hour hackathon sprint where the priority is demonstrating end-to-end data flow. The analytical baseline is deterministic, explainable, zero-dependency (no .joblib model artifact), and produces reasonable estimates. Trade-off: baseline predictions don't vary by time-of-day, mule behavioral profile, or fund flow velocity — they're purely balance-driven.
* **System Impact:** MVP drain time predictions are coarser. Post-MVP, the GBR replaces the baseline. The `drain_time_remaining_minutes` output format is identical regardless of method, so downstream consumers (View B, webhook) are unaffected by the swap.

---

### [Engineering Trade-off] IP Geolocation as MVP Primary Location Method
* **Context / Decision:** MVP relies on IP geolocation (from banking app sessions) as the primary mule location method. Cell tower trilateration is documented as Future State.
* **Why This Over Alternatives:** Real-time cell tower data requires CDR/LIS authorization from telecom operators, which in India takes hours to days under current law enforcement processes and is incompatible with Golden Hour latency. IP geolocation is available instantly because banks can provide the IP address from the mule's most recent mobile/net-banking session. Trade-off: IP geolocation accuracy is typically city-level (1–5 km error) vs cell tower trilateration (200m–1km error). This means the mule position estimate has a wider uncertainty ring, which is surfaced in View B (2 km accuracy ring for IP geo vs 500m for cell tower).
* **System Impact:** MVP ATM rankings are based on coarser location data. The confidence cap for IFSC fallback (0.75) provides a safety net if even IP data is unavailable. Post-MVP, cell tower integration improves position accuracy and enables the COMBINED method.

---

### [Engineering Trade-off] In-Memory JSON ATM Registry for MVP (v1.2 FIX 2-clarify update)
* **Context / Decision:** The internal ATM registry is loaded from a static JSON file into memory at server startup, not stored in a persistent geospatial database (PostGIS). `[v1.2 FIX 2-clarify]` The production path to supporting ≥ 50,000 ATMs at ≤ 100ms query latency (PRD ADM-09) requires either **vectorized NumPy haversine** (broadcasting `np.sin`, `np.cos`, `np.arcsin` over contiguous arrays — sub-millisecond for 50K points) or **PostGIS with `ST_DWithin`** (R-tree spatial index). The brute-force Python-loop approach is strictly MVP-scoped for 200 ATMs.
* **Why This Over Alternatives:** PostGIS provides R-tree spatial indexing for fast radius queries on large datasets, but requires PostgreSQL setup, schema migration, and connection pooling — overhead that delays MVP delivery. An in-memory JSON array with a brute-force haversine scan is O(n) per query but n = 200 ATMs for MVP, making it effectively instant (< 10ms). Trade-off: no persistence across restarts (server restart reloads from file), no concurrent write safety, and the Python-loop implementation won't scale beyond ~10K ATMs without noticeable latency. **However**, the same in-memory approach with NumPy vectorized haversine (no PostGIS) can handle 50K ATMs in < 1ms — this is the recommended intermediate production path before a full PostGIS migration.
* **System Impact:** ATM registry changes (ADM-09) in MVP require editing the JSON file and restarting the server. **Production scaling path:** (1) Immediate: vectorize haversine with NumPy arrays — handles 50K ATMs, meets 100ms SLA with no infrastructure change. (2) Long-term: migrate to PostGIS for persistence, concurrent writes, and CRUD UI support. The PRD ADM-09 acceptance criteria ("≤ 100ms for 5 km radius on 50K records") is a **production** target; the MVP's 200-ATM brute-force scan is explicitly out of scope for this benchmark.

---

### [Engineering Trade-off] Synthetic Data Only — No Real Data in Any Environment
* **Context / Decision:** All development, training, testing, and demonstration use synthetic datasets generated via AMLSim + PaySim + custom spatial generators. No real PII, account numbers, transaction data, or telco data is used in any environment.
* **Why This Over Alternatives:** Real CFCFRMS / NCRP / banking data is classified (MHA restricted). Using real data would require security clearance, air-gapped environments, and data handling protocols incompatible with hackathon/development velocity. Synthetic data allows rapid iteration, public demo, and open collaboration. Trade-off: synthetic distributions may not perfectly match real fraud patterns — model performance in production may differ from synthetic validation metrics. Mitigated by Platt scaling recalibration once anonymized outcome statistics become available.
* **System Impact:** All performance metrics (RMSE, FP rate, Top-3 hit rate) are measured against synthetic data. Production deployment requires a recalibration phase with real (anonymized) outcome data before metrics can be trusted.

---

## Category 4: Domain & Behavioral Assumption

---

### [Domain & Behavioral Assumption] Mule ATM Withdrawal Behavior Profile
* **Context / Decision:** Terminal mules are assumed to: (a) begin ATM withdrawals within 15–30 minutes of receiving funds, (b) withdraw the maximum per-transaction amount (₹20,000) each time, (c) average 4.5 minutes between successive withdrawals, (d) target 1–3 ATMs per session, (e) respect daily withdrawal limits (cannot exceed ₹1,00,000/day for most banks).
* **Why This Over Alternatives:** This profile is derived from I4C case study summaries (public presentations at CyberCon 2025) and calibrated via AMLSim synthetic trajectories. An alternative "slow drain" profile (small amounts over days) was rejected for Golden Hour scope — Synapse only handles the first 120 minutes, where rapid drain is the dominant pattern. An "unlimited withdrawal" assumption (v1.0.0-RC1) was rejected because it ignores bank-enforced daily caps and produces impossible drain time predictions.
* **System Impact:** Directly parameterizes the drain time formula. If real mules withdraw less than ₹20,000 per transaction (e.g., ₹10,000 to avoid detection heuristics), drain time would be under-predicted and urgency over-estimated. This behavioral parameter should be the first thing recalibrated with real outcome data.

---

### [Domain & Behavioral Assumption] Mule ATM Selection Preferences
* **Context / Decision:** Mules are assumed to prefer ATMs in this priority order: (1) closest to current location, (2) same bank as their account, (3) cash-available (FULL > PARTIAL > LOW), (4) offsite (no security guard / less CCTV), (5) high-traffic (blend in with crowd). These preferences are encoded as the five RiskScore components with weights β1–β5.
* **Why This Over Alternatives:** This preference model is a behavioral hypothesis based on: proximity minimizes exposure time; same-bank avoids interbank fees and has higher limits; cash availability is a practical constraint; offsite ATMs have less surveillance (per I4C presentations); high-traffic ATMs provide anonymity. An alternative "random walk" model (mule picks any nearby ATM with equal probability) was rejected because it ignores documented behavioral patterns. A "lowest surveillance" model (offsite-dominant) was rejected because proximity is empirically stronger — mules prioritize speed over stealth.
* **System Impact:** If any assumption is wrong (e.g., mules actually prefer onsite ATMs near bank branches for perceived legitimacy), the corresponding β weight can be adjusted via ADMIN threshold configuration in production. The modular scoring formula allows individual component recalibration without redesigning the pipeline.

---

### [Domain & Behavioral Assumption] Telecom CDR/LIS Latency in India
* **Context / Decision:** Real-time cell tower location data for a suspect's mobile device is assumed to be **unavailable** within the Golden Hour window under current Indian law enforcement processes. CDR (Call Detail Record) and LIS (Lawful Interception System) requests require police authorization and typically take hours to days for telecom operators to process.
* **Why This Over Alternatives:** Some countries (US E911, EU eCall) have near-real-time location APIs for emergency services. India's telecom regulatory framework does not currently mandate such APIs for law enforcement cyber fraud use cases. Assuming real-time availability would produce a system that cannot function in production. The pipeline is designed to work without cell tower data (IP geolocation fallback) and treat cell tower integration as a Future State dependent on telecom API partnerships or regulatory changes (e.g., via DoT mandate under IT Act amendments).
* **System Impact:** `cell_tower_cluster` is optional (`minItems: 0`) in the schema. MVP payloads ship with empty arrays. The priority cascade ensures the pipeline never blocks on missing cell tower data. Model training datasets include cell tower data for future readiness, but MVP inference does not depend on it.

---

### [Domain & Behavioral Assumption] NON_ATM_BANKS Exclusion List
* **Context / Decision:** The following banks are assumed to NOT issue physical ATM-capable debit cards and are excluded by the mule viability filter: Paytm Payments Bank, Fino Payments Bank, Airtel Payments Bank, Jio Payments Bank, India Post Payments Bank.
* **Why This Over Alternatives:** Payments Banks under RBI licensing conditions have restrictions on issuing physical debit cards with ATM withdrawal capability (some issue virtual cards only or have very limited ATM networks). Including them as viable ATM mules would generate false predictions. An alternative approach (check card linkage only, ignore bank type) was rejected because a mule account at a payments bank might show a non-null `linked_card_number_hash` (for a virtual card) but still be unable to perform ATM withdrawal. The bank-level exclusion provides a defense-in-depth check.
* **System Impact:** This list is **hardcoded for MVP** and should be maintained as a configurable data table in production. If any listed bank begins issuing ATM-capable cards, they must be removed from the exclusion list. False negatives (real mules at excluded banks) are routed to CFCFRMS manual review — they are not lost, just not handled by Synapse's automated pipeline.

---

### [Domain & Behavioral Assumption] Demo ATM Onsite/Offsite Classification (v1.3 FIX 4D)
* **Context / Decision:** CNRB-ATM-PNE-0042 (Canara Bank, FC Road, Shivaji Nagar, Pune) is classified as `is_onsite: false` (offsite ATM) in the internal ATM registry for the Pune demo payload.
* **Why This Over Alternatives:** The PRD's mock webhook example assigns a risk_score of 0.91 to this ATM. An onsite ATM's theoretical maximum RiskScore is 0.85 (because β₄ × 𝟙[is_onsite=false] = 0.15 × 0 = 0, forfeiting the entire offsite component). Setting `is_onsite: true` made 0.91 mathematically impossible. FC Road Shivaji Nagar is a high-traffic commercial area where offsite ATMs (not attached to a bank branch) are common, so `is_onsite: false` is realistic.
* **System Impact:** Offsite ATMs can score up to 1.0 (all five components maxed). The demo score of 0.91 is now mathematically valid: proximity ≈ 0.26 (1.2 km / ~5 km → ~0.76, × β₁=0.30 ≈ 0.228), bank match = 0.25 (Canara = Canara), cash = 0.20 (FULL), offsite = 0.15, traffic ≈ 0.07 (185 txns, log-normalized). Total ≈ 0.91. All demo ATM records must be verified for onsite/offsite consistency before the demo.

---

### [ML & Algorithmic Heuristic] Proximity Normalization Uses Active Radius (v1.3 FIX 4E)
* **Context / Decision:** The proximity component D_norm uses the **active** search radius r_active (which may be 1.0×, 1.5×, or 2.25× the base r_search after fallback expansions) as the normalization denominator, not the base r_search. Formula: `D_norm = max(0, 1 − d(P, a_j) / r_active)`.
* **Why This Over Alternatives:** Using base r_search (5.0 km urban) as the denominator produces negative D_norm values for ATMs found during radius expansion. Example: ATM at 6.2 km found when r_active = 7.5 km (1.5× expansion). With base r_search: `1 − 6.2/5.0 = −0.24` (negative, corrupts RiskScore). With r_active: `1 − 6.2/7.5 = 0.17` (correct, ATM is in the outer ring). The `max(0, ...)` clamp provides defense-in-depth against floating-point edge cases. Alternative: normalize all ATMs against a fixed global maximum distance. Rejected because it would make proximity scores meaningless in low-density areas where the search radius expands to 33.75 km (2.25 × 15 km rural).
* **System Impact:** ATMs found only via radius expansion will have lower D_norm values (they are farther away, so proximity should contribute less to their score). This is the correct behavior — expansion ATMs are backup candidates, not primary targets. The `max(0, ...)` clamp guarantees RiskScore ∈ [0, 1] regardless of expansion level.

---

### [Engineering Trade-off] Non-ATM Bank Dual Identification (Name + IFSC Prefix)
* **Context / Decision:** In Stage 1 (`/core/graph.py`), `is_non_atm_bank` checks both the bank name substring (e.g. "Paytm", "Fino", "Airtel", "Jio", "India Post") and the IFSC prefix (first 4 characters: `PYTM`, `FINO`, `AIRP`, `JIOP`, `IPOS`).
* **Why This Over Alternatives:** Relying solely on bank name string matching is fragile if incoming payloads use variations like "Paytm PB" or "IPPB". Relying solely on IFSC prefix fails if the bank name is present but IFSC is misformatted. Checking both ensures robust exclusion of non-ATM entities in defensive ingestion.
* **System Impact:** Guarantees that any payment bank account in `fund_flow` or `terminal_mule` is reliably intercepted by the viability filter without escaping due to naming variation.

---

### [Engineering Trade-off] Traffic Component Zero-Division Guard
* **Context / Decision:** In Stage 3c (`/core/cluster.py`), if the maximum `daily_avg_txn_count` across candidate ATMs is zero or if the candidate set has zero traffic, `T_traffic` defaults safely to 0.0 rather than evaluating `log(1 + 0) / log(1 + 0)` which produces a divide-by-zero error or NaN.
* **Why This Over Alternatives:** Real or synthetic registries in newly established or rural areas might occasionally report zero transactions for all nearby ATMs. Unhandled division by zero would crash the entire spatial pipeline. Defaulting to 0.0 is mathematically neutral (the traffic anonymity component contributes nothing).
* **System Impact:** Prevents 500 runtime errors and guarantees RiskScore $\in [0, 1.0]$ even in completely unpopulated or zero-activity ATM candidate clusters.

---

### [Architectural & Design Choice] Static IFSC & District Location Fallback Dictionary
* **Context / Decision:** In Stage 3a (`/core/cluster.py`), Priority 4 (`IFSC_BRANCH_FALLBACK`) resolves mule position by first querying a static map of known mock IFSC codes (e.g. `CNRB0002341 -> (18.5204, 73.8567)`), then falling back to district/city centroids (e.g. Pune, Bengaluru, Delhi), and finally defaulting to the geographic center of India.
* **Why This Over Alternatives:** Without an active external RBI database connection, an unmapped IFSC would raise an unhandled `KeyError` or fail position estimation completely. A hierarchical fallback ensures the spatial pipeline never blocks while correctly asserting the `confidence_cap_applied = True` flag (which caps downstream confidence at 0.75).
* **System Impact:** Guarantees that payloads lacking both cell tower and IP telemetry degrade gracefully to branch/city level without crashing the pipeline, while safety-capping composite confidence to avoid tactical physical dispatch on coarse coordinates.

---

### [Architectural & Design Choice] Simulation Mode Reference Clock (`X-Simulation-Mode`)
* **Context / Decision:** `POST /api/v1/ingest` accepts an optional `X-Simulation-Mode: true` header (or `?simulate=true` query param). When present, the Golden Hour gate check uses `payload.ingestion_timestamp` as the reference clock instead of `datetime.now(timezone.utc)`. All downstream pipeline stages (MPS recency, τ elapsed time) also use `ingestion_timestamp` as `T_now`.
* **Why This Over Alternatives:** Static demo payloads have fixed timestamps (e.g., 2026-09-05T01:22:00) baked into `payload_pune.json`. Without simulation mode, Gate 1 (`NOW() − max(txn_timestamp) ≤ 120 min`) would always fail when the real clock is hours/days ahead, making automated `verify_api.py` tests impossible. Alternative — regenerating payloads on every test run — was rejected because it breaks determinism (MPS scores, urgency values, and ATM rankings would change each run). Using `ingestion_timestamp` as the reference is self-consistent: it is the moment the payload declares it entered the system, so all temporal deltas are computed relative to that declared instant rather than wall clock. This approach is also safe in production: a live payload's `ingestion_timestamp` is set at submission time, so the reference clock differs from `NOW()` by only milliseconds.
* **System Impact:** In simulation mode, the pipeline is fully deterministic and reproducible across any machine at any time. Gate failures, τ values, MPS recency scores, and urgency outputs are identical across test runs. The simulation flag is logged and surfaced in `IngestResponse.simulation_mode` for auditability. Must NOT be exposed in production without auth guard (out of scope for MVP).

---

### [Engineering Trade-off] Injectable Webhook HTTP Client (`_set_webhook_client`)
* **Context / Decision:** `_dispatch_webhook()` checks a module-level `_WEBHOOK_HTTP_CLIENT` variable. If non-None, it uses that client's `.post()` for webhook calls instead of creating a real `httpx.Client`. The FastAPI `TestClient` is injected during `verify_api.py` bootstrap so webhook calls route in-process rather than hitting `localhost:8000`.
* **Why This Over Alternatives:** Without injection, `_dispatch_webhook` calls `httpx.Client.post("http://localhost:8000/api/v1/freeze-card-atm")`. During testing, no live server is running, so this raises `[Errno 61] Connection Refused` after 4 attempts, marking every webhook as `FAILED`. Monkey-patching `httpx.Client.post` at the class level was the first alternative tried but is fragile (affects all httpx calls globally, including TestClient internals). Dependency-injecting the client into the endpoint function was considered but would require changing the FastAPI route signature and break the PRD's clean pipeline orchestration model. The module-level injectable is the least invasive option: zero impact on production code paths (default is `None`), and the injection is limited to explicit test setup in `_bootstrap()`.
* **System Impact:** In production (no injection), `_WEBHOOK_HTTP_CLIENT` is `None` and real `httpx.Client` is used. In tests, `TestClient` is injected and webhook calls stay in-process with zero network overhead. The retry mock test (`test_webhook_retry_backoff`) further substitutes a `_MockClient` that returns HTTP 500 twice then 200, proving the exponential backoff logic without any actual HTTP calls.

---

---

### [Architectural & Design Choice] Phase 09 F01 — Gate Check Moved to Endpoint (`ingest_incident`)
* **Context / Decision:** The Golden Hour Gate 1 & Gate 2 checks were previously embedded in `schemas.py`'s `model_validator`. They have been moved into the `ingest_incident()` endpoint function in `api/main.py`.
* **Why This Over Alternatives:** The `model_validator` executes during Pydantic deserialization, before the endpoint function runs — meaning the simulation flag (`X-Simulation-Mode` header / `?simulate=true` query param) is not yet available. The old design worked around this by always using `ingestion_timestamp` as the reference clock regardless of the simulation flag, which was a hidden implicit simulation that obscured the real behaviour. Moving gate checks to the endpoint makes the logic explicit: if `simulation_active`, skip gates; otherwise run them against `datetime.now(UTC)`.
* **System Impact:** Schema validation errors (malformed JSON, wrong field types, missing required fields) still return HTTP 422 via Pydantic as before. Gate failures now also return HTTP 422 (via `HTTPException(422)`) with the same error format the UI's 422-handler already expects. Non-sim mode now correctly uses `datetime.now(UTC)` as both the gate reference and the pipeline reference clock, making τ (elapsed time), MPS recency, and drain time accurate in production.

---

### [Architectural & Design Choice] Phase 09 F01 — Drain Timer Registry (Frontend, Simulation Mode)
* **Context / Decision:** In Simulation Mode, the API always returns the same `drain_time_remaining_minutes` for a given payload (because `T_ref = ingestion_timestamp` is fixed). Without additional logic, re-submitting the same payload or clicking its row in View A would always restart the countdown to the same initial value. A JS `Map` (`_drainTimerRegistry`) keyed on `ncrp_ticket_id` stores `{ submittedAt: Date.now(), initialDrainSeconds }` on first submission. Subsequent calls to `_getSimDrainMinutes(ncrpId, apiDrainMinutes)` compute `remaining = initialDrainSeconds − elapsedSecs` from real wall time.
* **Why This Over Alternatives:** Alternative 1 — use `datetime.now()` as the pipeline reference in sim mode (so the API returns a decreasing drain time on each call). Rejected: demo payloads have timestamps from days ago, so `τ = now() − last_txn` would be enormous and drain_time would be negative/zero immediately. Alternative 2 — store submission time server-side per incident. Rejected: adds server state, complicates the in-memory incident store, and is unnecessary since the frontend already has `Date.now()`. The frontend registry is stateless from the server's perspective and is cleared on simulation mode toggle-off.
* **System Impact:** Timer continuity is fully frontend. The backend remains stateless and deterministic. The registry is cleared when simulation mode is toggled OFF (to avoid stale entries from old sim sessions contaminating live-mode timers). Works correctly for all three timer-entry paths: (1) first ingest via Admin Panel, (2) re-ingest of same payload, (3) clicking a row in View A / switching to View B.

---

> **Note:** This document is a living log. Every new session that makes a technical decision must append to the relevant category following the schema above. Do not delete or modify existing entries — append corrections as new entries referencing the original.

### [Architectural & Design Choice] Phase 09 F02 — Interception Window Zero-State Reason via Stage Detail Parsing

* **Context / Decision:** When `drain_time_remaining_minutes = 0` is returned by the API, the UI must display a contextual reason instead of a generic `00:00`. The known typed reason (`DAILY_LIMIT_EXHAUSTED`) is already embedded in the `STAGE_2_TEMPORAL` stage detail string by `api/main.py` (e.g., `"Drainable: ₹0.00 | τ=3.20 min | DAILY_LIMIT_EXHAUSTED | Urgency=0.0000"`). The UI parses this string via `s2.detail.includes('DAILY_LIMIT_EXHAUSTED')` in `renderViewB()` and passes it as the `preZeroReason` parameter to `startCountdown()`.
* **Why This Over Alternatives:** Alternative — add `daily_limit_exhausted: bool` as a top-level field to `IngestResponse` and propagate it through the incident store. Rejected for Phase 09 F02 because: (1) the information is already available in the `stages` array returned to the UI; (2) adding a field to `IngestResponse` would require updating `api/main.py`, `api/schemas.py`, and all verification scripts that validate response structure — scope creep for a pure UI feature; (3) the string `'DAILY_LIMIT_EXHAUSTED'` is a stable, documented token, not an implementation detail. If the API ever adds a dedicated field, the parsing logic can be replaced without changing the rendered output.
* **System Impact:** Zero backend impact. `startCountdown()` signature changed to `startCountdown(mins, preZeroReason = null)` — backward-compatible (default is `null`). The three contextual states are: `'DAILY LIMIT EXHAUSTED'` (parsed), `'NO WITHDRAWABLE BALANCE'` (generic fallback for other pre-zero cases), `'Window expired — cash-out complete or mule fled'` (live-countdown zero case, hardcoded inside `tick()`).

---

### [Engineering Trade-off] Phase 09 Bug Fix 01 — Async Webhook Dispatch (httpx.AsyncClient)

* **Context / Decision:** `_dispatch_webhook` was a synchronous function using `httpx.Client` (blocking I/O). It was called from `ingest_incident`, which is an `async def` endpoint. Calling blocking I/O directly inside an async function **freezes the event loop** — the single uvicorn event loop cannot accept any new connections while blocked. Since the webhook target is `localhost:8000/api/v1/freeze-card-atm` (self-referential), the server was deadlocked: the ingest handler blocked the loop waiting for the HTTP response, while the loop could not accept the incoming webhook connection. All 4 attempts timed out (10 s each + backoff = ~75 s total). Fixed by replacing `httpx.Client` with `httpx.AsyncClient` + `await`, and `time.sleep` with `asyncio.sleep`.
* **Why This Over Alternatives:** Alternative 1 — use `asyncio.to_thread(_dispatch_webhook)` to offload the sync blocking call to a thread so the event loop remains free. Rejected: adds unnecessary thread overhead when the native async fix (`httpx.AsyncClient`) is available and cleaner. Alternative 2 — change the webhook target to a separate process/port. Rejected: over-engineering for a mock endpoint. The injectable `_WEBHOOK_HTTP_CLIENT` (TestClient, sync) is preserved — the async branch activates only when no test client is injected.
* **System Impact:** `_dispatch_webhook` renamed to `_dispatch_webhook_async`, marked `async def`. Call site in `ingest_incident` gains `await`. `time.sleep` in retry backoff replaced with `await asyncio.sleep` (non-blocking). `asyncio` import added to `api/main.py`. All existing tests pass via the injected TestClient path. Live server now correctly dispatches webhooks on the first attempt.

---

### [Architectural & Design Choice] Phase 09 Bug Fix 02 — Force-Overwrite Drain Timer Registry on New Ingest POST

* **Context / Decision:** The `_drainTimerRegistry` (localStorage-backed) was designed to give re-submitted payloads a continuing countdown rather than restarting from the full drain time. However, the original implementation only **registered on first encounter** — if the same `ncrp_ticket_id` existed in the registry from a prior session persisted in localStorage, calls to `_getSimDrainMinutes` would compute `elapsed = now − old_submittedAt`, which could be hours, yielding a remainder of 0. This caused the INTERCEPTION WINDOW overlay to show `CLOSED` immediately even for an 18.6-min active window. Fixed by **force-overwriting** the registry entry on every new `ingest()` POST, anchoring `submittedAt` to the current wall time.
* **Why This Over Alternatives:** Alternative — clear the entire localStorage registry at page load. Rejected: destroys valid cross-refresh continuity for incidents genuinely submitted earlier in the same operator session. The correct invariant is: a new POST (operator action) always resets the clock for that ticket; a row click or page refresh does not.
* **System Impact:** `ingest()` in `ui/index.html` no longer calls `_getSimDrainMinutes()` (which conditionally registers). Instead it always calls `_drainTimerRegistry.set(...)` + `_saveRegistry()` unconditionally. `renderViewB()` (row-click path) still calls `_getSimDrainMinutes()` which reads without overwriting — cross-refresh continuity preserved.

---

### [Architectural & Design Choice] Phase 09 Bug Fix 03 — Persistent Crew Dispatch Registry (localStorage)

* **Context / Decision:** The `acknowledge()` function updated only the in-memory DOM (button class + text). On any page refresh or row re-click, `renderCards()` rebuilt cards from API-fetched incident data, which has no dispatch state — buttons reverted to "Acknowledge & Dispatch", risking duplicate crew dispatch to the same ATM. Fixed by adding `_dispatchRegistry` (Map) backed by `localStorage` key `synapse_dispatch_registry`. Key format: `"${ncrp_ticket_id}:${atm_id}"` — composite key ensures per-incident, per-ATM granularity. On click, entry is written to registry and persisted. On `renderCards()`, each ATM checks the registry before rendering its button: if dispatched → renders in `done` state with `"✓ Crew Dispatched — #${rank}"`; otherwise → normal dispatch button. Other ATMs' buttons are unaffected.
* **Why This Over Alternatives:** Alternative — add dispatch state to the backend `_incidents` store and serve it via `GET /api/v1/incidents`. Rejected for MVP: adds server state mutation, complicates the incident schema, requires a new `PATCH` endpoint — scope creep for a frontend UX concern. The backend already fired the webhook; dispatch acknowledgement is purely an operator UI record. Alternative — persist dispatch state in the frontend `_incidents` array. Rejected: array is overwritten on every `pollIncidents()` call (~5 s), losing state immediately.
* **System Impact:** `_dispatchRegistry` Map + `_saveDispatchRegistry()` / `_loadDispatchRegistry()` added to `ui/index.html`. Boot sequence updated: `_loadDispatchRegistry()` called at `DOMContentLoaded`. `acknowledge()` updated: saves to registry, logs ISO timestamp of dispatch. `renderCards()` updated: reads registry per ATM card, conditionally renders button class/text/onclick. Entries intentionally never cleared — dispatch records are permanent operational logs.

---

### [Architectural & Design Choice] Phase 09 Feature 03 — payload_snapshot: One-Time Snapshot vs. Live Reference

* **Context / Decision:** The Incident Intelligence Panel in View B requires rich payload data (fund flow transactions, terminal mule account details, complainant metadata, IP cluster) which the `IngestResponse` schema did not previously carry. Two approaches were considered: (A) store the entire original `IncidentPayload` object in the `_incidents` list, or (B) store a curated `payload_snapshot` dict with only the fields needed by the UI.
* **Why This Over Alternatives:** Approach A stores PAN-adjacent data (`linked_card_number_hash`) in the incidents endpoint which is polled frequently and broadly. Approach B allows intentional exclusion of sensitive fields at the point of extraction. `payload_snapshot` is populated once at ingest time from the original payload object before it leaves scope — it is a deliberate denormalization, not a live reference. If the upstream data changes post-ingest (planned for Phase 10 live updates), the snapshot will not auto-update; a new endpoint will be responsible for patching specific snapshot fields.
* **System Impact:** `payload_snapshot: Optional[Dict[str, Any]] = None` added to `IngestResponse` in `api/main.py`. Populated immediately after `result` is constructed, before `_incidents.append(result.model_dump())`. `complainant_name: Optional[str] = None` added to `NCRPTicket` in `api/schemas.py` with `getattr` fallback in main.py so legacy payloads without the field parse to `None` → UI renders `N/A`. Frontend `renderIntelPanel()` hides the panel entirely when `payload_snapshot` is absent.

---

### [Operational / Communication Design] Phase 09 Bug Fix 04 — Three-State Interception Window Zero-State Logic

* **Context / Decision:** When `drain_time_remaining_minutes = 0`, the dashboard previously showed one of two messages: `DAILY LIMIT EXHAUSTED` (detected from stage detail string) or `NO WITHDRAWABLE BALANCE` (catch-all fallback). The Delhi payload exposed a critical third case: `drain_time = 0`, `drainable_today_inr = ₹4,22,140`, `Urgency = 1.0` — funds fully accessible, but the temporal model's settlement/travel window had elapsed (τ ≥ window). The catch-all message `NO WITHDRAWABLE BALANCE` was factually wrong and operationally dangerous, implying no threat when the mule may already be at the ATM.
* **Why Three States:** Each zero-state carries a fundamentally different operational implication and requires a different officer response: (1) `DAILY LIMIT EXHAUSTED` → mule cannot withdraw more today; threat is deferred to tomorrow; lower urgency. (2) `WINDOW ELAPSED — MULE MAY BE AT ATM` → time ran out but money is accessible; **this is the highest-urgency state** — dispatch immediately, ATM is the target NOW. (3) `NO WITHDRAWABLE BALANCE` → balance or drainable amount is ≤ ₹100; no financial threat remains. Collapsing (2) into (3) suppresses the alarm for the most time-critical scenario.
* **System Impact:** `preZeroReason` logic in `renderViewB()` (`ui/index.html`) now checks: `DAILY_LIMIT_EXHAUSTED` in stage detail → case 1; `drainable_today_inr > 100` → case 2 (`WINDOW ELAPSED — MULE MAY BE AT ATM`); else → case 3. Threshold of ₹100 is chosen to exclude floating-point near-zero values while remaining below any meaningful withdrawal amount.

---

### [Architectural & Design Choice] Phase 10 Feature 01 — Single-Server Architecture for Both Portals

* **Context / Decision:** The Bank Feed Simulator was initially conceived as running on a separate port (e.g., `8001`) via `python3 -m http.server`. This caused two problems: (1) The static server served `ui/index.html` as the default index, so visiting `localhost:8003/` loaded the dashboard not the feed portal; users had to manually navigate to `/feed.html`. (2) Serving from a different origin caused the feed portal to make cross-origin API calls that could hit CORS issues. Fixed by adding `GET /feed` to the FastAPI server, serving `ui/feed.html` from the same origin as all API routes.
* **Why This Over Alternatives:** Alternative — add an `index.html` symlink or redirect in the `ui/` folder. Rejected: brittle, requires filesystem manipulation per deployment. Alternative — configure CORS headers specifically for the static server origin. Rejected: adds per-origin configuration maintenance and still requires two running processes. Single-server approach is simpler, more correct, and requires only one `uvicorn` command.
* **System Impact:** `GET /feed` route added to `api/main.py` alongside `GET /` (dashboard). `_UI_FEED` path constant already existed (`_PROJECT_ROOT / "ui" / "feed.html"`). No changes to feed.html required. Deployment is now: `uvicorn api.main:app --reload --port 8000` → open `localhost:8000` (dashboard) and `localhost:8000/feed` (simulator) in two tabs.

---

### [Engineering Trade-off] Phase 10 Feature 02 — Client-Side ingestion_timestamp Mutation for Sim Mode

* **Context / Decision:** In simulation mode, the interception window countdown must start from the moment of upload, not from the `ingestion_timestamp` field embedded in the saved JSON payload file (which is typically hours or days old). Two approaches: (A) mutate `ingestion_timestamp` client-side in the feed portal before POST, or (B) add a server-side parameter (e.g., `?stamp_now=true`) that causes the backend to override the timestamp.
* **Why This Over Alternatives:** Approach B would add a new backend code path and potentially obscure what timestamp the backend actually used (making debugging harder). Approach A is transparent: the payload the backend receives already has the correct timestamp — the backend's existing sim mode logic (`T_ref = ingestion_timestamp`) works without any modification. The mutation is logged to the Feed Activity Log so the operator can verify exactly what timestamp was stamped.
* **System Impact:** `submitPayload()` in `ui/feed.html` checks `_simMode` before POST. If true: `body.ncrp_ticket.ingestion_timestamp = new Date().toISOString()`. The mutation happens in the JavaScript object copy — the original textarea content is not modified (clearing and re-submitting would re-read the original file timestamp). Submission tagged `[SIM]` in activity log.

---

### [Architectural & Design Choice] Phase 10 Feature 03 — Backend-Resident Resolution State

* **Context / Decision:** Case resolution state needed to be visible to both the Synapse dashboard and the Bank Feed Simulator simultaneously. Two approaches: (A) store resolution in browser `localStorage` (like dispatch registry), or (B) store resolution in the backend `_incidents` list and serve it via `GET /api/v1/incidents`.
* **Why This Over Alternatives:** Approach A cannot be shared between two different browser origins (even on the same port, different tabs can have different `localStorage` states). Approach B leverages the existing poll loop — both portals poll `GET /api/v1/incidents` every 5 seconds and will see the resolved flag within one poll cycle. Resolution is a meaningful state change that should be authoritative (backend) rather than advisory (frontend).
* **System Impact:** `POST /api/v1/incidents/{ncrp}/resolve` mutates `_incidents` entry in place, adding `resolved`, `resolved_at`, `resolution_reason`, `resolution_note`. `POST /api/v1/incidents/{ncrp}/unresolve` removes these fields (safety valve). `GET /api/v1/incidents` returns these fields naturally since it serializes `_incidents` directly. Both portals react within 5 s: dashboard's `renderTable()` filters `resolved` incidents to the Resolved Cases section; feed portal's `rebuildSelect()` filters them from the dropdown.

---

### [Engineering Trade-off] Phase 10 BF05 — Poll-Time Drain Timer Anchoring

* **Context / Decision:** The `_drainTimerRegistry` anchors the interception window countdown to a wall-clock timestamp. Previously it was populated only at (1) dashboard admin panel submission and (2) lazy first row-click via `_getSimDrainMinutes()`. This meant incidents submitted via the Bank Feed Simulator had no registry entry until clicked — page refresh reset the countdown to the full initial drain value. Fix: `pollIncidents()` auto-registers every newly discovered incident immediately, anchoring `submittedAt = Date.now()`.
* **Why This Over Alternatives:** Alternative — register at row-click time only (existing behavior). Problem: if user refreshes before clicking the row, registry was never populated. Alternative — require the Bank Feed Simulator to also write to `localStorage`. Problem: different origins share `localStorage` only on same-origin — this is fragile and couples the portals. Poll-time registration is the most reliable approach: `pollIncidents()` runs unconditionally every 5 seconds regardless of which portal submitted or whether any row has been clicked.
* **System Impact:** `pollIncidents()` in `ui/index.html` now iterates `fresh` incidents before `renderTable()`. For each incident where `ncrp_ticket_id` is not in `_drainTimerRegistry` and `drain_time_remaining_minutes != null`, registers `{ submittedAt: Date.now(), initialDrainSeconds: Math.round(drainMins * 60) }` and sets `registryDirty = true`. After iteration, calls `_saveRegistry()` once if any new entries were added (batched write). Error delta: ≤5 s from true submission time (one poll cycle latency) — acceptable for a countdown measured in minutes.

---

### [Domain & Behavioral Assumption] Phase 10 Feature 05 — Automatic Simulated Withdrawal

* **Context / Decision:** When an incident arrives with an interception window that is already exhausted (`drain_time_remaining_minutes <= 0`), the system immediately simulates an automated natural ATM withdrawal for the scammer at the point of ingestion.
* **Why This Over Alternatives:** If the window is already zero, the scammer is presumed to have withdrawn funds at an ATM. We assume scammers naturally withdraw in increments of ₹10,000 brackets (simulated amount: `floor(drainable_today_inr / 10000) * 10000`). If the window is still open (`> 0`), we explicitly *bypass* this logic and do nothing, allowing the police and human operators time to intervene before the window naturally elapses. We initially explored spinning up an `asyncio` delayed background task for cases where `> 0`, but decided against it to preserve operator workflow integrity.
* **System Impact:** Directly mutates `withdrawals_today_inr` and `balance_inr` in the incident's `payload_snapshot` during ingestion. Downstream UI components immediately reflect the post-withdrawal balance without needing manual simulation via the Bank Feed Simulator.

---

### [Architectural & Design Choice] Phase 11 Feature 01 — Digital Lien Management System & UI Sync

* **Context / Decision:** A fully persistent, backend-backed Digital Lien management system was implemented. The "Initiate" button fires a webhook to mock `/api/v1/freeze-card-atm`, while the "Revoke" button cancels the lien with a reason. The UI accurately reflects this state across duplicate accounts (like Hop 1 receiver and Hop 2 sender) and survives page reloads. A periodic poll (`fetchLienRegistry()` inside `pollIncidents()`) syncs the UI with the backend `data/lien_registry.json`. If the pipeline automatically fires a webhook based on confidence >= 0.70, it seeds the lien registry so the terminal mule immediately shows a "Revoke Digital Lien {Pipeline}" state.
* **Why This Over Alternatives:** 
  - (1) **Backend Persistence (`data/lien_registry.json`) vs `localStorage`:** A `localStorage` approach would only persist state for a single operator's browser session. Real-world incident management requires all operators to see the same lien state. The backend JSON file serves as the source of truth.
  - (2) **Polling vs WebSockets:** WebSockets would provide real-time updates but require significant architectural changes. Since the dashboard already polls `GET /api/v1/incidents` every 5 seconds, piggybacking a `GET /api/v1/liens` call into the same polling loop is cheap, reliable, and provides near real-time sync without added infrastructure complexity.
  - (3) **Pipeline Seeding vs Manual Only:** The ML pipeline's automated webhooks (`PRIMARY_DIGITAL`) perform the exact same action as the manual "Initiate" button. If the pipeline didn't seed the registry, operators would see a disconnected state (funds frozen automatically, but UI says "Initiate"). Seeding the registry during automatic dispatch unifies automated and manual actions.
* **System Impact:** Replaced frontend-only mock toggles with robust backend logic. `api/main.py` now includes `/api/v1/liens` (GET and POST). The pipeline function `_dispatch_webhook_async` was updated to append an automatic entry into the lien registry. `ui/index.html` gained `fetchLienRegistry()` and `updateLienButtonsUI()` diffing logic to minimize DOM thrashing during the 5s poll.

---

### [Engineering Trade-off] Phase 13 Point 1 — FileLock over Database Migrations

* **Context / Decision:** We implemented the `filelock` package (`with FileLock("...lock"):`) around all our JSON-based persistence layers (`lien_registry.json`, `sent_crew.json`, `incidents.json`) to prevent concurrent write corruption from multiple tactical officers.
* **Why This Over Alternatives:** For a hackathon MVP, migrating the entire application state to a proper database (like PostgreSQL with Row-Level Locking) would require significant setup, migrations, and dockerization overhead. `filelock` is a highly robust, zero-configuration primitive that provides cross-process thread safety directly on the filesystem. It secures data integrity while maintaining our "zero-dependency, ultra-fast local deployment" design pillar.
* **System Impact:** The system is now concurrent-safe for multi-officer access. The latency cost of acquiring a lock is microseconds, completely negligible.

---

### [Engineering Trade-off] Phase 13 Point 2 — MVP Operator Identity over Full RBAC

* **Context / Decision:** We have decided to implement a manual `Operator ID` input field on state-mutating UI modals (Resolve, Lien Initiate, Lien Revoke) instead of a full Role-Based Access Control (RBAC) and JWT Authentication system as originally outlined in PRD 2.3.
* **Why This Over Alternatives:** Full authentication (RBAC + TOTP + Session Management) is a massive scope creep for a 48-hour hackathon MVP and distracts from the core ML/fraud interception problem. However, allowing anonymous, untraceable account freezing is unacceptable. By enforcing an explicit `operator_id` parameter on critical API endpoints, we satisfy the "Accountability/Audit Log" requirement without the heavy infrastructure of an auth server.
* **System Impact:** Hardcoded strings (`"handler"`) are replaced with traceable, user-submitted IDs in all audit registries. If left empty, the API strictly rejects the request via 422 Unprocessable Content.

---

### [Architectural & Design Choice] Phase 13 — Lien Initiation via Modal, Not Browser `prompt()`

* **Context / Decision:** The "Initiate Digital Lien" flow uses a custom glassmorphism modal (`#lien-initiate-modal`) to collect the mandatory reason and confirm the action. The previous implementation used the browser's native `prompt()` dialog.
* **Why This Over Alternatives:** `prompt()` is a synchronous, blocking browser API that cannot be styled, does not integrate with the UI's design system (dark theme, monospace fonts, glassmorphism cards), and provides no room for validation feedback or a loading state during the async webhook dispatch. The custom modal matches the existing Revoke modal design (indigo accent vs. red accent), preserves the `_initiateTarget` context across the async flow, and shows inline error messages if the reason is blank or the server returns an error. Consistency across the two destructive-action modals (Initiate = indigo, Revoke = red) also aids operator muscle memory.
* **System Impact:** `toggleLien()` no longer calls `prompt()`. Instead, it stores the target context in `_initiateTarget` and shows the modal. `submitLienInitiate()` handles the fetch, error display, and post-action re-render. The Operator ID check is moved inside `submitLienInitiate()` (was previously a guard before `prompt()`), so the flow is: click button → modal opens → fill reason → confirm → webhook fires → panel re-renders.

---

### [Architectural & Design Choice] Async Lock on Lien INITIATE (Single-Process Constraint)

* **Context / Decision:** `_LIEN_LOCK` (`asyncio.Lock()`) wraps the check→webhook→write sequence in lien INITIATE. REVOKE is intentionally not wrapped — it has no `await` between its duplicate check and its write, making it already atomic under asyncio's cooperative scheduling. Locking REVOKE would add overhead with no race to close.
* **System Impact:** This lock is effective only while the server runs as a single process (`uvicorn --workers 1`). Multiple workers would each hold a separate lock instance and a separate in-memory `_LIEN_REGISTRY`, silently reintroducing the race. The Phase 10 single-server architecture decision must remain in force — do not add worker processes without revisiting this.

---

### [Architectural & Design Choice] `resolved = True` is Reserved for Human Confirmations Only

* **Context / Decision:** The `resolved` field on an incident is set to `True` exclusively by `resolve_incident()` — the human-triggered close path. The 30-day administrative sweeper deliberately does not set `resolved`; it sets only `status = "ADMINISTRATIVELY_CLOSED"` and `synapse_disengaged_at`.
* **Why This Over Alternatives:** Treating a system timeout the same as a human confirmation would mislead any future consumer of the incidents store: audit logs would show a machine-generated outcome as operator-confirmed, NCRP intimation triggers would fire as if a resolution was reached, and ML training pipelines would label a default timeout as a ground-truth case closure. `resolved = True` must only ever mean "a human looked at this and confirmed an outcome."

---

### [Architectural & Design Choice] Resolve Idempotency Guard

* **Context / Decision:** `resolve_incident()` rejects (HTTP 409) any resolve call on an already-resolved incident, instead of silently overwriting `resolution_reason`, `operator_id`, `resolved_at`, and re-firing the NCRP intimation webhook.
* **Why This Over Alternatives:** Without this guard, the same ticket could be resolved twice with conflicting reasons — sending NCRP two different "resolved" intimations for one case with no record that an overwrite happened. The audit log would show only the most recent state, erasing the first resolution entirely.
* **System Impact:** Changing a resolution now requires an explicit `/unresolve` call first — a deliberate two-step, both logged with operator identity — rather than an implicit silent overwrite. This makes resolution changes auditable by design.

---

### [Architectural & Design Choice] Bank Feed Simulator Resolution Modal & Shared Operator Identity
* **Context / Decision:** In `ui/feed.html`, the case resolution flow opens a custom modal (`#resolve-modal`) allowing operators to pick from the 5 standard Synapse resolution taxonomy reasons (`FUNDS_FROZEN`, `MULE_APPREHENDED`, `FUNDS_RECOVERED`, `WINDOW_ELAPSED_CASE_CLOSED`, `FALSE_POSITIVE`) and provide an optional note. The modal reads and synchronizes the operator identity from `localStorage.getItem('synapse_operator_id')`, which is shared with the main dashboard (`ui/index.html`). If blank, resolution submission is prevented with an inline validation prompt. The officer note textarea uses an empty initial value with placeholder hint text (reset on modal open) rather than seeded inner content, ensuring that unedited resolutions never submit canned boilerplate.
* **Why This Over Alternatives:** 
  - (1) **Taxonomy Selection vs Hardcoding:** Hardcoding a default reason like `'FUNDS_FROZEN'` violated the domain model and corrupted NCRP intimation records with incorrect resolution statuses. Exposing the full 5-choice taxonomy guarantees audit fidelity regardless of which interface executes the resolution.
  - (2) **Shared LocalStorage vs Isolated Identity:** Using the identical `synapse_operator_id` key allows an operator authenticated or identified in Synapse to resolve cases in the simulator without re-typing their badge or ID, while still allowing in-place edits that persist back to the main dashboard.
  - (3) **Placeholder Hint vs Seeded Textarea Content:** Pre-filling `<textarea>Resolved via Bank Feed Simulator</textarea>` risked polluting case logs with boilerplate masquerading as specific operational facts whenever an operator submitted without manually editing the note. Using a placeholder attribute ensures the field is only populated when an operator deliberately inputs a note.
* **System Impact:** Closes BUG-001 by supplying the required `operator_id` string to `POST /api/v1/incidents/{ncrp}/resolve`, eliminating HTTP 422 errors while preventing arbitrary or hardcoded resolution outcomes and preserving case note integrity.

---

### [Architectural & Design Choice] Dual-Mode Synthetic Payload Generation Architecture
* **Context / Decision:** We implemented a two-pronged synthetic generator architecture in `synthetic/`: (1) `synthetic/generator.py` for high-volume automated and interactive batch generation (`payload_1.json` ... `payload_N.json`, default 50) with multi-dimensional randomization; and (2) `synthetic/generator_custom.py`, an interactive CLI wizard that prompts for every incident attribute individually, allowing operators to specify custom test edge-cases while pressing `<Enter>` on any prompt to auto-randomize that specific field. Both generators were strictly restricted to the cities present in Synapse's ATM registry (`data/atm_registry.json`): Pune (MH), Bengaluru (KA), and Delhi (DL), eliminating non-registry cities (Chennai, Mumbai, Hyderabad, Kolkata) that caused geospatial candidate lookup failures. Furthermore, elapsed time $\tau$ was calibrated as $\tau = T_{\text{session}} - D_{\text{target}}$ where $D_{\text{target}} \in [0.25, 0.88] \times T_{\text{session}}$, guaranteeing realistic, non-zero interception windows between $5.5\text{ min}$ and $38.5\text{ min}$ on every run.
* **Why This Over Alternatives:** Pre-generating a static set of 3 payloads was sufficient for initial smoke testing but inadequate for demonstration rehearsals and scale testing. Hardcoding scripts for individual test cases creates code sprawl. Restricting strictly to Pune, Bengaluru, and Delhi ensures that Stage 3 spatial candidate searches always discover candidate ATMs within $5\text{ km}$, and calibrating $\tau$ prevents arbitrary zero-minute countdown states.
* **System Impact:** Both generators enforce Pydantic `IncidentPayload` schema validation and Golden Hour dual-gate temporal math ($T_{\text{latest}} \le 120\text{ min}$, $T_{\text{complaint}} > T_{\text{latest}}$). Payloads generated by either tool ingest seamlessly into Synapse in both live and simulation modes.

---

### [Architectural & Design Choice] Ingestion Rejection on Non-Viable Terminal Mule (NO_VIABLE_ATM_MULE)
* **Context / Decision:** When Stage 1 Terminal Mule Isolation determines that a candidate terminal mule fails the ATM viability filter (e.g., non-ATM payments bank, account type `CURRENT`, or missing debit card linkage hash), the ingestion endpoint (`POST /api/v1/ingest`) immediately raises an `HTTPException(status_code=422, detail=f"NO_VIABLE_ATM_MULE: {stage1.disqualification_reason}")`. The payload is strictly dropped: it is NOT appended to in-memory `_incidents`, NOT persisted to `data/incidents.json`, and NOT emitted to tactical dashboard views. In the admin ingest drawer (`ui/index.html`), the 422 error is caught and visually represented by marking Golden Hour gates passed, Stage 1 failed with the exact disqualification reason string, subsequent stages skipped, and suppressing active incident cards.
* **Why This Over Alternatives:**
  - (1) **True Rejection vs Disguised Acceptance (HTTP 200 with status="NO_VIABLE_ATM_MULE"):** Previously, `api/main.py` wrapped non-viable mules into a full `IngestResponse`, persisted them to `_incidents`, and returned HTTP 200. Because Stages 2 and 3 were bypassed, downstream fields (confidence, urgency, spatial target) were left uncalculated or zeroed. When loaded into the tactical dashboard, this created ghost cases displaying fabricated-looking zeros and UI inconsistencies (such as an "Account Type Check Failed" badge displaying a green checkmark while stages 2 & 3 spun on "Processing..." indefinitely).
  - (2) **Consistency with Gate 1 / Gate 2 Rejection Semantics:** Gate 1 (`GOLDEN_HOUR_EXPIRED`) and Gate 2 (`STALE_PAYLOAD`) failures return HTTP 422 and are never admitted to the active incident store. Stage 1 non-viability is functionally identical: it represents an invalid candidate for Synapse's ATM cash-out interception scope. Unifying all three rejection states under HTTP 422 guarantees that only actionable ATM mule incidents enter the active operational state machine.
* **System Impact:** Eliminates phantom cases and zero-confidence dashboard clutter. Guarantees that `GET /api/v1/incidents` only contains cases that successfully completed the multi-stage prediction pipeline. The rejection reason is cleanly preserved in the HTTP 422 response body and logged with warning severity for upstream CFCFRMS manual routing.

---

### [Architectural & Design Choice] Bulk Payload Upload & Paced Client-Side Queue Engine
* **Context / Decision:** We implemented a client-side sequential queue engine in `ui/feed.html` supporting multi-file selection (`<input type="file" multiple accept=".json">`). Files are staged in memory in the exact order selected by the operator. The engine immediately dispatches the first payload, followed by subsequent payloads at paced 5.0-second intervals managed via `setInterval`. A visual telemetry card displays real-time statistics (total staged, sent, remaining, countdown timer, and progress bar). The queue is non-blocking: if an individual payload fails (e.g. HTTP 422 `NO_VIABLE_ATM_MULE`), the error is logged to the Feed Activity Log with the ticket ID and reason, and the queue seamlessly advances to the next payload. An operator can cancel the queue mid-run via a dedicated "Cancel" button.
* **Why This Over Alternatives:** Server-side batch processing endpoints (e.g. `POST /api/v1/ingest/batch`) would process 50 payloads in parallel within milliseconds, saturating webhook workers and making it impossible for a human demonstrator to watch the Tactical Dashboard react dynamically in real-time. Client-side 5-second pacing creates a realistic, observable simulation of upstream bank telemetry arriving in sequential waves.
* **System Impact:** Enables effortless demonstration of high-volume incident flow without manual clicking, preserving realistic real-time dashboard updates and webhook executions.

---

### [Architectural & Design Choice] Dynamic Unified Timestamp Alignment for Simulation Invariance (alignPayloadTimestampsToNow)
* **Context / Decision:** When ingesting static or historical payloads in Simulation Mode, `alignPayloadTimestampsToNow(payload)` translates all temporal fields (`ingestion_timestamp`, `complaint_timestamp`, `fund_flow.transactions[*].txn_timestamp`, `ip_cluster[*].last_seen`, `cell_tower_cluster[*].last_seen`) simultaneously by the exact same offset $\Delta = \text{Date.now()} - T_{\text{base}}$.
* **Why This Over Alternatives:**
  - (1) **Asymmetric Stamping Failure:** Stamping only `ingestion_timestamp = NOW()` while leaving historical transactions untouched decoupled the reference clock from the fund flow. Elapsed time $\tau = T_{\text{ref}} - \max(T_{\text{txn}})$ exploded to 80+ minutes, causing the drain time engine to compute $D_{\text{remaining}} = 0.0\text{ min}$ for every payload. This in turn triggered the Phase 10 auto-withdrawal rule on arrival, falsely marking all accounts as `DAILY_LIMIT_EXHAUSTED` and displaying 0 min on the dashboard.
  - (2) **Mathematical Invariance:** Translating all timestamps by identical $\Delta$ guarantees $(T_{\text{ingest}} + \Delta) - (T_{\text{last\_txn}} + \Delta) \equiv T_{\text{ingest}} - T_{\text{last\_txn}} = \tau_{\text{intended}}$. Every payload retains its calibrated non-zero interception window ($15.7\text{m}$, $38.5\text{m}$, etc.) on simulation ingest, regardless of whether the file was generated minutes or days ago.
  - (3) **Garbage Collector Immunity:** Because $T_{\text{ingest}}$ becomes `NOW()`, incidents are protected against the 6-hour tactical timeout background sweeper.
* **System Impact:** Eliminates the 0-minute drain time defect across both single and bulk upload flows in `ui/feed.html` and `ui/index.html`.

---

### [Architectural & Design Choice] Simulation Mode Toggle Decoupling (Live vs Simulated Bulk Ingest)
* **Context / Decision:** In `ui/feed.html`, the bulk upload queue engine dynamically branches based on `#sim-toggle` (`_simMode`):
  - **Toggle ON (Sim Mode):** Targets `POST /api/v1/ingest?simulate=true`, applies `alignPayloadTimestampsToNow()`, bypasses Golden Hour gates, and displays `5s Pacing · Sim Mode` (amber).
  - **Toggle OFF (Live Mode):** Targets `POST /api/v1/ingest`, transmits raw payload timestamps as-is from disk, strictly enforces Gate 1 (`recency ≤ 120 min`) and Gate 2 (`freshness ≤ 240 min`) rejections (HTTP 422), and displays `5s Pacing · Live Mode` (slate).
* **Why This Over Alternatives:** Hardcoding simulation mode on bulk uploads prevented operators from testing live gate validation rules on batch files. Tying bulk ingest to the existing simulation toggle maintains single-responsibility UI semantics: the operator explicitly controls whether uploads are treated as historical demo replays or strict real-time live feeds.
* **System Impact:** Ensures complete behavioral consistency between single payload submission and bulk queue execution.

---

### [Architectural & Design Choice] Incident Registry Purge Endpoint for Simulation Testing (POST /api/v1/incidents/clear)
* **Context / Decision:** We added `POST /api/v1/incidents/clear` in `api/main.py` which purges in-memory `_incidents` and writes `[]` to `data/incidents.json`, paired with a `🧹 Clear Incident Feed` button in `ui/feed.html`.
* **Why This Over Alternatives:** Prior to this endpoint, clearing incidents required manually stopping the server and editing `data/incidents.json` on disk. Exposing a clean administrative purge endpoint allows operators to reset the tactical dashboard and incident feed between rehearsal runs with a single click.
---

### [Architectural & Design Choice] Live ATM Cash-out Pinpointing & 3rd Location Telemetry Source
* **Context / Decision:** When an operator pushes a simulated live ATM withdrawal from `ui/feed.html`:
  - The operator selects one of the active incident's Top 3 candidate ATMs via `#atm-select`.
  - The backend (`PATCH /api/v1/incidents/{ncrp}/live-update`) pins the suspect's GPS coordinates (`mule_estimated_lat`, `mule_estimated_lon`) directly to the selected ATM's latitude and longitude, setting `mule_location_method` to `ATM_WITHDRAWAL_CONFIRMED`.
  - The event is logged in the Feed Activity Log and recorded in `snap["atm_location_source"]` as the **3rd Location Source (ATM Physical Withdrawal Fix)**.
  - In `ui/index.html`, the map renders an emerald pulsing beacon (`.mule-dot.confirmed`) with a tightened 50m pinpoint accuracy ring (replacing the 2 km IP uncertainty ring).
* **Why This Over Alternatives:**
  - (1) **True Ground-Truth Convergence:** While IP addresses give only 1–5 km city-level estimates, an ATM debit card transaction provides physical ground truth of the suspect's exact coordinates at transaction timestamp $T_{\text{txn}}$.
  - (2) **Omission of Dynamic Re-ranking:** Dynamic re-ranking of neighboring ATMs upon cash-out was evaluated and explicitly excluded per operator instruction. Preserving the original Top 3 candidate ATMs prevents disruptive UI layout shifts for dispatch officers while providing the exact pinpoint of the cash-out.
* **System Impact:** Transforms the live withdrawal simulation from an isolated numerical ledger edit into an integrated geospatial and intelligence event across both portals.

---

### [Architectural & Design Choice] Discrete Fund Flow Cash-Out Transaction Logging (ATM_CASH_WITHDRAWAL)
* **Context / Decision:** Pushing a live ATM withdrawal now creates and appends a discrete `ATM_CASH_WITHDRAWAL` transaction to `payload_snapshot.transactions`:
  - `hop_index`: `len(existing_hops) + 1`
  - `sender_account`: Terminal mule account
  - `receiver_account`: `CASH-{atm_id}`
  - `channel`: `ATM_CASH_WITHDRAWAL`
  - `amount_inr`: Additional withdrawal amount
* **Why This Over Alternatives:** Previously, fund flow transactions in the Incident Intelligence panel only displayed the historical bank-to-bank hops. The cash-out stage was invisible in the transaction flow accordion, creating a disconnect between the mule balance deduction and the transaction ledger. Appending an explicit cash-out hop completes the audit trail from the victim source account to the final physical cash withdrawal.
* **System Impact:** The dashboard Transaction Flow accordion dynamically renders the final cash-out hop with dedicated `🏧 ATM CASH OUT` badges and cash destination styling.
