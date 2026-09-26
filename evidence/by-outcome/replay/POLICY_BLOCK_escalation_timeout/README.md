# Replay outcome: POLICY_BLOCK_escalation_timeout

Source run: `eb3b99dd-689d-4aac-a423-fa785efeabbf` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Nobody answers within the escalation window (60s). A timeout is not an active decision, so the ticket records `decision: timeout`, distinct from `reject`, and the run ends `POLICY_BLOCK` with nothing dispatched.

Command shape: `replay ... --param amount=101   (do nothing for 60s)`

Result:
- `status: POLICY_BLOCK`
- failure at step 5: expected `a captured approval click`, observed `no human response within the escalation window`
- ticket `ticket-5b47247b-5434-4468-b2ba-2e1245f79818.json`: `status: resolved`, `decision: timeout`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
