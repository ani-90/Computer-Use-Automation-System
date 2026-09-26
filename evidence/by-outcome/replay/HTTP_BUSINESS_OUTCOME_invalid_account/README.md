# Replay outcome: HTTP_BUSINESS_OUTCOME_invalid_account

Source run: `a521997f-6351-4af6-84ae-5e03e944378d` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

An invalid account over HTTP: `BUSINESS_OUTCOME` / `invalid_account`, recognized by the artifact's `error_mapping`.

Command shape: `the agent is asked to transfer from an account that does not exist`

Result:
- `status: BUSINESS_OUTCOME`
- `business_outcome: invalid_account`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790416991.json`](../../../agent_demo/transcript-1790416991.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
