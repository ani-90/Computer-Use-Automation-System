# Replay outcome: POLICY_BLOCK_non_positive_amount

Source run: `793ecfd8-e924-4df2-ae44-228d25ad0fb0` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

`amount <= 0` is the first rule in the Policy Gate's ordered amount checks (`<=0` blocks, `>balance` blocks,
`>threshold` escalates). `0` and a negative value are both syntactically valid decimals — parsed cleanly, not
malformed — so this is a `POLICY_BLOCK`, not a `HARD_FAILURE`, same category as too-many-decimals or
same-account. It is judged before the browser opens: this is the one amount rule that can be checked with no
known balance yet (any amount `<= 0` is wrong regardless of what the balance turns out to be), so the engine
asks the gate immediately rather than waiting to navigate and read one. No screenshots.

Command shape: `replay ... --param amount=0`

Result:
- `status: POLICY_BLOCK`
- failure at step -1: expected `policy allow`, observed `amount must be greater than zero`
- `side_effects: none` (the retry contract: `none` safe to retry, `unverified` check the ledger first, `committed` the money moved)
- artifact: `transfer_funds` version `2`, `created_from` the discovery run in `by-outcome/discovery/SUCCESS`
- `llm_calls: 0`

Files: `result.json`, `trace.json`.

Credentials and account numbers grepped clean; the saved files hold no real account number.
