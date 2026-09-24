# Replay outcome: HARD_FAILURE

Source run: `evidence/replay/6e8e73ba-b87d-40eb-9c13-fed0459aaa5f` (copied here in full).

Picked over a generic "missing element" failure because this is the more important, genuinely
subtle case: a real regression test proven live. The supervisor actually clicked Transfer for
real ($188 genuinely moved — see step-05.png, "Transfer Complete!"), then typed `reject` by
mistake. A typed word is never trusted alone: the engine checks what was actually captured
against it, finds a click matching the submission button, and refuses to guess which of the two
signals is true. `failure_detail.observed`:

> "reject was signaled but a click matching the submission button was captured; the outcome
> cannot be trusted either way"

This is deliberately **not** reported as `POLICY_BLOCK` ("Transfer was never dispatched") because
that would be a lie — money genuinely moved. It's also not silently accepted as `SUCCESS`,
because the human's own signal said reject. `HARD_FAILURE` is the honest answer: something is
wrong and it needs a human to look, not an automated guess either way.

Files: `trace.json`, `log.jsonl`, `result.json`, `ticket-dd8ee70d-....json`, `prelude-*.png`,
`step-*.png`.

Credentials and account numbers grepped clean; every screenshot reviewed before inclusion.
