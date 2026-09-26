# Replay outcome: HTTP_SUCCESS_decimal_amount

Source run: `ce93c1ff-7fba-4097-a864-7c2be3c3a4dc` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A decimal amount through the language model: `amount: '1.75'` travels as a string, is typed as `1.75`, and is confirmed as `$1.75 has been transferred...` on the page.

Command shape: `the agent is asked to transfer 1.75 dollars`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790416844.json`](../../../agent_demo/transcript-1790416844.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
