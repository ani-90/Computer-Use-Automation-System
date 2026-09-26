# Replay outcome: HTTP_HARD_FAILURE_dispatch_unverified

Source run: `8afb33dc-a928-4807-af2f-3e7ed9dd5037` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The unverified dispatch over HTTP. The service returns `HARD_FAILURE` / `dispatch_unverified` with the ticket's `ticket_id` and `run_id` and, on purpose, no `procedure` (it embeds the run's real parameters and is for the operator). The model relays the whole ticket ID to the user and tells them to verify before any retry. The saved ticket file holds the redacted procedure. The operator verified the balance was unchanged, so nothing had posted.

Command shape: `service started with the operator-only CUA_FAULT delay; the agent asked to transfer 5 dollars`

Result:
- `status: HARD_FAILURE`
- `business_outcome: dispatch_unverified`
- failure at step 5: expected `wait element_visible`, observed `the transfer may or may not have posted — verify before any retry`
- ticket `ticket-ead1d70a-be26-4df8-835d-cd8c40cba55a.json`: `status: open`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790417799.json`](../../../agent_demo/transcript-1790417799.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
