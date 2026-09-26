# Replay outcome: SUCCESS_supervisor_approved

Source run: `8d7b134d-737f-405f-9130-99a6abf25fc6` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Human escalation (Phase 7). 101 is over the approval threshold (100), so the Transfer step escalates and hands the live browser to a person. The supervisor's own click on Transfer is the approval; the engine never dispatches it. The ticket file is written `open` the moment the escalation fires and rewritten `resolved` on the decision (`decision: approve`, the captured click, `opened_at`/`resolved_at` in UTC, the artifact, the step and its screenshot); this folder holds the final state, and the transition itself is proven by `tests/test_escalation.py::test_the_ticket_is_a_real_json_file_moving_from_open_to_resolved`.

Command shape: `replay ... --param amount=101   (then click Transfer yourself, press Enter)`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- ticket `ticket-7d4f1717-886d-4c1b-82cf-5871fc9fcec6.json`: `status: resolved`, `decision: approve`
- `side_effects: committed` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
