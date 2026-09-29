# Agent-facing outcome: BUSINESS_OUTCOME_invalid_account

Source run: `1d96b3bd-03e0-428d-9ae6-df9765abbc8f` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

An invalid account over HTTP: `BUSINESS_OUTCOME` / `invalid_account`, recognized by the artifact's `error_mapping`.

Command shape: `the agent is asked to transfer from an account that does not exist`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: invalid_account`
- failure at step 0: expected `element_visible cell[row "{{from_account}}", "Balance*"]`, observed `known condition, mapped to invalid_account`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790422912.json`](../../../agent_demo/transcript-1790422912.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
