# Discovery outcome: DEAD_END, dispatch unverified

Source run: `evidence/1ef82656-0f0a-4446-b3c3-0f0b0d2d0dac` (copied here in full).

Discovery moves real money too — it has to, to observe what success looks like. This run proves the
consequence of that is handled the same way replay's own `dispatch_unverified` trigger handles it, not
silently: the real transfer dispatched and was confirmed (`confirmation_text`/`new_balance`/`transaction_id`
are all populated in `result.json`), but the run itself did not end `SUCCESS` — an extra, deliberately
unsatisfiable requirement was added to the goal for this one evidence run (asking for a wire routing
reference ParaBank never shows anywhere), so the agent got stuck immediately after the real work was
already done, and ended `DEAD_END` / `report_stuck`.

Because a dispatch genuinely happened before the run failed, this is exactly the case
`DiscoveryRun._result()` is built to catch: `side_effects: "unverified"` and an open ticket
(`ticket-563f9ce9-8e6a-42fb-81de-148f7d21adf3.json`), pointing at step 14 (`button "Transfer"`), with its
own screenshot. The ticket's `procedure` carries the real inputs, amount, balance beforehand and dispatch
timestamp — redacted on disk exactly like every other write to `/evidence/` (`from_account`/`to_account`
show `[REDACTED]`; the amount and balance don't, by design, since neither is account-shaped).

This is a real, non-goal-file version of the same taxonomy every other outcome here uses: the run is not
pretended safe to just re-run, the way an ordinary `DEAD_END` is. `capability: null` on the ticket, since no
artifact exists yet at this point in the pipeline — the engine names no page of any app, same as replay's
version of this ticket.

The engineered goal that produced this run (real transfer instruction plus the one unsatisfiable extra ask)
is not committed — it exists only to reliably reproduce this exact scenario for evidence, rather than
gambling on where an ordinary run happens to get stuck.

Files: `transcript.jsonl`, `trace.json`, `log.jsonl`, `step-*.png`, `result.json`,
`ticket-563f9ce9-8e6a-42fb-81de-148f7d21adf3.json`.

Credentials and account numbers grepped clean; every screenshot reviewed before inclusion.
