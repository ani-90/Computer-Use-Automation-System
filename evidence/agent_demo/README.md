# Agent-facing capability interface - demo transcripts

`scripts/agent_demo.py` stands in for a production agent platform's tool-execution layer: it fetches the capability
catalog over real HTTP from `cua.api`, registers it as tools with Claude, and, when the model decides to call one, makes a
real HTTP `POST` to `/capabilities/transfer_funds/invoke`, which runs the real `ReplayEngine` underneath. Each file here
is the full transcript of one such round trip (catalog fetch, the model's tool call, the HTTP request/response, the
model's final reply), redacted the same way every other write to `evidence/` is before being saved.

Seven transcripts, each with the replay run it triggered under `../by-outcome/replay/HTTP_*/`:

| Transcript | What it shows | Replay evidence |
|---|---|---|
| `transcript-1790416751.json` | normal transfer, `amount: '5'` -> `SUCCESS` | `HTTP_SUCCESS` |
| `transcript-1790416844.json` | a decimal amount through the model, `amount: '1.75'` -> `SUCCESS` | `HTTP_SUCCESS_decimal_amount` |
| `transcript-1790416895.json` | more than 2 decimals -> `POLICY_BLOCK` before any browser opens | `HTTP_POLICY_BLOCK_too_many_decimals` |
| `transcript-1790416934.json` | same account twice -> `POLICY_BLOCK` | `HTTP_POLICY_BLOCK_same_account` |
| `transcript-1790416991.json` | a nonexistent account -> `BUSINESS_OUTCOME` / `invalid_account` | `HTTP_BUSINESS_OUTCOME_invalid_account` |
| `transcript-1790417062.json` | over the approval threshold, nobody to approve over HTTP -> `POLICY_BLOCK`, no ticket | `HTTP_POLICY_BLOCK_over_threshold` |
| `transcript-1790417799.json` | a lost confirmation -> `HARD_FAILURE` / `dispatch_unverified`, with `ticket_id` and `run_id` and no `procedure` | `HTTP_HARD_FAILURE_dispatch_unverified` |

Notes:
- The last transcript was produced with the operator-only `CUA_FAULT` switch, set in the service's environment before it
  started (never from a request), which holds the Transfer request for 90 seconds. The model quotes the ticket ID in full.
- In `transcript-1790416895.json` the amount reads `1.[REDACTED]`: the redactor masks any 5-12 digit run as a possible
  account number, and the five digits after the decimal point look like one. Cosmetic; the block reason stays readable.
- A model may decline to call a tool it finds odd; where a scenario needed the call to happen, the instruction says so.

Credentials and account numbers grepped clean; every transcript's tool result carries the intact `run_id` and `ticket_id`.
