# CLAUDE.md

Take-home for interface.ai: a computer-use automation system. An LLM discovers a UI flow once; the flow is
compiled into a versioned `capability.json`; a deterministic Replay Engine (no LLM) runs it forever after.
Target app: self-hosted ParaBank (Docker). Single goal: log in, transfer funds between two accounts,
confirm, read the new balance.

## Progress (updated 2026-09-26)
Phases 0-8 are built, live-tested against the real ParaBank instance, and merged to `main` (each phase's
own branch is kept, not deleted, as history). That covers the whole core thesis: discover once (LLM) -> compile ->
replay deterministically -> classify every outcome -> escalate to a human when policy requires it -> recover on its own
from a real fault without one.

Phase 9 is built in a deliberately scoped form on branch `phase-9-dispatch-unverified` (merged to `main`): the
secondary escalation trigger for a money-moving click whose confirmation never verifies (see "Dispatched but
unverified" below), money precision (`src/cua/money.py`), the operator-only `CUA_FAULT` switch for a live HTTP fault
test, and compiler fixes found by live discovery runs. Live-verified from the CLI (15 scenarios) and over HTTP through
the agent (8 runs). Not built from the original Phase 9 idea: the `drop_response` fault, a live browser handoff with
in-run resume (`complete` / `retry_step` / `abort`), and engine-side balance reconciliation. Those are named cuts.

Evidence: curated, reviewed, and audited. `evidence/by-outcome/replay/` holds 23 replay runs (one folder per
scenario, named by outcome, including the same scenarios over HTTP), `evidence/by-outcome/discovery/` one run per
discovery stop reason (its `SUCCESS` is the run the committed artifact was compiled from), `evidence/agent_demo/` eight
redacted transcripts. Raw run folders are NOT committed. `python scripts/audit_evidence.py --tracked` passes with 0
problems and 0 warnings: every run's `run_id` agrees across its files and with its README's `Source run:`, and the
artifact's `created_from` resolves to a discovery run present in the evidence.

Untested edges, named honestly: amounts of $1000 or more (a source balance over $1000 and a supervisor's click are
needed; if ParaBank printed a thousands separator the exact-text confirmation check would not match, which would surface
as a flagged `dispatch_unverified`, not a silent error), and session expiry after step 0 (recovery only lands correctly
at step 0 live; see `replay.py`'s module docstring).

Phase 10 (deliverables): `README.md` is written (setup, discover, replay, agent path, evidence, known limits) and a
fresh-clone software check passed (439 tests, ruff, audit clean, no `.env` needed). Not done: `REPORT.md` with the 7
fixed headings (to be discussed before writing; the README links to it).

## Source of truth
The design docs live outside the repo (the user's Downloads folder): `approach_4.md` (what to build),
`discovery_agent_design_4.md` (discovery drill-down), `review_checklist_4.md` (per-phase review gate),
`solution_overview_onepager.md` (summary). The execution plan is at
`C:\Users\user\.claude\plans\i-am-now-going-typed-haven.md`. When a doc and this file disagree, ask the user.

## How to work in this repo
- The user controls every decision. Every edit needs their manual approval.
- Work in phases (see the plan). For each phase: post a short spec first, wait for approval, then code only
  that phase, then explain the diff against the matching checklist section.
- Do not commit, push or merge unless the user says so. One branch per phase (`phase-N-...`).
- Do not start the next phase until the user says "continue".
- Do not expand scope: no extra features, abstractions or files beyond the current phase.

## Design rules that must never be broken
- Only `PlaywrightAdapter` may import `playwright`. Everything else uses its methods — the original four,
  `observe()`, `resolve(locator)`, `act(element, action)`, `navigate(target)`, plus a small number of
  deliberate, explicitly-approved extensions added when a real capability genuinely needed one and the four
  couldn't express it: `set_session_owner()`/`captured_actions()` (Phase 7, capturing a human's own action
  during a handoff), `clear_session()`/`delay_next_request()` (Phase 8, real fault injection needs real
  browser-context control), `exists()` (Phase 8, a pure existence check — 1-or-more matches — distinct from
  `resolve()`'s exactly-one rule for picking a single action target). Any new one needs the same bar: a real
  need, flagged explicitly, not a convenience.
- The Replay Engine takes no LLM client and never imports the Anthropic SDK. Report a computed LLM-call count.
- Account numbers and amounts are parameters (`{{from_account}}`, `{{to_account}}`, `{{amount}}`). Never
  hardcode them in code, artifacts, tests or docs. The compiler rewrites tagged CLI values to placeholders.
- Money precision (`src/cua/money.py`, the one place that knows what an amount is): VALIDATION — an amount
  is a plain, finite decimal with at most 2 decimal places, counted on the normalized value (1.5, 1.50 and
  1.500 pass; 1.984 and 1.98484 don't; NaN, Infinity, `1e2`, `$5`, `1,5` aren't amounts). More than 2
  decimals is a `POLICY_BLOCK` ("amount supports at most 2 decimal places (USD)"), a non-number stays a
  `HARD_FAILURE`; both happen before the browser opens, on every entry point (CLI `parse_params`, the engine,
  therefore HTTP). Why: ParaBank accepts `1.98484`, moves the unrounded value, shows `$1.98`, and is left
  with a balance it can never format (see `recon-notes.md`). RENDERING — a money parameter is written
  `{{amount:money}}` in the artifact and renders as exactly 2 decimals with no `$` (`1.5` -> `1.50`), for
  the typed field, the search box and the confirmation sentence alike; never `"$" + amount + ".00"`, and a
  caller's raw `1.500` is never typed. COMPARISON — by Decimal value, never by string. `discover` also
  canonicalizes the amount, so the agent types the form the app expects.
- Artifact provenance: `Capability.created_from` is the run_id of the discovery run it was compiled from
  (a discovery evidence folder), and `version` is 2 for money-typed placeholders (1 predates them). An
  artifact is only ever produced by the compiler from a live discovery result — never hand-edited and never
  migrated by script (saved traces are redacted, so they are not recompilable). An old (version 1) artifact
  still works for whole-number amounts only: the engine refuses a fractional amount for it before the
  browser opens, because it would move the money and then fail to find the confirmation.
- No credentials in artifacts or logs. The service-account login comes from `.env` and is not a per-invocation
  input. Redact before anything is written to `/evidence/`. `run_id` and `ticket_id` are the one deliberate
  exception to pattern-based redaction (see below) — never hardcode another key into that exemption without
  the same reasoning: our own generated correlation ID, never customer data.
- Artifact steps have these fields: `precondition`, `action`, `target`, `parameters`, `wait_strategy`,
  `checkpoint`, `error_mapping` (the original seven), plus `extract_as` (which output an extract fills),
  `is_submission` (marks the one step that actually moves money, for escalation gating), and `best_effort`
  (Phase 8 — a step whose own failure must degrade to a missing output, never crash a run whose real work
  already succeeded; see the `transaction_id` rule below). `target` is a locator description (ranked
  fallback chain), never a raw selector or a live handle.
- The Policy Gate runs before every action in both Discovery and Replay. Amount rules, in order:
  `amount <= 0` blocks, `amount > balance` blocks, `amount > approval_threshold` escalates (not a block).
  `/parabank/services/*` is disallowed.
- Outcomes: `SUCCESS`, `BUSINESS_OUTCOME`, `RECOVERABLE`, `HARD_FAILURE`, `POLICY_BLOCK`. Classification is
  separate from escalation. Exactly two named triggers open a ticket — an amount over `approval_threshold`, and a
  dispatched money-moving step whose confirmation never verifies; every other failure returns a structured
  result and pages nobody (there is no per-outcome escalation dict; it was deleted as dead config). A
  genuinely expired session (`RECOVERABLE`/`session_expired`) is auto-recovered in place (Phase 8):
  re-authenticate with the fixed service account, retry the one step that failed, at most once per run, no
  human involved — for every step EXCEPT the money-moving one (see the next rule). A caller only ever sees a
  terminal `RECOVERABLE` if that one retry also fails. Every other `RECOVERABLE`-adjacent outcome still means
  classification only — safe and cheap to `replay()` again, never auto-retried. Inputs that must differ
  (`distinct_inputs`, e.g. from/to account) that don't are `POLICY_BLOCK` everywhere — CLI, engine and HTTP.
- The retry contract (`ReplayResult.side_effects`, set on EVERY result, also over HTTP): `none` — nothing was
  dispatched, safe to run again; `unverified` — the money-moving step was dispatched but its outcome could not be
  confirmed, check the ledger before any retry; `committed` — dispatched AND confirmed, so a later failure (a read
  after the transfer) must never be "fixed" by running the whole thing again. The engine sets it from what it actually
  did, not from which step failed. Every result and trace also names the artifact that produced it (`capability`: name,
  version, `created_from`), a fault-injected run says so in its trace (an `injected` entry; nothing reads it), each
  attempt of a retried step keeps its own screenshot (`*-retry1.png`), and a ticket records `opened_at`/`resolved_at`
  (UTC), the artifact, the step's own target (placeholders, never values), its screenshot and, when named through the
  `CUA_OPERATOR` environment variable, the operator. A mapped business outcome (`invalid_account`) says which step and
  which condition, by parameter name.
- Dispatched but unverified (Phase 9, scoped): once the `is_submission` click has been dispatched, the engine
  never clicks it again. It waits `Config.submit_confirmation_wait_ms` (default 60s, overriding the artifact's
  shorter wait for that one step) on the same page; if the confirmation still doesn't verify it does NOT probe
  the session, re-login or retry — those recovery paths would re-execute an irreversible step. It writes an
  `open` ticket (`Escalation.procedure`: inputs, balance before, and a verification procedure built from the
  artifact's own `best_effort` lookup steps — the engine names no page of any app) and returns `HARD_FAILURE`
  with `business_outcome = "dispatch_unverified"`. A click that raises (`ActionFailed`) counts the same as a
  missing confirmation — the request may already have gone out; only "element not found" proves nothing was
  clicked. The ticket is never resolved in-run: there is no live handoff, so the operator verifies against
  the ledger with their own access and re-runs only if it didn't post. The CLI prints the real, unredacted
  procedure to the terminal only (the saved ticket is redacted and cannot name the accounts); nothing printed
  there is ever written to a file. Over HTTP the caller gets `ticket_id` + `run_id` but never `procedure` (it embeds the run's real
  parameters). Click-then-reject/timeout: the engine verifies the page instead of trusting the typed word —
  confirmation visible → `SUCCESS` with the conflict recorded in `Escalation.note`; not visible →
  `dispatch_unverified`. The word can neither create nor erase a submission; only the page can.
- Fault injection (`--inject-faults` on the CLI, or the operator-only `CUA_FAULT` environment variable on
  the capability service) must be gated behind an explicit switch a normal invocation can never trigger by
  accident, and must make the real app misbehave for real (real cookies cleared, a real request genuinely
  delayed) — never a special-cased branch the classifier is aware of. The exact same code path must handle a
  real, un-injected failure of the same shape. `CUA_FAULT` is read once when the service starts, never from a
  request body; unset means no fault, a malformed value refuses to start, and a set value prints a loud
  banner.
- Do not use ParaBank's `/parabank/services/*` API or call `services_proxy` directly. Drive the rendered UI only.
- The discovery agent gets no ParaBank-specific knowledge in its prompt.
- Browser runs headed. `session_owner` (`agent` | `human`) gates every action.
- Outputs are `confirmation_text`, `new_balance` and `transaction_id: str | None`. ParaBank's confirmation
  screen shows no transaction ID; it is read afterwards from Find Transactions (search by amount, open the
  last row: results list oldest-first) on the Transaction Details page. The match is best-effort (amount, description, date, newest),
  not a guarantee — the compiler marks the step that opens the match with `nth: -1` (pick the newest) and
  marks every step in that read-only tail `best_effort: true`, so a failed or ambiguous match yields `None`,
  never a crash, once the real transfer and `new_balance` have already succeeded. This is a read-only step
  after `new_balance`. Populated in Phase 5, declared in the schema; the best-effort/newest-match handling
  was hardened in Phase 8 after live testing hit a real multi-match collision.
- Every run has a system-generated `run_id` (UUID) in logs, `/evidence/`, `ReplayResult` and escalation
  tickets. It is our own correlation ID, not the bank's, and lives beside `outputs`, not inside it. It must
  never be mangled by redaction — a UUID segment that happens to be all-digits looks like an account number
  to the generic pattern, so `run_id`/`ticket_id` are explicitly exempted from pattern-based redaction
  (found live as a real bug, fixed in `Redactor`).
- The discovery agent's prompt states only the goal (what outcome and outputs are wanted). It must not name
  ParaBank pages, element IDs, URLs beyond the start page, or the Admin Page. Review the prompt file at the
  Phase 3 gate.
- The Policy Gate allowlist permits only the pages the flow needs. `/parabank/services/*` and the Admin
  Page are denied.

## Setup
```
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
copy .env.example .env     # then fill in the values yourself
docker start parabank      # or: docker run -d --name parabank -p 8080:8080 parasoft/parabank
```
ParaBank: `http://localhost:8080/parabank`. Test login is `agentdemo`; the password lives only in `.env`.

## Commands
- Tests: `pytest`
- Lint: `ruff check .`
- Resetting ParaBank data means recreating the container, which also deletes `agentdemo`; re-register after.
- Discover (costs real LLM money, ~$0.30-0.40/run): `python -m cua.cli discover --param from_account=... --param to_account=... --param amount=...`
  Discover with a two-decimal amount (e.g. `amount=1.50`): the compiler then sees the app's money form and writes `{{amount:money}}`; the new artifact records the run in `created_from`.
- Evidence audit: `python scripts/audit_evidence.py --tracked` (folder name = every `run_id` inside; curated folders' README source = every `run_id`; an artifact's `created_from` resolves to discovery evidence). Exit 0 means clean.
- Replay (free — `llm_calls: 0` on every run, confirmed in the printed output): `python -m cua.cli replay --capability capabilities/transfer_funds.json --param from_account=... --param to_account=... --param amount=...`
- Fault injection (Phase 8, `--inject-faults` only — a normal replay above never touches this):
  `--inject-faults --fault-step N --fault-type transient_fail --fault-url-pattern "<glob>" [--fault-delay-ms N]`
  or `--inject-faults --fault-step N --fault-type clear_session`. `clear_session` only lands correctly on a
  step whose page is reached by nothing more than a fresh login (this capability's step 0) — see `replay.py`'s
  module docstring for the full scope boundary.
- Live HTTP fault test (operator-only): start the service with the switch set, then call it as usual —
  `$env:CUA_FAULT = 'transient_fail:5:**/*transfer*:90000'` then `python -m uvicorn cua.api:app --port 8000`
  (format `transient_fail:<step>:<url glob>[:<delay_ms>]` or `clear_session:<step>`). Step 5 is the Transfer
  click. Expect `HARD_FAILURE` / `dispatch_unverified` after about 3 minutes (the fault holds the request 90s,
  then the engine waits its own 60s); `scripts/agent_demo.py` allows 300s. Remove the variable afterwards.
- Agent demo over HTTP: `python -m uvicorn cua.api:app --port 8000`, then
  `python scripts/agent_demo.py 'transfer $5 from account ... to account ...'` (single quotes in PowerShell).
