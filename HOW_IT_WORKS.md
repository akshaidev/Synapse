# Project Synapse — Complete System Documentation

> *Everything the system does, explained from scratch.*
> *The Pune fraud case from 5 September 2026 is used as the running example throughout.*

---

## The Problem Being Solved

When someone is defrauded in India — say, a scammer calls pretending to be a bank officer and tricks the victim into transferring ₹4,87,500 — the money doesn't sit in one place. Within minutes, it moves through a chain of 3–6 "mule accounts" (accounts controlled by the scammers) before the last person in the chain walks up to an ATM and withdraws it as cash.

By the time the victim calls the 1930 helpline and a police complaint is filed, the money is usually already gone. The entire ATM cash-out typically happens within **45–90 minutes** of the original fraud.

Synapse's job is to beat that clock.

---

## The Golden Hour

Synapse operates exclusively within the **first 120 minutes after the fraud transaction happened**. This window is called the Golden Hour (even though it's technically two hours — the name reflects urgency, not clock math).

The clock starts from the **last transaction in the fund flow chain** — not from when the victim called the police. This distinction matters: if the money took 12 minutes to travel through 4 hops, the clock started at hop 4's timestamp, not at the complaint timestamp.

**Two gates must pass before Synapse does anything:**

**Gate 1 — Is the fraud recent enough?**
Synapse looks at every transaction timestamp in the chain, takes the latest one, and checks: `is NOW minus that timestamp less than 120 minutes?` If not, the funds are almost certainly already cashed out. The incident is logged but Synapse produces no predictions.

**Gate 2 — Is the complaint plausible?**
The complaint timestamp must be within 240 minutes of NOW. This prevents someone from finding a week-old fraud report and trying to trigger emergency interventions on it.

If either gate fails, the case is routed to the regular CFCFRMS manual investigation queue. Synapse doesn't touch it.

> **Simulation Mode:** For demos and testing, both gates can be bypassed by setting a toggle in the Admin Drawer. When active, an amber "⚡ SIM MODE" badge appears in the navbar.

---

## What Gets Submitted to Synapse

Someone uploads a single JSON file — the Incident Payload. Think of it as a structured case file with three sections stapled together:

**Section 1 — The NCRP Ticket**
Who the victim is, when they filed the complaint, how much was stolen, their state/district, and what kind of fraud it was (UPI fraud, vishing, phishing, etc.)

**Section 2 — The Fund Flow**
A list of every hop the money took, in order:
- Hop 1: Victim's Axis Bank account → HDFC Bank mule account (₹4,87,500 via UPI, 01:06:12)
- Hop 2: HDFC mule → SBI mule account (₹4,85,000 via IMPS, 01:09:45)
- Hop 3: SBI mule → PNB mule account (₹2,40,000 via IMPS, 01:14:22)
- Hop 4: PNB mule → Canara Bank account (₹2,37,500 via NEFT, 01:18:07)

Each hop includes sender and receiver account numbers, bank names, IFSC codes, amount, channel, and timestamp.

**Section 3 — Terminal Mule Intelligence**
Everything known about the last account in the chain (the one that will walk to an ATM): current balance, daily withdrawal limit, how much they've already withdrawn today, their IP address from a recent banking session (for location), and a hash of their debit card number.

---

## Stage 1 — Finding the Cash-Out Account

**What it does:** Synapse reads the fund flow and builds a map of who sent money to whom. It then finds the account at the end of the chain — the one that received money but never sent it anywhere. That's the terminal mule.

**How it works — the fund flow graph:**

Imagine drawing arrows between accounts:

```
Victim (Axis) ──→ HDFC Mule ──→ SBI Mule ──→ PNB Mule ──→ Canara Mule
```

The Canara Bank account is at the end of the arrow with no outgoing arrow. That's the leaf node. In the Pune example, there's only one such account, so it's straightforwardly identified.

**What if money splits into multiple accounts at the end (fan-out)?**

Sometimes the money gets split — e.g., the PNB mule sends ₹1,20,000 to Canara and ₹1,20,000 to ICICI. Both Canara and ICICI are leaf nodes. Synapse needs to pick the most likely cash-out account. It does this by scoring each candidate:

**The Mule Probability Score (MPS):**

Each candidate leaf gets a score between 0 and 1 based on three factors:

| Factor | What it measures | Weight |
|---|---|---|
| Amount ratio | Did this account receive the largest share of the money? | 30% |
| Recency | How recently did the money arrive? (Freshly arrived = more likely to be cashed out soon) | 30% |
| CFCFRMS match | Did the upstream fraud system already flag this specific account? | 40% |

The recency score uses an **exponential decay curve** — an account that received money 2 minutes ago scores near 1.0, one that received it 30 minutes ago scores near 0.05. This is important because the curve keeps all three scores in the same 0–1 range, so the 30%/30%/40% weights actually mean what they say.

*Why not a simple "the most recent is the mule"?* Because the CFCFRMS match (the upstream system's pre-identification) is the strongest signal and should dominate. If CFCFRMS says account X is the mule, and X got the most money recently, MPS confirms it with very high confidence.

**The Viability Check:**

Before proceeding, Synapse checks three things about the selected mule account:

1. **Does it have a physical debit card?** — The card hash must be present. No card = no ATM withdrawal = wrong type of mule.
2. **Is the account type ATM-capable?** — Savings and basic savings accounts can use ATMs. Some account types cannot.
3. **Is it a payments bank?** — Paytm Payments Bank, Fino, Airtel Payments Bank, Jio Payments Bank, and India Post Payments Bank don't issue standard ATM-capable cards. If the mule's account is at one of these, they're flagged as a non-ATM mule and the case goes to manual review.

If any of these checks fail, Synapse stops here and routes the case to CFCFRMS. No ATM prediction is made because it would be meaningless.

---

## Stage 2 — How Much Time Is Left Before the ATM Window Closes?

**What it does:** Synapse estimates how many minutes remain before the mule finishes withdrawing all the money they can access at ATMs today.

**The logic, step by step:**

**Step 1 — How much can the mule actually withdraw today?**

```
Accessible amount = min(account balance, daily limit - already withdrawn today)
```

In Pune:
- Account balance: ₹2,41,350
- Daily withdrawal limit: ₹1,00,000
- Already withdrawn today: ₹0
- Accessible amount = min(₹2,41,350, ₹1,00,000 - ₹0) = **₹1,00,000**

Even though the balance is ₹2,41,350, the mule can only take ₹1,00,000 today because of the daily ATM cap.

**Step 2 — How many ATM trips does that require?**

Each ATM transaction is capped at ₹20,000 (standard bank policy). So:

```
Number of trips = ceiling(₹1,00,000 / ₹20,000) = 5 trips
```

**Step 3 — How long does 5 trips take?**

Based on observed mule behaviour (from synthetic datasets calibrated against I4C case studies), the average time between successive ATM withdrawals is **4.5 minutes**. This accounts for walking to the ATM, the transaction time, and moving to the next machine.

```
Total session time = 5 × 4.5 minutes = 22.5 minutes
```

**Step 4 — Subtract time already elapsed**

By the time Synapse processes this, some time has already passed since the mule received the funds. In Pune, the money arrived at 01:18:07 and Synapse processed it at 01:22:00, so **3.88 minutes** have already elapsed. The mule may have already started withdrawing during this time.

```
Remaining time = max(0, 22.5 - 3.88) = 18.6 minutes
```

This is the **Interception Window** — 18.6 minutes for police to reach ATMs before the money is gone.

**Special case — Daily Limit Already Exhausted:**

If the accessible amount ≤ 0, the mule has already hit their daily withdrawal ceiling. The remaining time is set to 0, but critically, the **urgency score is also set to 0** — not maximum. This is counterintuitive but correct: a mule who *cannot* withdraw any more money today has zero interception urgency. Sending officers to ATMs for this case would be pointless.

---

## Stage 3 — Which ATMs Will the Mule Go To?

This is the spatial intelligence stage. It has three sub-steps.

### Step 3a — Where Is the Mule Right Now?

Synapse doesn't know the mule's physical location directly. It estimates it from available data using a priority system:

**Priority 1 — Cell tower data** (rarely available in real-time due to Indian legal processes — listed for completeness)
The mule's mobile device pings nearby cell towers. Synapse triangulates their position using signal strength and recency of each ping.

**Priority 2 — IP address geolocation** (the primary method in the MVP)
When the mule last used their banking app, the bank recorded their IP address. Synapse geolocates that IP to a latitude/longitude. In Pune, two IP observations both place the mule near Shivaji Nagar (18.5204°N, 73.8567°E). Synapse takes the average of all IP coordinates as the estimated position.

IP geolocation is city-level accurate — typically within 1–5 km. This means the position ring on the map shows a ~2 km uncertainty circle, not a pinpoint.

**Priority 3 — IFSC branch fallback** (last resort)
If no IP data and no cell towers, Synapse looks up the physical address of the mule's bank branch using their IFSC code. This is just a rough estimate — the mule opened their account there but could be anywhere in the city. When this fallback is used, the confidence score is capped at 0.75 (more on that below).

### Step 3b — Which ATMs Are Nearby?

Synapse maintains an internal database of 200 ATMs across Pune, Bengaluru, and Delhi (for the demo). In production this would be 50,000+ ATMs nationally.

Using the mule's estimated position, Synapse draws a 5 km radius circle (urban areas) or 15 km circle (rural areas) and collects all ATMs within it. If fewer than 3 ATMs are found, it expands the radius by 1.5× and tries again, up to twice.

For Pune, the 5 km search around Shivaji Nagar finds a cluster of ATMs from Canara Bank, PNB, SBI, HDFC, and others.

### Step 3c — Which of Those ATMs Is the Mule Most Likely to Use?

Every ATM gets a **Risk Score** between 0 and 1 based on five factors that describe how a typical ATM mule behaves:

| Factor | How it's scored | Weight | Why it matters |
|---|---|---|---|
| **Proximity** | 1 minus (distance to mule / search radius). An ATM 0 km away scores 1.0; one at the edge of the radius scores 0. | 30% | Mules minimise exposure time. The nearest ATM is the most likely target. |
| **Bank match** | 1 if the ATM's bank matches the mule's bank, 0 otherwise | 25% | Same-bank ATMs: higher per-day limits, no interbank fees, faster transaction |
| **Cash availability** | FULL=1.0, PARTIAL=0.7, LOW=0.3, EMPTY=0.0, UNKNOWN=0.5 | 20% | Mules need cash. An empty ATM defeats the purpose. |
| **Offsite location** | 1 if the ATM is not on a bank branch premises, 0 if it's inside a branch | 15% | Offsite ATMs (standalone kiosks) have less CCTV coverage and no branch security guard |
| **Traffic level** | Log-normalised daily transaction count vs. the busiest ATM in the cluster | 10% | High-traffic ATMs let the mule blend in with normal customers |

**Worked example for the top-ranked Pune ATM (CNRB-ATM-PNE-0042):**

- Location: Canara Bank offsite ATM, FC Road, Shivaji Nagar — 1.2 km from the mule's estimated position
- Proximity score: ~0.76 (1 - 1.2/5.0) × 0.30 weight = **+0.228**
- Bank match: Canara Bank = Canara Bank ✓ × 0.25 weight = **+0.25**
- Cash status: FULL × 0.20 weight = **+0.20**
- Offsite: `is_onsite = false` × 0.15 weight = **+0.15**
- Traffic: 185 daily txns, log-normalised × 0.10 weight ≈ **+0.07**
- **Total Risk Score ≈ 0.91** → Ranked #1

The top 3 ATMs are returned: CNRB-ATM-PNE-0042 (0.91), PUNB-ATM-PNE-0091 (0.84), CNRB-ATM-PNE-0058 (0.76).

---

## The Confidence Score — Should We Actually Act?

After all three stages run, Synapse combines their outputs into a single **Composite Confidence Score** between 0 and 1. This score determines whether an automatic action is taken and how aggressive it is.

**Formula:**

```
Confidence = (0.20 × MPS score) + (0.35 × Urgency) + (0.45 × Top ATM risk score)
```

**The three ingredients:**

**MPS score (20% weight):** How certain are we this is the right mule? In Pune, the CFCFRMS system had already flagged the Canara account, so MPS is high (close to 1.0 → contributes ~0.20 to confidence).

**Urgency score (35% weight):** How much of the interception window is left?
```
Urgency = 1 - (drain time remaining / 120)
```
With 18.6 minutes left out of a 120-minute window: `Urgency = 1 - (18.6 / 120) = 0.845`
This contributes `0.35 × 0.845 = 0.296` to confidence.

**Top ATM risk score (45% weight):** How confident are we in the spatial prediction?
The top ATM scored 0.91, contributing `0.45 × 0.91 = 0.410` to confidence.

**Pune confidence score: ≈ 0.20 + 0.296 + 0.410 = ~0.88**

**What the score means:**

| Score | What happens |
|---|---|
| Below 0.70 | Incident is logged and shown on the strategic map. No automatic action. |
| 0.70 – 0.84 | **PRIMARY DIGITAL** — Synapse automatically sends a freeze webhook to the mule's bank. The mule's ATM card is blocked for 120 minutes. |
| 0.85 and above | **SECONDARY PHYSICAL** — Same freeze webhook fires, PLUS the incident appears in the tactical officer queue with ATM locations and a "Send Crew" button. |

Pune scores 0.88 → **SECONDARY PHYSICAL**. Automated card freeze goes out immediately, and the incident appears in the tactical officer's dashboard for field deployment.

**The IFSC Confidence Cap:**
If Synapse had to fall back to IFSC branch coordinates to estimate the mule's location (because no IP and no cell tower data was available), the confidence score is hard-capped at 0.75. This prevents an imprecise location estimate from ever triggering physical officer deployment (which requires 0.85+). An IFSC-based prediction can freeze the card (0.70 threshold) but cannot send officers to specific ATMs.

---

## The Automatic Freeze Webhook

When confidence crosses 0.70, Synapse fires a machine-to-machine webhook to the participating bank's card management switch. This is a POST request with a structured payload containing:

- Which card to block (identified by a SHA-256 hash of the card number — the raw card number is never stored or transmitted)
- The block type: `ATM_WITHDRAWAL_BLOCK` (blocks only ATM cash withdrawals, not UPI/online purchases)
- Duration: 120 minutes (aligned with the Golden Hour window)
- The three ATMs to flag at the terminal level
- The justification: drain time remaining, amount at stake, fund flow depth, location method used
- An expiry timestamp: when the Golden Hour ends, the bank auto-releases the hold if no FIR/court order has arrived

**Retry logic:** If the bank's server returns an error, Synapse retries 3 times with exponential backoff (5s → 10s → 20s). If all 4 attempts fail, the webhook is logged as `WEBHOOK_FAILED` and an alert is raised.

---

## The User Interface

Synapse has two portals: the **main dashboard** at `localhost:8000` and the **Bank Feed Simulator** at `localhost:8000/feed`.

### The Main Dashboard

**Admin Drawer (left sidebar)**

Accessible via the hamburger icon. Contains:
- A JSON upload area to submit incident payloads (drag and drop or file select)
- Quick Demo buttons for Pune, Bengaluru, and Delhi pre-made payloads
- Simulation Mode toggle (amber when on)
- Operator ID field — every action that freezes or unfreezes accounts requires an operator to identify themselves

**View A — Strategic Command (top section)**

Shows the aggregate picture:
- KPI cards: active incidents, resolved incidents, total amount at stake, automated webhooks fired
- A map with pulsing dots for each active mule location (2 km accuracy rings for IP-geolocated incidents)
- A list of all active incidents; clicking one opens the Intelligence Panel

**View B — Tactical Interception Queue (bottom section)**

Shows only incidents that scored 0.85+ (SECONDARY PHYSICAL tier). Each incident has:
- Three ATM cards (Rank 1, 2, 3) with the ATM's address, risk score, distance from mule, cash status, and whether it's on-site or off-site
- An "Acknowledge & Dispatch" button on each ATM card to send a field crew there

**The Intelligence Panel (right side, opens when you click an incident)**

A full dossier on the selected case, with five sections:

1. **Case Overview** — NCRP ticket ID, fraud type, victim state/district, complainant name, total amount stolen
2. **Source Account** — The victim's bank details
3. **Terminal Mule** — The cash-out account's balance, daily limit, amount already withdrawn, account type
4. **Transaction Flow** — An accordion showing each hop. Each hop expands to reveal the sender and receiver account numbers, bank names, IFSC codes, amount, timestamp, channel, and **a Digital Lien button on every account** (except the victim)
5. **IP Intelligence** — A table of IP addresses observed in the mule's banking sessions, their geolocation, and ASN (internet provider)

**The Interception Window Countdown**

Below the case overview, a large countdown timer shows the minutes remaining before the cash-out window closes. Three possible end-states:

- **"INTERCEPTION WINDOW: CLOSED"** — the timer hit zero during live countdown
- **"DAILY LIMIT EXHAUSTED"** — the mule's daily ATM cap was already at zero when the payload arrived
- **"NO WITHDRAWABLE BALANCE"** — the balance itself is zero or negligible

---

## The Digital Lien System

Every account that appears in the Transaction Flow has a lien button next to it (except the victim's account, which is intentionally excluded).

**Two states:**

**🔒 Initiate Digital Lien** — No lien is currently active. Clicking it opens a popup modal with:
- The account number and bank name
- A warning that this will immediately fire a freeze webhook to the bank
- A mandatory text field for the reason (cannot submit blank)
- "Confirm — Initiate Lien" button (indigo colour)
- "Cancel" button

After confirming, the freeze webhook fires in real-time and the account is recorded on the server with the operator's ID, reason, and timestamp.

**🔓 Revoke Digital Lien [MANUAL/PIPELINE]** — A lien is currently active. The tag shows whether it was placed manually or automatically by the pipeline. Clicking it opens the Revocation modal with a dropdown of pre-defined reasons:
- Account verified as non-mule
- Incorrect account flagged
- Operational override
- Court order
- Lien placed in error
- Other (with a free-text field)

Revoking removes the account from the active liens list but appends a permanent record to the revocation log — the history is never deleted.

**State synchronisation:** The receiver of Hop 1 is the same physical bank account as the sender of Hop 2. Both buttons show the same state because they look up the same account number in the registry. Click one → both update.

**Persistence:** Lien state is stored in `data/lien_registry.json` on the server. Survives page refreshes, browser clears, and server restarts.

**When the pipeline fires a webhook automatically** (confidence ≥ 0.70 on ingestion), the terminal mule's lien button is automatically set to "Revoke Digital Lien [PIPELINE]" — no manual action needed.

---

## Crew Dispatch

In the tactical queue, each ATM card has an "Acknowledge & Dispatch" button. Once clicked:
- The button changes to "✓ Crew Dispatched — #1" (or #2, #3)
- The dispatch is recorded in `data/sent_crew.json` on the server
- The button remains dispatched across page refreshes and for all operators viewing the same case
- Other ATM buttons are unaffected (all three can be dispatched independently)

---

## The Bank Feed Simulator (`/feed`)

A separate page at `localhost:8000/feed` simulates what a bank would push to Synapse in real-time as the mule makes withdrawals.

**Section A — Upload**
Upload a payload with a Simulation Mode toggle. When Sim Mode is ON, it re-stamps the payload with the current time, so the interception countdown starts from "right now."

**Section B — Live Withdrawal Push**
Select any active incident, specify a withdrawal amount (e.g., ₹20,000), and click "Push". This:
- Adds the withdrawal amount to the mule's total withdrawn today
- Subtracts it from the account balance
- Recalculates how much the mule can still withdraw

The main dashboard polls every 5 seconds and automatically updates when it detects the change. No manual refresh.

---

## Case Resolution

When a case is handled, the "✓ MARK RESOLVED" button opens a modal with five resolution reasons:
- Funds Frozen
- Mule Apprehended
- Funds Recovered
- Window Elapsed (Case Closed)
- False Positive

After resolving, the incident moves to a Resolved Cases section, KPIs update, and a webhook fires to a mock NCRP endpoint recording the closure. Cases can be reopened.

**Automatic timeouts (background process):**
- Incidents active for more than 6 hours → auto-marked `TACTICAL_TIMEOUT`
- Incidents older than 30 days → auto-marked `ADMINISTRATIVELY_CLOSED`

---

## Automatic Simulated Withdrawal on Late Ingestion

If a payload is submitted when the interception window has already closed (drain time ≤ 0 on arrival), Synapse automatically simulates the withdrawal it was too late to prevent. It calculates the likely ₹10,000-bracket withdrawals and deducts them from the balance snapshot, so the operator sees the realistic remaining balance rather than the pre-cash-out figure.

---

## Three Demo Scenarios

| Scenario | Chain | Confidence | Outcome |
|---|---|---|---|
| **Pune** | 4-hop, ₹4,87,500, Canara Bank mule, IP-geolocated to Shivaji Nagar, 18.6 min window | ~0.88 | SECONDARY PHYSICAL — card frozen + crew dispatch recommended |
| **Bengaluru** | Shorter drain window, lower spatial score | ~0.76 | PRIMARY DIGITAL — card frozen automatically, no officer dispatch |
| **Delhi** | Payload arrives after window closes | — | Simulated withdrawal deducted; case logged as tactical timeout |

---

## What Happens When Things Go Wrong

| Situation | What Synapse does |
|---|---|
| Payload arrives more than 120 min after last transaction | Gate 1 fails → `GOLDEN_HOUR_EXPIRED` → logged, sent to CFCFRMS queue |
| Complaint is older than 240 minutes | Gate 2 fails → `STALE_PAYLOAD` → manual review |
| Mule account has no ATM card | Viability check fails → `NO_VIABLE_ATM_MULE` → CFCFRMS manual review |
| Mule is at a payments bank (Paytm, Fino, etc.) | Same viability check → no ATM prediction |
| Mule's daily ATM cap is already exhausted | Drain time = 0, urgency = 0 (not maximum — critical distinction), no officer dispatch |
| Fewer than 3 ATMs in the search radius | Radius expands to 1.5×, then 2.25× if still not enough |
| Bank webhook fails | 3 retries with 5s → 10s → 20s waits. If all fail: `WEBHOOK_FAILED`, card is NOT frozen, alert raised |
| No IP and no cell tower data | Falls back to IFSC branch coordinates, confidence capped at 0.75 (card freeze possible, no physical dispatch) |
| Fund flow splits to multiple accounts at the end | MPS scoring picks the highest-scoring leaf; CFCFRMS mismatch warning logged if they disagree |

---

## Numbers to Remember

| Parameter | Value | What changes if you change it |
|---|---|---|
| Golden Hour window | 120 minutes | Longer = more cases qualify, staler intelligence |
| Complaint freshness window | 240 minutes | Gate 2 threshold |
| MPS weights (amount / recency / CFCFRMS) | 30% / 30% / 40% | Rebalancing changes which leaf wins in fan-out cases |
| Recency decay half-life | 7 minutes | Shorter = recency matters more, degrades faster |
| Per-transaction ATM limit | ₹20,000 | Changes number of estimated ATM trips |
| Inter-withdrawal interval | 4.5 minutes | Core drain time multiplier |
| ATM search radius | 5 km (urban) / 15 km (rural) | Too small = too few ATMs; too large = unrealistic targets |
| PRIMARY DIGITAL threshold | 0.70 | Lower = more automatic card freezes (more false positives) |
| SECONDARY PHYSICAL threshold | 0.85 | Lower = more physical deployments (higher resource cost if wrong) |
| IFSC fallback confidence cap | 0.75 | Prevents imprecise location from ever triggering physical dispatch |
| Webhook retries | 3 (5s / 10s / 20s backoff) | More retries = better delivery, slower Golden Hour response |

---

*Document written 7 September 2026. Reflects system state at Phase 13 (all 22 modules complete).*
