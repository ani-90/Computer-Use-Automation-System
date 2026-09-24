# Replay outcomes

One subfolder per distinct outcome the Replay Engine actually produced live, each a full copy of
that run's evidence, curated and reviewed (credentials/account numbers grepped clean, every
screenshot viewed) before being placed here. See each subfolder's own `README.md` for why that
specific run was picked over other candidates.

- `SUCCESS/` — the most comprehensive run: fault-injected auto-recovery and human escalation
  approval, together, in one run.
- `POLICY_BLOCK/` — an escalation cleanly rejected by the supervisor.
- `HARD_FAILURE/` — a real click captured alongside a typed "reject"; the engine refuses to guess
  which signal is true.
- `BUSINESS_OUTCOME_invalid_account/` — a destination account that doesn't exist, recognized via
  `error_mapping` as a known answer, not a crash.
- `BUSINESS_OUTCOME_login_rejected/` — a wrong password, recognized via ParaBank's real rejection
  page.

**Gap, noted honestly rather than papered over**: there is no folder for a *terminal*
`RECOVERABLE` outcome. Every `clear_session` fault injected during Phase 8 testing was
successfully auto-recovered by the engine (re-authenticate, retry, continue) — meaning the run
that actually reaches a human or a caller as `RECOVERABLE` only happens if that one retry itself
also fails (see `tests/test_replay_faults.py::test_a_session_that_keeps_expiring_is_not_retried_forever`
for the unit-tested version of that path). No such run occurred live this session.
