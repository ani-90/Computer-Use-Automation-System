# Replay outcome: HTTP_POLICY_BLOCK_same_account

Source run: `6b251114-d056-4323-a374-50584b7980d5` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Same account in both positions over HTTP: `POLICY_BLOCK` (`from_account and to_account must be different`), no browser.

Command shape: `the agent is told to call the tool with the same account twice`

Result:
- `status: POLICY_BLOCK`
- failure at step -1: expected `policy allow`, observed `from_account and to_account must be different`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790416934.json`](../../../agent_demo/transcript-1790416934.json).

Files: `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
