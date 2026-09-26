# Replay outcome: BUSINESS_OUTCOME_invalid_destination_account

Source run: `7b484cf6-85ef-48be-86f2-9102367f227e` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A destination account that does not exist. The destination dropdown never offers that account, the wait on its option times out, and the mapped condition gives `business_outcome: invalid_account`. Nothing is submitted.

Command shape: `replay ... --param to_account=<nonexistent> ...`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: invalid_account`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
