# goals/

`transfer_funds.json` **is a goal statement**, not the artifact — the input to the discovery agent: only the target
outcome and the outputs wanted (`confirmation_text`, `new_balance`, `transaction_id`), plus where to start and which
env vars hold credentials. It states no ParaBank page name, element ID, or the Admin Page — the agent has to find
those itself. `python -m cua.cli discover ...` reads this once, before any artifact exists.

Not to be confused with the file of the same name in
[`../capabilities/`](../capabilities/transfer_funds.json) — that one is the compiled *output* discovery produces,
read on every replay from then on, with no LLM involved. This file is only ever read once, at discovery time.
