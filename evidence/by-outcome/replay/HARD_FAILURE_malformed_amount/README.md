# Replay outcome: HARD_FAILURE_malformed_amount

Source run: `2ce41c9c-99ae-479c-b1f9-926005d9546b` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Not a number: `HARD_FAILURE` (`amount is not a valid decimal`) before the browser opens. Malformed input is a `HARD_FAILURE`; well-formed input that breaks a contract rule (too many decimals, same account) is a `POLICY_BLOCK`. `NaN` used to be accepted as a number and crashed the engine on the first comparison. No screenshots.

Command shape: `replay ... --param amount=abc`

Result:
- `status: HARD_FAILURE`
- failure at step -1: expected `valid parameters`, observed `amount is not a valid decimal`
- `llm_calls: 0`

Files: `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
