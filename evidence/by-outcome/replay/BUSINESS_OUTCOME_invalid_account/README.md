# Replay outcome: BUSINESS_OUTCOME_invalid_account

Source run: `54a10fe6-3831-4af1-bf1e-82a4b673f083` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A source account that does not exist. Step 0 waits for that account's balance row, times out, and the artifact's own `error_mapping` turns it into the known answer `business_outcome: invalid_account`, not a crash. No transfer is attempted. (An earlier artifact returned `HARD_FAILURE` here: the compiler carried a dropped duplicate read's check into step 0's precondition, which pre-empted this mapping. Fixed in the compiler.)

Command shape: `replay ... --param from_account=<nonexistent> ...`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: invalid_account`
- failure at step 0: expected `element_visible cell[row "{{from_account}}", "Balance*"]`, observed `known condition, mapped to invalid_account`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
