# Agent-facing capability interface — demo transcripts

`scripts/agent_demo.py` stands in for a production agent platform's tool-execution layer: it
fetches the capability catalog over real HTTP from `cua.api`, registers it as tools with Claude,
and — when the model decides to call one — makes a real HTTP `POST` to
`/capabilities/transfer_funds/invoke`, which runs the real `ReplayEngine` underneath. Each file
here is the full transcript of one such round trip (catalog fetch, the model's tool call, the
HTTP request/response, the model's final reply), redacted the same way every other write to
`/evidence/` is before being saved.

Three curated here, each proving something distinct:

- `transcript-1790288024.json` — the happy path. Model calls the tool, a real transfer completes,
  the model reports it back in plain language.
- `transcript-1790288167.json` — an amount over the account's balance. `POLICY_BLOCK`, and the
  model explains *why* rather than reporting a generic failure.
- `transcript-1790288082.json` — an invalid destination account. `BUSINESS_OUTCOME`/
  `invalid_account`, recognized as a known answer, not a crash.

The matching real replay evidence (screenshots, trace, log) for each of these lives under
`evidence/replay/<run_id>/` — see each transcript's own `invoke_response` for which `run_id`.
