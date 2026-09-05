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

> **Note:** This document is a living log. Every new session that makes a technical decision must append to the relevant category following the schema above. Do not delete or modify existing entries — append corrections as new entries referencing the original.
