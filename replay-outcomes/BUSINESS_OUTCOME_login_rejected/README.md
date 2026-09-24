# Replay outcome: BUSINESS_OUTCOME (login_rejected)

Source run: `evidence/replay/cb2eb1eb-b632-4244-b50b-5e0b4e61afb2` (copied here in full).

A deliberately wrong password (set for this one process only via a shell-level environment
override — `.env` and the real credentials were never touched) lands on ParaBank's real rejection
page: heading "Error!", paragraph "The username and password could not be verified." — confirmed
live beforehand via a one-off probe script. The engine recognizes this exact page as a known
answer, not a generic login failure.

`result.json`: `status: "BUSINESS_OUTCOME"`, `business_outcome: "login_rejected"`.

Files: `trace.json`, `log.jsonl`, `result.json`, `prelude-*.png` (prelude-02.png shows the actual
rejection page — empty username/password fields, nothing typed leaked into the screenshot).

No step-*.png files: the run never got past the login prelude, so no compiled step ever ran.

Credentials grepped clean, including the real password, the real username, and the deliberately
wrong password that was typed — none appear anywhere in this folder. Every screenshot reviewed.
