# Replay outcome: POLICY_BLOCK_supervisor_rejected

Source run: `a56521cc-b094-4725-bab8-2fd4af0501a9` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Escalation cleanly declined. The supervisor types `reject` and clicks nothing; Transfer is never dispatched. The ticket records `decision: reject`.

Command shape: `replay ... --param amount=101   (type reject)`

Result:
- `status: POLICY_BLOCK`
- failure at step 5: expected `a captured approval click`, observed `rejected by the supervisor`
- ticket `ticket-35263125-7787-4c51-a32d-c305b413fdcd.json`: `status: resolved`, `decision: reject`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
