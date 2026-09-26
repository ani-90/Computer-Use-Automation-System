# Replay outcome: POLICY_BLOCK_amount_exceeds_balance

Source run: `794faf6a-d53c-476f-8cca-6620cfeb3e43` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The Policy Gate blocks at the Transfer step because the amount exceeds the balance it read at step 0. The form is filled (see `step-05.png`) but Transfer is never clicked, so nothing is dispatched.

Command shape: `replay ... --param amount=9000`

Result:
- `status: POLICY_BLOCK`
- failure at step 5: expected `policy allow`, observed `amount exceeds balance`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
