# Replay outcome: POLICY_BLOCK_supervisor_rejected

Source run: `12f22cc1-8ed3-4be3-a1f6-a9fc7a605247` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Escalation cleanly declined. The supervisor types `reject` and clicks nothing; Transfer is never dispatched. The ticket records `decision: reject`.

Command shape: `replay ... --param amount=101   (type reject)`

Result:
- `status: POLICY_BLOCK`
- failure at step 5: expected `a captured approval click`, observed `rejected by the supervisor`
- ticket `ticket-dc77382d-23c1-4bca-b825-ac09972bf21f.json`: `status: resolved`, `decision: reject`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
