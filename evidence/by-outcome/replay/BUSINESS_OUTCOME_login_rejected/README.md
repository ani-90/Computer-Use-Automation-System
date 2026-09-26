# Replay outcome: BUSINESS_OUTCOME_login_rejected

Source run: `c8dee263-956e-432f-93ef-5ae9cd3efd95` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

A wrong password. Login is the engine's fixed prelude, not an artifact step: ParaBank's real rejection page (`The username and password could not be verified.`) is recognized and reported as `business_outcome: login_rejected`.

Command shape: `PARABANK_PASSWORD set to a wrong value for this one run`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: login_rejected`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
