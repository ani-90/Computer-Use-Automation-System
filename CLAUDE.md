# CLAUDE.md

Take-home for interface.ai: a computer-use automation system. An LLM discovers a UI flow once; the flow is
compiled into a versioned `capability.json`; a deterministic Replay Engine (no LLM) runs it forever after.
Target app: self-hosted ParaBank (Docker). Single goal: log in, transfer funds between two accounts,
confirm, read the new balance.

## Progress (updated 2026-09-24)
Phases 0-8 are built, live-tested against the real ParaBank instance, and merged to `main` (each phase's
own branch is kept, not deleted, as history — `main` fast-forwarded through all of them, so nothing was
squashed). That covers the whole core thesis: discover once (LLM) → compile → replay deterministically →
classify every outcome → escalate to a human when policy requires it → recover on its own from a real fault
without one. Curated, reviewed evidence for every outcome actually produced lives in `discovery-outcomes/`
and `replay-outcomes/` (one subfolder per outcome, named by the outcome, each with its own `README.md`).

Not done: **Phase 9** (secondary escalation, `drop_response` — explicitly a stretch goal in the plan, "only
after Phases 0-8 are solid," which they now are; skip or attempt on request) and **Phase 10** (deliverables:
`README.md`, `REPORT.md` with the 7 fixed headings, an evidence audit, a fresh-clone verification pass).

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
  separate from escalation; escalation is a configurable policy. A genuinely expired session
  (`RECOVERABLE`/`session_expired`) is auto-recovered in place (Phase 8): re-authenticate with the fixed
  service account, retry the one step that failed, at most once per run, no human involved. A caller only
  ever sees a terminal `RECOVERABLE` if that one retry also fails. Every other `RECOVERABLE`-adjacent
  outcome still means classification only — safe and cheap to `replay()` again, never auto-retried.
- Fault injection (`--inject-faults` only) must be gated behind an explicit flag a normal invocation can
  never trigger by accident, and must make the real app misbehave for real (real cookies cleared, a real
  request genuinely delayed) — never a special-cased branch the classifier is aware of. The exact same code
  path must handle a real, un-injected failure of the same shape.
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
- Replay (free — `llm_calls: 0` on every run, confirmed in the printed output): `python -m cua.cli replay --capability capabilities/transfer_funds.json --param from_account=... --param to_account=... --param amount=...`
- Fault injection (Phase 8, `--inject-faults` only — a normal replay above never touches this):
  `--inject-faults --fault-step N --fault-type transient_fail --fault-url-pattern "<glob>" [--fault-delay-ms N]`
  or `--inject-faults --fault-step N --fault-type clear_session`. `clear_session` only lands correctly on a
  step whose page is reached by nothing more than a fresh login (this capability's step 0) — see `replay.py`'s
  module docstring for the full scope boundary.
