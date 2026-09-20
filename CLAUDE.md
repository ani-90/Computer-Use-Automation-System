# CLAUDE.md

Take-home for interface.ai: a computer-use automation system. An LLM discovers a UI flow once; the flow is
compiled into a versioned `capability.json`; a deterministic Replay Engine (no LLM) runs it forever after.
Target app: self-hosted ParaBank (Docker). Single goal: log in, transfer funds between two accounts,
confirm, read the new balance.

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
- Only `PlaywrightAdapter` may import `playwright`. Everything else uses its four methods:
  `observe()`, `resolve(locator)`, `act(element, action)`, `navigate(target)`.
- The Replay Engine takes no LLM client and never imports the Anthropic SDK. Report a computed LLM-call count.
- Account numbers and amounts are parameters (`{{from_account}}`, `{{to_account}}`, `{{amount}}`). Never
  hardcode them in code, artifacts, tests or docs. The compiler rewrites tagged CLI values to placeholders.
- No credentials in artifacts or logs. The service-account login comes from `.env` and is not a per-invocation
  input. Redact before anything is written to `/evidence/`.
- Artifact steps have seven fields: `precondition`, `action`, `target`, `parameters`, `wait_strategy`,
  `checkpoint`, `error_mapping`. `target` is a locator description (ranked fallback chain), never a raw
  selector or a live handle.
- The Policy Gate runs before every action in both Discovery and Replay. Amount rules, in order:
  `amount <= 0` blocks, `amount > balance` blocks, `amount > approval_threshold` escalates (not a block).
  `/parabank/services/*` is disallowed.
- Outcomes: `SUCCESS`, `BUSINESS_OUTCOME`, `RECOVERABLE`, `HARD_FAILURE`, `POLICY_BLOCK`. Classification is
  separate from escalation; escalation is a configurable policy.
- Do not use ParaBank's `/parabank/services/*` API or call `services_proxy` directly. Drive the rendered UI only.
- The discovery agent gets no ParaBank-specific knowledge in its prompt.
- Browser runs headed. `session_owner` (`agent` | `human`) gates every action.
- Outputs are `confirmation_text`, `new_balance` and `transaction_id: str | None`. ParaBank's confirmation
  screen shows no transaction ID; it is read afterwards from Find Transactions (search by amount, open the
  last row: results list oldest-first) on the Transaction Details page. The match is best-effort (amount, description, date, newest),
  not a guarantee. This is a read-only step after `new_balance`. Populated in Phase 5, declared in the schema.
- Every run has a system-generated `run_id` (UUID) in logs, `/evidence/`, `ReplayResult` and escalation
  tickets. It is our own correlation ID, not the bank's, and lives beside `outputs`, not inside it.
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
