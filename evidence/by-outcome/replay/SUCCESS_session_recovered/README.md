# Replay outcome: SUCCESS_session_recovered

Source run: `02b36b70-3ec3-4438-928d-5d679510b728` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Auto-recovery from a real session expiry (Phase 8). The fault genuinely clears the browser's cookies; the next step fails its checkpoint for real, the session probe confirms the expiry, and the engine re-authenticates with the fixed service account and retries that one step, once. See the `"recovered"` entry in `trace.json`. No human is involved.

Command shape: `replay ... --inject-faults --fault-step 0 --fault-type clear_session`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
