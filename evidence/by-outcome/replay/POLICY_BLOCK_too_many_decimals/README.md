# Replay outcome: POLICY_BLOCK_too_many_decimals

Source run: `0a709272-f151-412e-9f45-2084282db772` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

An amount with more than 2 decimal places is rejected before the browser opens (`step -1`): `amount supports at most 2 decimal places (USD)`. Found by hand: ParaBank accepts such an amount, moves the unrounded value, shows the rounded one and is left with a balance it can never format, so every page then fails (see `recon-notes.md`). No browser, so no screenshots.

Command shape: `replay ... --param amount=1.98484`

Result:
- `status: POLICY_BLOCK`
- failure at step -1: expected `policy allow`, observed `amount supports at most 2 decimal places (USD)`
- `llm_calls: 0`

Files: `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
