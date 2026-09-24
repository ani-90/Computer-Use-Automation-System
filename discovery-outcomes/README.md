# Discovery outcomes

One subfolder per distinct `stop_reason` the Discovery Agent actually produced live, each a full
copy of that run's evidence, curated and reviewed (credentials/account numbers grepped clean,
every screenshot viewed) before being placed here. See each subfolder's own `README.md` for why
that specific run was picked over other candidates.

- `SUCCESS/` — the full flow discovered end to end, all four outputs populated.
- `DEAD_END/` — the transfer itself succeeded, but a later action the agent attempted was denied
  by the Policy Gate (which runs in Discovery too, not only Replay).
- `MAX_STEPS_EXCEEDED/` — the step cap is actually enforced, not a constant sitting unused.
- `TIMEOUT/` — the wall-clock budget is actually enforced.
