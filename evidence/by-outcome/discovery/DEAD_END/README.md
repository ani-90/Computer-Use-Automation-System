# Discovery outcome: DEAD_END

Source run: `evidence/2a797c52-a756-4286-ada1-f515b7847416` (copied here in full).

`detail: "blocked_by_policy"` — the Policy Gate runs before every action in Discovery too, not
only Replay. Here the transfer itself already succeeded (`confirmation_text`/`new_balance` are
populated in `result.json`), but a later action the agent attempted was denied by policy, and the
run correctly ends `DEAD_END` rather than pretending the disallowed action happened.

Other DEAD_END reasons observed but not included here (only one representative per outcome):
`report_stuck` (the agent gave up, no path forward) and an `llm_error` from the Anthropic API
itself surfacing as a dead end rather than a silent hang.

Files: `transcript.jsonl`, `trace.json`, `log.jsonl`, `step-*.png`, `result.json`.

Credentials and account numbers grepped clean; every screenshot reviewed before inclusion.
