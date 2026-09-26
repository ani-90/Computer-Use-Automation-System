# Replay outcome: SUCCESS_click_then_reject

Source run: `351b3a12-59bd-45fd-8e24-86c220148334` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The human's two channels disagree: they really clicked Transfer, then typed `reject`. The word can neither create nor erase a submission, only the page can: the engine verifies the confirmation is on screen and reports `SUCCESS`. The ticket keeps `decision: reject` and adds a `note`: the click came before the signal, the transfer was verified executed, and the signal arrived post-dispatch and could not be applied. Before this rule the same situation was a `HARD_FAILURE` that contradicted what had happened.

Command shape: `replay ... --param amount=101   (click Transfer, THEN type reject)`

Result:
- `status: SUCCESS`
- outputs: confirmation_text, new_balance, transaction_id
- ticket `ticket-18b6b1ef-ee2c-44ab-9f5f-667f7ca9836c.json`: `status: resolved`, `decision: reject`
- `side_effects: committed` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `log.jsonl`, `prelude-*.png`, `result.json`, `step-*.png`, `ticket-*.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number (the CLI's operator block, which does, is terminal-only); screenshots reviewed before inclusion.
