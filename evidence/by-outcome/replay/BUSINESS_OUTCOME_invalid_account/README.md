# Replay outcome: BUSINESS_OUTCOME_invalid_account

Source run: `11f9e42e-216b-428e-90b9-8b5fbc51856b` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A source account that does not exist. Step 0 waits for that account's balance row, times out, and the artifact's own `error_mapping` turns it into the known answer `business_outcome: invalid_account`, not a crash. No transfer is attempted. (An earlier artifact returned `HARD_FAILURE` here: the compiler carried a dropped duplicate read's check into step 0's precondition, which pre-empted this mapping. Fixed in the compiler.)

Command shape: `replay ... --param from_account=<nonexistent> ...`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: invalid_account`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
