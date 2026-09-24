# Replay outcome: POLICY_BLOCK

Source run: `evidence/replay/62bb7859-c786-4c8d-92e3-b78f5cb654d7` (copied here in full).

Picked over a plain pre-flight block (e.g. `amount <= 0`, blocked before the browser even opens)
because this one exercises the more interesting path: an **escalation that was cleanly rejected**.
The amount was over the approval threshold, the ticket opened (`status: open` → `resolved`,
`decision: "reject"`), and the supervisor declined — `captured_human_actions` is genuinely empty,
proving Transfer was never dispatched (it never shows up as a browser action in the logs).
`failure_detail.observed` reads `"rejected by the supervisor"`.

Files: `trace.json`, `log.jsonl`, `result.json`, `ticket-ec5a70d1-....json`, `prelude-*.png`,
`step-*.png` (the form sits fully filled in, never submitted — step-05.png).

Credentials and account numbers grepped clean; every screenshot reviewed before inclusion.
