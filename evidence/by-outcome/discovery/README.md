# Discovery outcomes

One subfolder per distinct `stop_reason` the Discovery Agent actually produced live, each a full copy of that run's
evidence, curated and reviewed (credentials/account numbers grepped clean, screenshots viewed) before being placed here.
See each subfolder's own `README.md` for why that run was picked.

- `SUCCESS/` - the full flow discovered end to end, all four outputs populated. **This is the run the committed artifact
  was compiled from** (`created_from` in `capabilities/transfer_funds.json`).
- `DEAD_END/` - the transfer itself succeeded, but a later action the agent attempted was denied by the Policy Gate
  (which runs in Discovery too, not only Replay).
- `DEAD_END_dispatch_unverified/` - the transfer dispatched and was confirmed, but the run still ended `DEAD_END`
  afterward — discovery's own version of replay's `dispatch_unverified` trigger: `side_effects: "unverified"` and a
  real ticket, not a silent "safe to re-run."
- `MAX_STEPS_EXCEEDED/` - the step cap is actually enforced, not a constant sitting unused.
- `TIMEOUT/` - the wall-clock budget is actually enforced.

The last three (`DEAD_END`, `MAX_STEPS_EXCEEDED`, `TIMEOUT`) were produced by earlier versions of the code
and are kept as evidence that each guard rail works. `DEAD_END_dispatch_unverified` was produced against the
final code, with a goal deliberately engineered to reproduce that exact scenario reliably (see its own
README).
