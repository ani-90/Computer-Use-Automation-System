# Replay outcome: HARD_FAILURE_dispatch_unverified

Source run: `d8b91eae-30c5-4e6b-a1d0-a7c0cffea0a0` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The engine clicked Transfer (irreversible) and the confirmation never appeared: the fault holds the request for 90s and ParaBank answers with its internal-error page (`step-05.png`). The engine waits 60s on the same page and then stops. It never clicks Transfer again, does not probe the session or re-login, opens a ticket (`status: open`) holding the inputs, the balance before and the steps for checking the ledger, and reports `HARD_FAILURE` with `business_outcome: dispatch_unverified` ("the transfer may or may not have posted - verify before any retry"). The operator verified by hand: the source balance was unchanged, so the transfer had not posted. The ticket's `procedure` here is redacted; the CLI prints the real one to the operator's terminal only.

Command shape: `replay ... --inject-faults --fault-step 5 --fault-type transient_fail --fault-url-pattern "**/*transfer*" --fault-delay-ms 90000`

Result:
- `status: HARD_FAILURE`
- `business_outcome: dispatch_unverified`
- failure at step 5: expected `wait element_visible`, observed `the transfer may or may not have posted — verify before any retry`
- ticket `ticket-517f1163-f6de-483a-a3a5-0f6172d1fb79.json`: `status: open`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
