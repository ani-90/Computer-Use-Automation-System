# capabilities/

`transfer_funds.json` **is the artifact** — the versioned, compiled capability the Replay Engine reads. It's what
`python -m cua.cli replay --capability capabilities/transfer_funds.json ...` runs, with no LLM involved
(`llm_calls: 0` on every run). It was produced once by the compiler from a real discovery run (see its
`created_from`), never hand-written or hand-edited.

Not to be confused with the file of the same name in [`../goals/`](../goals/transfer_funds.json) — that one is the
*input* to discovery (a goal statement: what outcome and outputs are wanted), read once, before this artifact exists.
This file is the *output* of discovery, read on every replay, forever after.
