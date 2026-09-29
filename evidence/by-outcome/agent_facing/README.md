# Agent-facing outcomes (stretch goal)

The same `transfer_funds` capability, the same `ReplayEngine`, the same five-outcome taxonomy — but invoked over
HTTP through `api.py` (`GET /capabilities`, `POST /capabilities/transfer_funds/invoke`) instead of the CLI, so an
outside agent can discover and call it. Each folder here is a full copy of one such run's evidence
(`trace.json`, `log.jsonl`, `result.json`, screenshots, and any `ticket-*.json`), curated and reviewed before being
placed here, same as every folder under [`../replay/`](../replay/). Every run: `llm_calls: 0`.

Each folder's own `README.md` names its source run and is paired with the transcript of the call that triggered
it, in [`../../agent_demo/`](../../agent_demo/) — the transcript shows the agent's side (what it called, with what
parameters); the folder here shows the engine's side (what actually happened).

- `SUCCESS/`, `SUCCESS_decimal_amount/`
- `BUSINESS_OUTCOME_invalid_account/`, `BUSINESS_OUTCOME_invalid_destination_account/`
- `POLICY_BLOCK_over_threshold/` (no live handoff over HTTP, so an amount that would escalate on the CLI is a block
  here instead), `POLICY_BLOCK_same_account/`, `POLICY_BLOCK_too_many_decimals/`
- `HARD_FAILURE_dispatch_unverified/`

**What's deliberately not duplicated here:** the outcomes that don't depend on the transport at all (`SUCCESS_session_recovered`,
the login/amount-gate scenarios not listed above) are only proven once, under `../replay/` — re-running every
scenario a second time over HTTP would test the same engine code path twice for no new signal. What's here is
specifically the set that differs or is worth reproving under the agent-facing surface.
