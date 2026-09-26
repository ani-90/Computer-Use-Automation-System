# Replay outcome: HTTP_POLICY_BLOCK_too_many_decimals

Source run: `12491b6f-058f-48d1-94fc-dbaf4bd421b5` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

More than 2 decimals over HTTP: `POLICY_BLOCK`, before any browser opens. In the transcript the amount reads `1.[REDACTED]`: the redactor masks any 5-12 digit run as a possible account number, and the five digits after the point look like one. Cosmetic; the block reason is still readable.

Command shape: `the agent is told to call the tool with amount=1.98484`

Result:
- `status: POLICY_BLOCK`
- failure at step -1: expected `policy allow`, observed `amount supports at most 2 decimal places (USD)`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790422807.json`](../../../agent_demo/transcript-1790422807.json).

Files: `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
