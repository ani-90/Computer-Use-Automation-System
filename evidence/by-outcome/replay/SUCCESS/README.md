# Replay outcome: SUCCESS

Source run: `557d229f-6f1d-4e48-a653-b5bb335ada4b` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The plain path, deterministic and with `llm_calls: 0`: log in, read the starting balance, fill the Transfer form (the amount is typed as `5.00`, the canonical money form), click Transfer (the one `is_submission` step), read the confirmation sentence and the new balance, then look up the transaction ID. All three outputs are populated.

Command shape: `replay ... --param amount=5`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `side_effects: committed` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
