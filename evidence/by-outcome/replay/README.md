# Replay outcomes

One subfolder per scenario the Replay Engine was run through live, each a full copy of that run's evidence (`trace.json`,
`log.jsonl`, `result.json`, screenshots, and any `ticket-*.json`), curated and reviewed before being placed here. Each
folder's own `README.md` names its source run (a `run_id`), what it shows, and the result. Raw run folders are not
committed: every `run_id` inside a folder's files equals the one its README names, and `scripts/audit_evidence.py` checks that.

Every run: `llm_calls: 0`. The Replay Engine has no LLM client.

**SUCCESS**
- `SUCCESS/` - the plain path, all outputs populated
- `SUCCESS_decimal_amount/` - `1.5` typed as `1.50`, confirmed as `$1.50`
- `SUCCESS_session_recovered/` - a real session expiry, auto re-authenticated and retried once
- `SUCCESS_supervisor_approved/` - over the approval threshold; the supervisor's own click authorizes it
- `SUCCESS_click_then_reject/` - the human clicked Transfer, then typed `reject`; the page decides, the conflict is recorded

**BUSINESS_OUTCOME** (a known answer from the app, not a crash)
- `BUSINESS_OUTCOME_invalid_account/` - a nonexistent source account
- `BUSINESS_OUTCOME_invalid_destination_account/` - a nonexistent destination account
- `BUSINESS_OUTCOME_login_rejected/` - a wrong password

**POLICY_BLOCK** (a rule refused it; nothing was dispatched)
- `POLICY_BLOCK_amount_exceeds_balance/`, `POLICY_BLOCK_too_many_decimals/`, `POLICY_BLOCK_same_account/`
- `POLICY_BLOCK_non_positive_amount/` - `amount <= 0`, the ordered gate's first rule; judged before the browser
  opens, same as the other amount rules below it
- `POLICY_BLOCK_supervisor_rejected/`, `POLICY_BLOCK_escalation_timeout/` (the ticket records `timeout`, distinct from `reject`)

**HARD_FAILURE**
- `HARD_FAILURE_dispatch_unverified/` - Transfer clicked, confirmation never seen: waits 60s, never clicks again, opens a ticket
- `HARD_FAILURE_malformed_amount/` - not a number, rejected before the browser opens

A subset of these scenarios, run through the agent-facing HTTP interface (the stretch goal) instead of the CLI,
live separately in [`../agent_facing/`](../agent_facing/) — not mixed in here; see its own README for which ones
and why.

**Gaps, noted honestly rather than papered over**
- No folder for a *terminal* `RECOVERABLE` outcome. Every `clear_session` fault was auto-recovered on its one retry, so a
  caller only sees `RECOVERABLE` if that retry also fails (covered by
  `tests/test_replay_faults.py::test_a_session_that_keeps_expiring_is_not_retried_forever`, not produced live).
- Amounts of $1000 or more were not run (they need a source balance over $1000 and a supervisor's click). If ParaBank
  printed a thousands separator on the confirmation, the exact-text confirmation check would not match; that would surface
  as a flagged `dispatch_unverified`, not a silent error.
