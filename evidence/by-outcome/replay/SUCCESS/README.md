# Replay outcome: SUCCESS

Source run: `39dc7e0d-dded-4985-8e50-448faa6b1244` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The plain path, deterministic and with `llm_calls: 0`: log in, read the starting balance, fill the Transfer form (the amount is typed as `5.00`, the canonical money form), click Transfer (the one `is_submission` step), read the confirmation sentence and the new balance, then look up the transaction ID. All three outputs are populated.

Command shape: `replay ... --param amount=5`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
