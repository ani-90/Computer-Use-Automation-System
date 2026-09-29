# Agent-facing outcome: HARD_FAILURE_dispatch_unverified

Source run: `42e3f71c-5aa6-480c-a42d-4b6ddbf70f76` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The unverified dispatch over HTTP. The service returns `HARD_FAILURE` / `dispatch_unverified` with the ticket's `ticket_id` and `run_id` and, on purpose, no `procedure` (it embeds the run's real parameters and is for the operator). The model relays the whole ticket ID to the user and tells them to verify before any retry. The saved ticket file holds the redacted procedure. The operator verified the balance was unchanged, so nothing had posted.

Command shape: `service started with the operator-only CUA_FAULT delay set; the agent asked to transfer 5 dollars`

Result:
- `status: HARD_FAILURE`
- `business_outcome: dispatch_unverified`
- failure at step 5: expected `wait element_visible`, observed `the transfer may or may not have posted — verify before any retry`
- ticket `ticket-29b6ed42-3896-493d-bf2c-89978768f324.json`: `status: open`
- `side_effects: unverified` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790422288.json`](../../../agent_demo/transcript-1790422288.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
