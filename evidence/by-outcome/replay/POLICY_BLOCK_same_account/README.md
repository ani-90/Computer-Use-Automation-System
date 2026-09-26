# Replay outcome: POLICY_BLOCK_same_account

Source run: `66a64559-4952-4eed-a072-71af7c9525e2` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Two inputs that must differ are equal. A rule the caller's own request breaks is a `POLICY_BLOCK`, the same on the CLI, in the engine and over HTTP, raised before the browser opens. No screenshots.

Command shape: `replay ... --param from_account=X --param to_account=X`

Result:
- `status: POLICY_BLOCK`
- failure at step -1: expected `policy allow`, observed `from_account and to_account must be different`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
