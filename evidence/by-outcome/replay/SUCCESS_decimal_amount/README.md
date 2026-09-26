# Replay outcome: SUCCESS_decimal_amount

Source run: `42cb9ea5-1416-4f68-a071-5d62e99a437d` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A non-whole amount. `1.5` is typed as `1.50`, checked against `$1.50 has been transferred...` on the confirmation page and searched as `1.50` in the ledger, all from one `{{amount:money}}` placeholder in the artifact. An earlier artifact hardcoded `.00` after the amount, so any non-whole amount moved the money and then failed to find its confirmation. `1.33`, `3.3` and `1.300000` (typed and sent as `1.30`) were also run live with the same result.

Command shape: `replay ... --param amount=1.5`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `side_effects: committed` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
