# Replay outcome: HTTP_SUCCESS

Source run: `0fff2030-5f4f-4038-b806-654d96b9f49b` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The agent-facing path (`api.py` -> `invoke_capability` -> the same `ReplayEngine`). The model discovers the tool from the catalog, calls it with `amount: '5'`, and the service returns `SUCCESS`; the model reports the confirmation and new balance.

Command shape: `the agent is asked to transfer 5 dollars`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790416751.json`](../../../agent_demo/transcript-1790416751.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
