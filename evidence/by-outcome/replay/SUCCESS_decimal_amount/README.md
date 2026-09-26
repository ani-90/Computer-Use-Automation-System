# Replay outcome: SUCCESS_decimal_amount

Source run: `f63d0031-ab2a-4beb-9dba-23b008dd437e` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A non-whole amount. `1.5` is typed as `1.50`, checked against `$1.50 has been transferred...` on the confirmation page and searched as `1.50` in the ledger, all from one `{{amount:money}}` placeholder in the artifact. An earlier artifact hardcoded `.00` after the amount, so any non-whole amount moved the money and then failed to find its confirmation. `1.33`, `3.3` and `1.300000` (typed and sent as `1.30`) were also run live with the same result.

Command shape: `replay ... --param amount=1.5`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
