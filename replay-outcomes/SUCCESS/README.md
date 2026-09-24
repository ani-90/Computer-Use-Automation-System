# Replay outcome: SUCCESS

Source run: `evidence/replay/a6eca63b-da86-4c05-a06f-b822693c00ef` (copied here in full).

The most comprehensive single replay this project produced — two major subsystems exercised
together in one real run, both ending cleanly in `SUCCESS`:

1. **Fault injection + auto-recovery (Phase 8, `--inject-faults`)**: `clear_session` genuinely
   deletes the browser's cookies at step 0. The next step fails for real, the auth probe confirms
   a genuine session expiry (not a guess), and the engine auto re-authenticates with the fixed
   service account and retries that one step — see the `"recovered"` entry in `trace.json`. No
   human is ever involved in this path.
2. **Human escalation (Phase 7)**: the amount (101) is over the approval threshold (100), so the
   Transfer step escalates — see the `"escalate"` gate verdict at step 5 and
   `ticket-1e3f0d37-....json`, whose `status` moved `open` → `resolved` and whose
   `captured_human_actions` holds the supervisor's own real click on Transfer (the engine never
   dispatches it).

Full outputs populated: `confirmation_text`, `new_balance`, `transaction_id`. `llm_calls: 0`.

Files: `trace.json`, `log.jsonl`, `result.json`, `ticket-*.json`, `prelude-*.png`, `step-*.png`.

Credentials and account numbers grepped clean; every screenshot reviewed before inclusion.
