# Replay outcome: BUSINESS_OUTCOME_invalid_destination_account

Source run: `8640138f-0f83-4f15-a4b0-f37bbd1fef8c` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A destination account that does not exist. The destination dropdown never offers that account, the wait on its option times out, and the mapped condition gives `business_outcome: invalid_account`. Nothing is submitted.

Command shape: `replay ... --param to_account=<nonexistent> ...`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: invalid_account`
- failure at step 3: expected `option_present combobox "to account #" = {{to_account}}`, observed `known condition, mapped to invalid_account`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
