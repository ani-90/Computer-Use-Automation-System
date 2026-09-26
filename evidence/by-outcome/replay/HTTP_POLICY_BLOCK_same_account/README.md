# Replay outcome: HTTP_POLICY_BLOCK_same_account

Source run: `ea7f3570-0e74-4b35-9e5e-496aa5533736` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Same account in both positions over HTTP: `POLICY_BLOCK` (`from_account and to_account must be different`), no browser.

Command shape: `the agent is told to call the tool with the same account twice`

Result:
- `status: POLICY_BLOCK`
- failure at step -1: expected `policy allow`, observed `from_account and to_account must be different`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790422845.json`](../../../agent_demo/transcript-1790422845.json).

Files: `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
