# Agent-facing outcome: SUCCESS_decimal_amount

Source run: `2d120cac-d167-4e47-b79c-eb24f8ca88d3` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A decimal amount through the language model: `amount: '1.75'` travels as a string, is typed as `1.75`, and is confirmed as `$1.75 has been transferred...` on the page.

Command shape: `the agent is asked to transfer 1.75 dollars`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `side_effects: committed` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790422741.json`](../../../agent_demo/transcript-1790422741.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
