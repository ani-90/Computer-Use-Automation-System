# Replay outcome: BUSINESS_OUTCOME (invalid_account)

Source run: `evidence/replay/491be9bf-2b0b-4a4b-8f05-7318d0e4136f` (copied here in full).

ParaBank's transfer dropdowns only ever list real accounts, so a nonexistent `to_account` can't
be submitted through the UI at all — it simply never appears as an option. The compiler emits an
`error_mapping` on the destination-account select step: when its `option_present` wait times out
looking for exactly that account number, the engine recognizes it as a known, mapped answer
(`business_outcome: "invalid_account"`) rather than a generic, unexplained `HARD_FAILURE`.

`result.json`: `status: "BUSINESS_OUTCOME"`, `business_outcome: "invalid_account"`.

This run also proves a real CLI bug fix: the terminal now actually prints
`business_outcome: invalid_account` under the status line — earlier it printed only the bare
status, telling an operator nothing about *why*.

Files: `trace.json`, `log.jsonl`, `result.json`, `prelude-*.png`, `step-*.png` (the form sits
fully filled in — both account dropdowns masked — with the destination account never resolvable).

Credentials and account numbers grepped clean; every screenshot reviewed before inclusion.
