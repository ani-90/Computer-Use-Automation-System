# Agent-facing outcome: SUCCESS

Source run: `6502ef99-e4dd-4743-a079-d604e92b2a6a` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The agent-facing path (`api.py` -> `invoke_capability` -> the same `ReplayEngine`). The model discovers the tool from the catalog, calls it with `amount: '5'`, and the service returns `SUCCESS`; the model reports the confirmation and new balance.

Command shape: `the agent is asked to transfer 5 dollars`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `side_effects: committed` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790422625.json`](../../../agent_demo/transcript-1790422625.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
