# Agent-facing capability interface — demo transcripts

## What this is

This is the stretch goal, not core scope: an HTTP surface that exposes the `transfer_funds` capability the same way a
real agent platform's tool-execution layer would consume it — a catalog endpoint an agent can discover tools from,
and a typed invoke endpoint that runs the real, unmodified Replay Engine underneath. No new logic lives at that HTTP
boundary; it's a thin shell that stays free of any LLM import, same as the engine itself.

The demo script plays the role of that platform's executor, not "an agent" in its own right:

1. **Fetch the catalog** over real HTTP (`GET /capabilities`) — the tool schema, generated straight from the
   capability's own `inputs`.
2. **Register it as tools with Claude** and give the model a plain-English instruction (e.g. *"transfer $5 from
   account X to account Y"*).
3. When the model decides to call the tool, **make a real HTTP `POST`** to
   `/capabilities/transfer_funds/invoke` with the arguments the model generated. This is the one moment that opens a
   real browser and moves real money in the ParaBank sandbox — everything before it is just API/LLM traffic.
4. **Feed the tool result back to the model** for its final, human-facing reply.

Each file here is the full, redacted transcript of one such round trip: the catalog, the model's first response
(including the tool call it decided to make), the raw HTTP request and response, and the model's final reply. This is
the same redaction the rest of `/evidence/` uses — applied to everything printed to the terminal too, not only what's
saved, since the conversation itself (the instruction, the model-generated arguments) carries real account numbers
just as much as the tool result does.

**What this does and doesn't prove.** It proves the capability is genuinely callable as a tool by an LLM-driven
caller, not just from our own CLI — same engine, same outcome taxonomy, same `side_effects` retry contract, reached
over a real network boundary. It does **not** prove a production-shaped service: this demo is synchronous and
unauthenticated, and — the one real behavioral difference from the CLI — **there is no human callback over HTTP**.
An amount over the approval threshold has no browser to hand off and no terminal to wait on a decision from, so it is
a `POLICY_BLOCK` here, never a ticket. See `REPORT.md` (heading 1) for the production shape this stands in for.

## The eight outcomes

Eight transcripts, one per scenario, each paired with the replay evidence it triggered under
[`../by-outcome/agent_facing/`](../by-outcome/agent_facing/).

| Transcript | Instruction (paraphrased) | What it demonstrates | Outcome | Replay evidence |
|---|---|---|---|---|
| `transcript-1790422625.json` | transfer a normal amount | the happy path works end-to-end over HTTP, LLM-driven | `SUCCESS` | `SUCCESS` |
| `transcript-1790422741.json` | transfer a decimal amount (`1.75`) | the model reproduces a non-integer amount correctly; money typing survives an LLM as the caller, not just the CLI | `SUCCESS` | `SUCCESS_decimal_amount` |
| `transcript-1790422807.json` | transfer an amount with more than 2 decimal places | rejected before any browser opens, on the same validation path as the CLI | `POLICY_BLOCK` | `POLICY_BLOCK_too_many_decimals` |
| `transcript-1790422845.json` | transfer between the same account twice | `distinct_inputs` enforced the same way over HTTP | `POLICY_BLOCK` | `POLICY_BLOCK_same_account` |
| `transcript-1790422912.json` | transfer from a nonexistent account | a known ParaBank error message maps to a named business outcome, not a generic crash | `BUSINESS_OUTCOME` / `invalid_account` | `BUSINESS_OUTCOME_invalid_account` |
| `transcript-1790423145.json` | transfer to a nonexistent account (the model names the recipient, not the sender, as invalid) | the same mapping catches the error on either side of the transfer | `BUSINESS_OUTCOME` / `invalid_account` | `BUSINESS_OUTCOME_invalid_destination_account` |
| `transcript-1790422982.json` | transfer an amount over the approval threshold | **the one deliberate behavioral difference from the CLI**: no human to hand the browser to over HTTP, so this blocks instead of escalating — no ticket is opened | `POLICY_BLOCK` | `POLICY_BLOCK_over_threshold` |
| `transcript-1790422288.json` | transfer a normal amount, with the Transfer request held for 90s by the operator-only `CUA_FAULT` switch | the irreversible-action rule live over HTTP: Transfer is dispatched, confirmation never arrives in time, the engine never re-clicks, a ticket opens; the caller gets `ticket_id`/`run_id` and `side_effects: "unverified"` but never the real `procedure` (that's terminal-only, and there is no terminal here to print to) | `HARD_FAILURE` / `dispatch_unverified` | `HARD_FAILURE_dispatch_unverified` |

## Notes

- The last transcript's fault was set in the service's own environment before it started
  (`CUA_FAULT=transient_fail:5:**/*transfer*:90000`), never from a request — a caller has no way to trigger this
  itself. The model quotes the ticket ID in full in its final reply; nothing about it was redacted, since `ticket_id`
  is explicitly exempt from pattern-based redaction (same rule as `run_id`).
- In `transcript-1790422807.json` the amount reads `1.[REDACTED]`: the redactor masks any 5–12 digit run as a
  possible account number, and the five digits after the decimal point happen to match that shape. Cosmetic — the
  block reason stays fully readable.
- A model may decline to call a tool it finds odd (e.g. an ambiguous or suspicious instruction); where a scenario
  needed the call to actually happen, the instruction given to it says so plainly.
- Every transcript here was grepped for the real service-account username and password (both absent everywhere), and
  every tool result carries its `run_id` and, where relevant, `ticket_id` intact.
