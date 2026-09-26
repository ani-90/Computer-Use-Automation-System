# Replay outcome: SUCCESS_session_recovered

Source run: `c67c9b5f-9cac-4616-8a5b-3c6126a4fdb2` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

Auto-recovery from a FAULT-INJECTED session expiry (Phase 8, `--inject-faults`). The fault is operator-requested and the trace says so (an `injected` entry before step 0). It really clears the browser's cookies, so what follows is a real failure: step 0 only reads the page already loaded and passes; step 1 (opening the transfer form) needs a request, fails its checkpoint, the session probe confirms the session is expired, and the engine re-authenticates with the fixed service account and retries that one step, once (the `"recovered"` entry). Nothing in the recovery path knows a fault was involved; a real expiry takes the same path. No human is involved. Each attempt keeps its own screenshot (`*-retry1.png`), so the failed attempt's evidence is not overwritten. The re-login steps report `overview.htm` as their URL because ParaBank serves the login form through a server-side forward while the address bar keeps the page that was requested.

Command shape: `replay ... --inject-faults --fault-step 0 --fault-type clear_session`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- `side_effects: committed` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `step-01-retry1.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
