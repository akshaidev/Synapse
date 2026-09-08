# Known Bugs & Issues Registry

### BUG-001: Bank Feed Simulator `resolveCase()` Missing `operator_id` (HTTP 422) [RESOLVED]
* **Severity:** Medium / Functional Regression
* **Status:** ✅ RESOLVED (2026-09-08)
* **Affected File:** [`ui/feed.html`](file:///Users/akshai/Developer/Synapse/ui/feed.html)
* **Description:** 
  The Bank Feed Simulator portal (`ui/feed.html`) had a "✓ Mark Resolved" button that dispatched a POST request to `/api/v1/incidents/{ncrp_ticket_id}/resolve` with a hardcoded reason `'FUNDS_FROZEN'` and omitted the required `operator_id` field, causing FastAPI to return `HTTP 422 Unprocessable Entity`.
* **Resolution:**
  1. Replaced the direct action with `openResolveModal()`, surfacing a dedicated resolution modal (`#resolve-modal`) matching the main dashboard's design.
  2. Integrated an Operator ID input field that pre-populates and bi-directionally synchronizes with `localStorage.getItem('synapse_operator_id')`. Inline validation strictly prevents submission if the operator ID is empty.
  3. Added a resolution reason `<select>` exposing all 5 valid Synapse resolution taxonomy options (`FUNDS_FROZEN`, `MULE_APPREHENDED`, `FUNDS_RECOVERED`, `WINDOW_ELAPSED_CASE_CLOSED`, `FALSE_POSITIVE`).
  4. Added an optional officer note textarea.
  5. Implemented `submitResolve()` which dispatches `{ reason, operator_id, note }` to `/resolve`, closes the modal on HTTP 200, logs the resolution to the activity feed, and calls `pollIncidents()`.
* **Verification:** Validated via automated test suite [`tests/verify_feed_resolution.py`](file:///Users/akshai/Developer/Synapse/tests/verify_feed_resolution.py) (17/17 PASS), verifying HTML controls, schema compliance, 422 rejection without operator ID, and 409 idempotency guard.

