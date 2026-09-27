# Computer-Use Automation System

An LLM discovers a UI flow **once**. The flow is compiled into a versioned `capability.json`. A deterministic
Replay Engine, with **no LLM**, runs it from then on, classifies every outcome, hands a person the live browser when
policy requires it, and recovers from a real fault by itself.

The target is a self-hosted [ParaBank](https://parabank.parasoft.com). The one flow: log in, transfer funds between two
accounts, confirm, and read the new balance and the transaction ID.

Design write-up: [`REPORT.md`](REPORT.md). Evidence and how it is audited: [`evidence/README.md`](evidence/README.md).

## Requirements

- Python 3.11 or newer, and Docker (for ParaBank).
- An Anthropic API key, only for **discovery** and the **agent demo**. Replay never calls an LLM.
- A display. The browser is always headed (by design, so a person can take over). The tests need no browser or display.

## 1. Set up

PowerShell:
```powershell
git clone https://github.com/ani-90/Computer-Use-Automation-System.git
cd Computer-Use-Automation-System
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
Copy-Item .env.example .env
```
bash:
```bash
git clone https://github.com/ani-90/Computer-Use-Automation-System.git
cd Computer-Use-Automation-System
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env
```
Then edit `.env`: set `PARABANK_PASSWORD` (any password you choose in step 2) and, for discovery, `ANTHROPIC_API_KEY`.
Run every command below from the repository root.

## 2. Start ParaBank

```bash
docker run -d --name parabank -p 8080:8080 parasoft/parabank
```
Wait about a minute, open <http://localhost:8080/parabank>, and:
1. **Register** a user named `agentdemo` with the password you put in `.env`.
2. Use **Open New Account** to create a second account.
3. Note your two account numbers (`<FROM>` and `<TO>` below). Keep at least $250 in `<FROM>`.

The runs below move real money inside this sandbox. If you need a clean slate, recreate the container
(`docker rm -f parabank`, then the `docker run` above) and register again.

## 3. Check the install (no ParaBank, no API key)

```bash
pytest                                      # 447 tests
ruff check .
python scripts/audit_evidence.py --tracked  # every run_id in the committed evidence agrees: 29 clean, 0 problems
```

## 4. Discover (once, costs about $0.35)

```bash
python -m cua.cli discover --param from_account=<FROM> --param to_account=<TO> --param amount=1.50 --dry-run   # free: shows the goal
python -m cua.cli discover --param from_account=<FROM> --param to_account=<TO> --param amount=1.50 \
    --max-steps 40 --timeout 420 --capability-out scratch/transfer_funds.json
```
The agent gets only the goal, never a page name or selector. On `SUCCESS` the compiler writes the artifact to
`--capability-out`. Without that flag it **overwrites** `capabilities/transfer_funds.json`, the committed artifact. Use a
two-decimal amount you have not transferred before, so the ledger search has exactly one match. A run that ends
`DEAD_END`, `MAX_STEPS_EXCEEDED` or `TIMEOUT` writes no artifact. Its evidence goes to `evidence/<run_id>/`.

Discovery moves real money too — it has to, to observe what success looks like — so it carries the same retry
contract replay does. If a run dispatches the real transfer and then still doesn't reach `SUCCESS`, it prints
`side_effects: unverified` and opens a ticket (same machinery as replay's `dispatch_unverified`), with the real
verification procedure shown to your terminal only. A run that reaches `SUCCESS` prints `side_effects: committed`.

## 5. The artifact

[`capabilities/transfer_funds.json`](capabilities/transfer_funds.json) is what the Replay Engine reads. It records its
`version` and `created_from`, the discovery run it was compiled from (`evidence/by-outcome/discovery/SUCCESS`). Each step
has a `precondition`, `action`, `target` (a ranked locator description, never a raw selector), `parameters`,
`wait_strategy`, `checkpoint` and `error_mapping`. Account numbers and the amount are placeholders
(`{{from_account}}`, `{{amount:money}}`), never literals.

## 6. Replay (free, no LLM)

```bash
python -m cua.cli replay --capability capabilities/transfer_funds.json \
    --param from_account=<FROM> --param to_account=<TO> --param amount=5
```
```
LIVE REPLAY: no LLM is called; this may move money in the sandbox.
evidence: evidence/replay/<run_id>

status: SUCCESS
side_effects: committed
llm_calls: 0
  confirmation_text: $5.00 has been transferred from account #[REDACTED] to account #[REDACTED].
  new_balance: $...
  transaction_id: [REDACTED]
```
Account numbers and transaction IDs print as `[REDACTED]`, on screen and in every saved file. The run's evidence is in the
folder named on the `evidence:` line.

`side_effects` is the **retry contract**, on every result and over HTTP:

| `side_effects` | Meaning | Safe to run again? |
|---|---|---|
| `none` | nothing was dispatched | yes |
| `unverified` | the transfer was dispatched but its outcome could not be confirmed | **no**: check the ledger first |
| `committed` | dispatched and confirmed | not the transfer: a later failure must not be retried away |

Change the inputs to see each outcome:

| Run | Outcome |
|---|---|
| `amount=1.5` | `SUCCESS`, typed and confirmed as `1.50` |
| `from_account=<a number that does not exist>` | `BUSINESS_OUTCOME` / `invalid_account` |
| `amount=9000` | `POLICY_BLOCK`, amount exceeds balance, Transfer never clicked |
| `amount=1.98484` | `POLICY_BLOCK`, more than 2 decimal places, before any browser opens |
| `from_account` = `to_account` | `POLICY_BLOCK`, must be different |
| `amount=101` | over the approval threshold (100): the browser is handed to you. Click **Transfer** yourself, then press Enter, to approve; type `reject` to decline; no answer in 60s is a `POLICY_BLOCK` (timeout) |
| a wrong `PARABANK_PASSWORD` for one run | `BUSINESS_OUTCOME` / `login_rejected` |

For example, a wrong password for one run (`load_dotenv` never overrides a variable that is already set):
```powershell
$env:PARABANK_PASSWORD='wrong'; python -m cua.cli replay --capability capabilities/transfer_funds.json --param from_account=<FROM> --param to_account=<TO> --param amount=5; Remove-Item Env:PARABANK_PASSWORD
```
```bash
PARABANK_PASSWORD=wrong python -m cua.cli replay --capability capabilities/transfer_funds.json --param from_account=<FROM> --param to_account=<TO> --param amount=5
```

**Fault injection**, for testing only. Nothing runs it unless `--inject-faults` is given, and the trace records that it did.

A real session expiry, auto-recovered (`SUCCESS`, with a `recovered` step in the trace):
```bash
python -m cua.cli replay --capability capabilities/transfer_funds.json --param from_account=<FROM> --param to_account=<TO> --param amount=5 --inject-faults --fault-step 0 --fault-type clear_session
```
A Transfer request held for 90 seconds (`HARD_FAILURE` / `dispatch_unverified`, `side_effects: unverified`, an open ticket; about 3 minutes):
```bash
python -m cua.cli replay --capability capabilities/transfer_funds.json --param from_account=<FROM> --param to_account=<TO> --param amount=5 --inject-faults --fault-step 5 --fault-type transient_fail --fault-url-pattern "**/*transfer*" --fault-delay-ms 90000
```

After Transfer is dispatched the engine never clicks it again. If the confirmation does not appear within 60s it stops,
opens a ticket with the steps to check the ledger, and prints them, with your real values, to your terminal only. Set
`CUA_OPERATOR=<name>` to record who acted on an escalation.

## 7. The agent path (optional)

An HTTP service exposes the capability as a tool an agent can discover and call:
```bash
python -m uvicorn cua.api:app --port 8000                                              # terminal 1
python scripts/agent_demo.py "transfer 5 dollars from account <FROM> to account <TO>"  # terminal 2
```
`GET /capabilities` lists the tool schema; `POST /capabilities/transfer_funds/invoke` runs it. There is no human callback
over HTTP, so an amount over the threshold is a `POLICY_BLOCK` there. The demo has no authentication and runs
synchronously. See `REPORT.md` for how a production version would differ.

## 8. Evidence

[`evidence/README.md`](evidence/README.md) is the map. Start with `evidence/by-outcome/replay/SUCCESS/` and
`evidence/by-outcome/discovery/SUCCESS/`. There are 23 replay runs (each scenario above, and the same ones over HTTP)
and 8 agent transcripts, all with a README naming the run it came from.
`python scripts/audit_evidence.py --tracked` re-checks the whole tree.

## Known limits

- Tested on Windows only; the commands are given for bash as well, but not run there.
- Amounts of $1000 or more were not run. If ParaBank printed a thousands separator on the confirmation, the exact-text
  check would not match, and the result would be a flagged `dispatch_unverified`, not a silent error.
- Session recovery lands correctly at the first step only.
- One flow on one app. The HTTP service has no authentication or idempotency key.
- Not built: a live browser handoff with in-run resume for an unverified dispatch, and the `drop_response` fault.
