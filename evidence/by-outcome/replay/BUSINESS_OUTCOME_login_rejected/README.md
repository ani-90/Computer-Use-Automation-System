# Replay outcome: BUSINESS_OUTCOME_login_rejected

Source run: `9196f3ef-7fb5-4c50-9602-f7ac3a3ff345` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A wrong password. Login is the engine's fixed prelude, not an artifact step: ParaBank's real rejection page (`The username and password could not be verified.`) is recognized and reported as `business_outcome: login_rejected`.

Command shape: `PARABANK_PASSWORD set to a wrong value for this one run`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: login_rejected`
- failure at step -1: expected `login lands on the accounts overview`, observed `known condition: paragraph "The username and password could not be verified."`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
