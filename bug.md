# Known Bugs & Issues Registry

### BUG-001: Bank Feed Simulator `resolveCase()` Missing `operator_id` (HTTP 422)
* **Severity:** Medium / Functional Regression
* **Affected File:** [`ui/feed.html:L416-L420`](file:///Users/akshai/Developer/Synapse/ui/feed.html#L416-L420)
* **Description:** 
  The Bank Feed Simulator portal (`ui/feed.html`) includes a "✓ Mark Resolved" button next to the Push Update button. When clicked, `resolveCase()` dispatches a POST request to `/api/v1/incidents/{ncrp_ticket_id}/resolve`:
  ```javascript
  const res = await fetch(`${SYNAPSE}/api/v1/incidents/${encodeURIComponent(ncrp)}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: 'FUNDS_FROZEN', note: 'Resolved via Bank Feed Simulator' }),
  });
  ```
  The payload omits the `operator_id` field. Because [`api/schemas.py:L177-L180`](file:///Users/akshai/Developer/Synapse/api/schemas.py#L177-L180) (`ResolveIncidentRequest`) defines `operator_id: str` as a required field, FastAPI raises a validation error (`HTTP 422 Unprocessable Entity`).
* **Impact:** Operators cannot resolve cases directly from `ui/feed.html`. The feed activity log displays: `Resolve failed: undefined`.
* **Fix Required:** Update `resolveCase()` in `ui/feed.html` to supply an `operator_id` (e.g. prompt or default to `'BANK_FEED_SIMULATOR'`).
