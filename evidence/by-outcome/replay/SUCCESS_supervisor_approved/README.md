# Replay outcome: SUCCESS_supervisor_approved

Source run: `0efa2008-654c-48aa-affd-15fcabe3dc77` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Human escalation (Phase 7). 101 is over the approval threshold (100), so the Transfer step escalates and hands the live browser to a person. The supervisor's own click on Transfer is the approval; the engine never dispatches it. The ticket moves `open` -> `resolved` with `decision: approve` and the captured click.

Command shape: `replay ... --param amount=101   (then click Transfer yourself, press Enter)`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- ticket `ticket-80942c62-2a2a-4d10-9eeb-824312253c9c.json`: `status: resolved`, `decision: approve`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
