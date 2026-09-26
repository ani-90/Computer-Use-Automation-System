# Discovery outcome: SUCCESS

Source run: `cbc80832-5586-47c1-ac22-4d0a468420ea` (this folder is a full copy of that run's evidence; the raw run folder itself is not committed).

The LLM discovers the whole flow from scratch (log in, read the starting balance, transfer funds, confirm, read the
new balance, look up the transaction ID) with no ParaBank-specific knowledge in its prompt. 23 model calls, 22 counted
steps, about 93 seconds, 158,074 input / 2,662 output tokens (limits: `max_steps=40`, `timeout=420s`).

This is the run that produced the committed artifact. `capabilities/transfer_funds.json` records it in
`created_from`, and `scripts/audit_evidence.py` checks that link resolves to this folder. The compiler (never a hand
edit) wrote `version: 2`: money parameters are `{{amount:money}}`, with no hardcoded `.00`, and no customer name.

All four outputs populated in `result.json`: `source_balance_before`, `confirmation_text` (the full sentence, not just
its heading), `new_balance`, `transaction_id`. The amount used was one never transferred before, so the ledger search
had exactly one match and the transaction ID the agent read is unambiguous.

Earlier runs on the same day led here. Two of them succeeded but compiled artifacts with defects (a hardcoded customer
greeting in step 0's precondition, and a dropped duplicate read whose check replaced step 0's login precondition); one
ended `MAX_STEPS_EXCEEDED` after extracting only the confirmation heading; one ended `DEAD_END` after the agent repeated a
blocked click. Each exposed a real defect that was fixed in the compiler, the goal wording or the blocked-action
feedback, not worked around. Those raw runs are not committed.

Files:
- `transcript.jsonl` - the agent's own reasoning and tool calls
- `trace.json` - the structured, redacted step record
- `log.jsonl` - the raw action/gate log
- `step-*.png` - a masked screenshot per step
- `result.json` - the final DiscoveryResult summary

Credentials and account numbers grepped clean; screenshots reviewed before inclusion.
