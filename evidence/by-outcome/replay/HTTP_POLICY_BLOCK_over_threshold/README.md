# Replay outcome: HTTP_POLICY_BLOCK_over_threshold

Source run: `2f0a30ac-0f76-4327-bbbf-c03475b34c8f` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

An amount over the approval threshold over HTTP. There is no callback for a human here, so the run ends `POLICY_BLOCK` with no ticket (human approval works from the CLI; over HTTP it would need an async job and a webhook, a named cut).

Command shape: `the agent is asked to transfer 150 dollars`

Result:
- `status: POLICY_BLOCK`
- failure at step 5: expected `policy allow`, observed `amount exceeds approval threshold`
- `llm_calls: 0`

The matching transcript: [`../../../agent_demo/transcript-1790417062.json`](../../../agent_demo/transcript-1790417062.json).

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
