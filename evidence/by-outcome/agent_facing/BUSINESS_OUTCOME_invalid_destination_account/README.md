# Agent-facing outcome: BUSINESS_OUTCOME_invalid_destination_account

Source run: `764f8607-483d-419c-bbd2-8963fd858b79` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A nonexistent destination account over HTTP: `BUSINESS_OUTCOME` / `invalid_account`, raised at the destination dropdown (step 3) and named by parameter (`{{to_account}}`), so the model can tell the user it is the recipient account that is wrong, not the source.

Command shape: `the agent is asked to transfer to an account that does not exist`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: invalid_account`
- failure at step 3: expected `option_present combobox "to account #" = {{to_account}}`, observed `known condition, mapped to invalid_account`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790423145.json`](../../../agent_demo/transcript-1790423145.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
