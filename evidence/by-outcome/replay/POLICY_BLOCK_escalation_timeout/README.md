# Replay outcome: POLICY_BLOCK_escalation_timeout

Source run: `5426cc1f-3027-42cf-b3df-8984dea64c3c` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Nobody answers within the escalation window (60s). A timeout is not an active decision, so the ticket records `decision: timeout`, distinct from `reject`, and the run ends `POLICY_BLOCK` with nothing dispatched.

Command shape: `replay ... --param amount=101   (do nothing for 60s)`

Result:
- `status: POLICY_BLOCK`
- failure at step 5: expected `a captured approval click`, observed `no human response within the escalation window`
- ticket `ticket-a1fa502e-ca52-44b8-bad9-ec1e5427f114.json`: `status: resolved`, `decision: timeout`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
