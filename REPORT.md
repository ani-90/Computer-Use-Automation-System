# Design write-up

## 1. Architecture

Three stages, one direction.

**Discovery:** a person states the goal once, in plain language — what should happen and what should be reported
back. The agent gets nothing else: no page names, no element IDs, no hints about the app. It looks at the screen
(observe), picks an element, acts, and repeats until the goal is met, a dead end, or its step/time budget. Every
action passes the Policy Gate first — the guardrails run while learning, not just afterward. The result is a
step-by-step trace of what actually worked.

**Compilation:** that trace becomes the product. The compiler replaces the specific values typed during discovery
(account numbers, amounts) with typed placeholders, marks which step is the one that moves money, marks the optional
read-only lookups that must never fail a finished run, and records which discovery run the artifact came from. An
artifact is only ever born this way — never hand-edited afterward, never patched by script. If the compiler is
wrong, you fix the compiler and re-record; you don't edit the output.

**Replay:** the production path. The engine takes the artifact and the caller's inputs — nothing else. No LLM
client, no Anthropic SDK import: every run reports a computed count of zero LLM calls. Each step runs the same fixed
loop — check the precondition, act, wait, verify the checkpoint — and every run ends with a structured result: what
happened, what was returned, and whether it is safe to try again.

Evidence is written as it happens, not after. Every run — success, failure, or escalation — streams a step log and
a transcript to its own `run_id`-keyed folder while it executes, so the step log and every screenshot survive even
a crash mid-run: what the agent saw and did, and what the page looked like at each step. The final structured
result is written once, when the run concludes; only a genuine crash before that point would leave it missing. Two
files are deliberately separate: the transcript (full, uncompressed, what the model actually saw and said) and the
compiled artifact (the clean, reviewed capability). The first is for debugging and audit; the second is the
product. They never merge. Redaction happens at the write boundary — account-number-shaped values are masked
before anything touches disk — so no run's evidence can ever hold what the caller's inputs held. A script audits
the committed evidence tree: every `run_id` in every file matches the run its folder claims to be.

**Key decisions and trade-offs.**

**Target and its limit:** ParaBank is self-hosted — real Parasoft banking code on a genuinely legacy-flavored
surface, locally controlled so an evaluator's rerun can't fail on a shared instance. Its honest limit: it is a
customer self-service portal, not a staff tool, so the demo collapses the service-account and customer roles into
one login. The design — the service account authenticating as itself, the customer traveling as data, never as a
login — targets the real staff-tool environment regardless. The app is the stand-in; the task and the access
pattern are not: "transfer funds, confirm it posted, get a durable reference" is exactly the kind of
transaction-processing work back-office bank staff actually do. One fixed service account acting on customer data — never a
per-customer login — is the real back-office authentication model, demonstrated here against a customer-facing app.
Login is a prelude, not a recorded step:
authentication is session-layer infrastructure — the artifact carries zero auth, and in production login would itself
be a discovered, per-app session capability.

**The agent-facing capability interface (stretch-goal implementation).**

**What it is:** invokes the `transfer_funds` capability as a tool (tool name: `transfer_funds`). Once the tool is
invoked, the real, deterministic engine carries out the task — no LLM involved in actually doing it, only in
deciding to call it.

**What it can do:** list the available capability and what inputs it needs (a catalog); accept a real, typed call
with real values (`from_account`, `to_account`, `amount`); run it through the same replay engine. Demonstrated with
8 real, curated runs — a plain success, a decimal amount, an invalid account on either side, too many decimals in
amount, the same account twice, and a lost confirmation opening a ticket.

**What it cannot do (yet):** the caller waits for the whole thing to finish instead of getting a tracking number
back immediately. There's no login or API key — anyone reaching the address can call it. Calling the same request
twice isn't detected, so a network retry could resend a transfer. And there's no human escalation over this
interface at all — an amount over the approval threshold just gets blocked here (`POLICY_BLOCK`), since there's no
live browser to hand off to anyone remotely.

**The engine's own decisions:** only `PlaywrightAdapter` imports Playwright; everything else calls four adapter
methods plus a handful of explicitly-approved extensions added only when a real need arose — what makes the engine
and compiler provably surface-agnostic (heading 4). The artifact is a description, not a script: locators are ranked,
human-readable fallback chains, never raw selectors; `resolve()` requires exactly one match. Classification (what
happened) is kept separate from escalation (does a human need to look) — a per-outcome escalation dictionary was
deleted once the real two triggers were identified. The irreversible step (`is_submission`) is a schema fact, not an
engine special case. Money is a single-sourced type (`src/cua/money.py`), after ParaBank was found live to accept a
five-decimal amount, move the unrounded value, and show a balance it could never re-render (`recon-notes.md`).

## 2. Artifact schema

An artifact is one JSON document — the compiled, versioned description of a flow, never hand-written
(`capabilities/transfer_funds.json`, 15 steps). Every field is validated against a strict schema (`src/cua/models.py`);
an unknown field is rejected, so a malformed artifact fails at load, not mid-run.

Four nested types make up the schema:
- **Capability** — the whole artifact
  - **Step** — one action in the flow
    - **Locator** / **Condition** — what to act on, or what must be true
      - **LocatorCandidate** — one way to find that element, ranked in a fallback chain

**Capability-level fields**

| Field | What it's for |
| --- | --- |
| `schema_version` / `version` | the format vs. this instance — versioned separately; `version: "2"` marks money-typed placeholders |
| `inputs` / `outputs` | typed; also source the HTTP tool schema — the schema *is* the API |
| `amount_input` | which input the amount rules apply to (optional — not every capability moves money) |
| `distinct_inputs` | input groups that must differ, e.g. `[["from_account","to_account"]]` |
| `created_from` | the discovery run's `run_id`, checked by `scripts/audit_evidence.py` |

**Step-level fields** — seven core, plus three purpose flags added only when a real need arose. Values may hold
`{{placeholders}}`, never a literal account number or amount.

| Field | What it's for |
| --- | --- |
| `precondition` | must hold before the step acts |
| `action` | click / type / select / navigate / extract |
| `target` | the locator (below) |
| `parameters` | the values to use |
| `wait_strategy` | how the step knows it took effect |
| `checkpoint` | must hold after the step acts |
| `error_mapping` | which visible condition means which business outcome |
| `extract_as` | which declared output this value fills |
| `is_submission` | true on exactly one step: the one that moves money |
| `best_effort` | a step whose own failure degrades to a missing output, never crashes a run whose real work already succeeded |

A **locator** is a `description` plus a ranked `chain` of candidates; exactly one match is required to act. The
transfer form's two account dropdowns have no accessible name at all, so position is the only stable key available
for them — the artifact's one locator without a real fallback chain, on the mandatory path; a renamed or reordered
form fails there rather than silently misfiring. A
**condition** is one of ten kinds — page, element, text, form-state, shape — each validated to carry the target/value
it needs. `error_mapping` lets the artifact declare which visible condition means which business outcome (e.g.
`invalid_account`), so that mapping is compiler-produced data, not engine logic — the honest exception:
`invalid_account` arrives via compiler-emitted rules, but `login_rejected` is currently an engine-side condition (login
precedes the artifact's own steps); the reviewed merge path that would carry probe-derived mappings into the artifact
is designed, not built.

**Why shaped this way:** every step states what must be true before and after it, so failure is detected at the step
where it happens, with a plain-language `expected`/`observed` pair, not an unexplained timeout downstream. Money is
typed at the schema level (`{{amount:money}}` renders as exactly two decimals, no `$`), so the typed field, search
box and confirmation text share one source of truth.

## 3. Determinism & error handling

**Determinism** comes from three things together: no LLM in the loop; event-based waits (`url_change`,
`element_visible`/`_hidden`, `option_present`, `network_idle`, never a bare sleep as the primary strategy) with a
per-step timeout; checkpoints that assert meaning (a heading's text, a Decimal-compared field value), not layout.

**The replay contract** is the composition of the artifact's `inputs`/`outputs`/`checkpoint`/`error_mapping`, the
engine's fixed per-step loop, and `ReplayResult`. What a caller depends on most is `side_effects`, the retry
contract: `none` (safe to retry), `unverified` (dispatched, unconfirmed — check the ledger first), `committed`
(dispatched and confirmed — a later failure must never be "fixed" by re-running the whole capability). It reflects
what the engine actually did, never which step failed. Discovery carries the same contract on `DiscoveryResult`,
because it dispatches the same real action to learn what success looks like — a discovery run that dispatched and
then failed to reach `SUCCESS` is `unverified` too, with its own ticket; see heading 5.

**Runtime errors, per step:** precondition false means drift, wrong state, or an expired session; a checkpoint/action
failure checks `error_mapping` first, else falls to `HARD_FAILURE`/`RECOVERABLE`. An expired session is
auto-recovered in place — re-login, retry the failed step, once, no human — for every step except the money-moving
one, so a caller only sees terminal `RECOVERABLE` if that retry also fails. This lands correctly only where a step's
own precondition is satisfied by nothing more than being freshly logged in — true at the first step, since re-login
always returns to the same page — not guaranteed deeper into the flow, where a precondition expects form state a
login alone doesn't restore; resuming mid-flow is a real, named scope boundary, not an oversight.

**The irreversible-action rule**, the sharpest edge in the design: once `is_submission` is dispatched, the engine
never clicks it again — no probe, no re-login, no retry. It waits 60s; if confirmation still doesn't verify, that's
`HARD_FAILURE` / `dispatch_unverified` with an open ticket. A raised error counts the same as a missing confirmation
(the request may already be in flight) — only "element not found" proves nothing was clicked. If a human types
`reject` (or times out) after actually clicking Transfer, the engine trusts the page, not the word. `is_submission`
steps use this fixed 60-second window regardless of the artifact: every step's `wait_strategy.timeout_ms` is
uniformly 10s (the schema default), and the engine overrides it for this one step only, on purpose — a false
escalation on the one irreversible action is worse than waiting longer.

**Amount validation** happens before the browser opens, identically on the CLI, engine and HTTP: malformed input is
`HARD_FAILURE` (a contract violation — the request cannot be evaluated, distinct from a policy refusal of a
well-formed value; a possible `INVALID_INPUT` refinement); more than two decimals or two required-distinct inputs
being equal is `POLICY_BLOCK`; then the ordered gate (`<=0` blocks, `>balance` blocks, `>threshold` escalates).

**UI drift** is the same taxonomy, not a separate mechanism: a locator that can't resolve to exactly one match, or a
checkpoint whose text no longer appears, is a classified failure pointing at the exact step index, with the run's
per-step screenshots as the richer signal — the failure says what differed; the screenshot says what the page
actually looked like when it did.

## 4. Heterogeneity & multi-tenant

Design, not code — nothing beyond one app, one instance, was built or tested.

**Three layers.** The **artifact** is the app's flow, shareable by every institution running the same app:
intent-level steps, placeholders, ranked locator descriptions, semantic checkpoints — no URL beyond a path fragment,
no credentials, no literal values. A **tenant profile** would hold what legitimately differs — base URL, a
credential-secret reference, approval threshold, allowlist. Today that's one `.env`/`Config` per instance; a tenant
key selecting among several is a straightforward extension of that seam, not a new architecture. **Run inputs** are
the per-invocation parameters, unchanged.

**Why one artifact serves many tenants:** locators are descriptions (role, name, context), not selectors, so they
resolve against a differently themed instance of the same app; checkpoints assert meaning, not layout, so styling or
copy differences don't matter as long as the semantics match. A tenant whose flow genuinely differs fails a
checkpoint or locator resolution — a classified `HARD_FAILURE`, never silently skipped — and the fix is to re-run
discovery for that one tenant and compile a tenant-scoped artifact, still carrying its own `created_from`. The cost
of divergence is per drifting tenant, not a fixed subsystem paid up front.

**The variant spectrum.** A locator's fallback chain absorbs *markup* differences — the same label found a different
way — not a label change itself: every candidate in a chain is built from that same label, so a rename breaks the
whole chain at once. That case routes to a **per-tenant override**: one locator swapped in for one step, keyed by
the step's position, merged in only at replay time, never touching the shared artifact — valid exactly as long as
the flow's shape is unchanged. The boundary test: if a tenant's flow ever needs `is_submission` itself to sit on a
different step — an inserted review page, say — no override can express that; it's the signal for the fresh-discovery
path above, not a bigger override format. A **drift canary** (periodically resolving an artifact's checkpoint targets
without acting — a read-only health check) would tell you which case you're in before a real invocation fails; not
built. **Onboarding economics:** because the discovery prompt is app-agnostic, the marginal cost of a new tenant is
one discovery run plus one artifact review — engineering time becomes operational cost.

**Legacy web and desktop:** the engine, schema and outcome taxonomy touch the adapter only through four methods. A
desktop adapter implementing the same four over an accessibility tree (or a vision-based fallback) needs no change
above that boundary — same schema, same locator-ranking idea, same replay contract. That's the payoff of "only
`PlaywrightAdapter` imports Playwright": the boundary that keeps this build clean is what a second surface would
implement against.

## 5. Escalation & handoff

**Two named triggers on replay**, not a per-outcome dictionary: an amount over `approval_threshold` (a normal,
foreseeable decision), and a dispatched money-moving click whose confirmation never verifies (the one place a run
might have already changed the world and the engine can't tell). Every other failure returns a structured result
and pages nobody. "Stuck" is detected the same way in both cases — a checkpoint that won't become true within its
wait — but what happens next differs.

**Trigger 1, live handoff:** `session_owner` (`agent`|`human`) gates every action. The engine opens a ticket, hands
the live browser to a human, and waits up to a fixed timeout (60s in the CLI) for a decision; the human's actual
actions are captured, not assumed. On `approve`, `reject`, or timeout, control returns to the agent, which verifies
the page rather than the typed word before deciding the outcome.

**Trigger 2, ticket-only by design:** once Transfer is dispatched, there is no live handoff — re-entering the run to
let a human retry would mean re-clicking an action that may have already gone through. The engine writes an `open`
ticket with a verification procedure built from the artifact's own best-effort lookup steps (real inputs, balance
beforehand; the engine names no page of any app) and returns `HARD_FAILURE` / `dispatch_unverified`. The operator
checks the real ledger and decides whether to re-run.

**Discovery shares trigger 2, because it shares the risk.** Discovery has to dispatch the real money-moving action
to learn what success looks like — there is no artifact yet, so no `is_submission` flag exists to lean on, but the
same shape of risk exists: a real click, followed by a run that never confirms it. The engine tracks this itself,
live — the same first-button-after-the-amount signal the compiler later turns into `is_submission` — and treats it
identically: no re-click, no silent "safe to re-run." If the run ends anything but `SUCCESS` after that dispatch, it
opens the same ticket machinery, with `side_effects: "unverified"` on the result. A raised error on that one click
counts the same as a missing confirmation would — only "element not found" proves nothing was clicked, matching
replay's own rule exactly. Live-verified: a run given one extra, deliberately unsatisfiable requirement dispatched a
real transfer, confirmed it, then correctly failed to finish — and opened a real ticket pointing at the real step,
with a real screenshot (`evidence/by-outcome/discovery/DEAD_END_dispatch_unverified/`).

**Trust surfaces stay separate:** a ticket is always written redacted. The CLI prints the real, unredacted procedure
to the operator's terminal only, never to a file. Over HTTP the caller gets `ticket_id`/`run_id` but never
`procedure`. A ticket also records timestamps, the artifact, the step's target description (placeholders, never
values), a screenshot, and — via `CUA_OPERATOR` — who acted on it.

## 6. Safety

**Three guardrail layers, same order, every action, in both Discovery and Replay:** a **path allowlist** (only the
pages the flow needs; `/parabank/services/*` and the Admin Page are denied, only the rendered UI is ever driven);
**ordered amount rules** (`<=0` blocks, `>balance` blocks, `>threshold` escalates); the **irreversible-action rule**
(once dispatched, a money-moving step is never retried or re-clicked).

**Why block, escalate, and never flag.** The two hard rules block because no decision exists to defer — no supervisor
should be able to authorize an overdraft or a negative amount, and recon proved the app itself enforces neither (see
`recon-notes.md`: no overdraft protection, accounts can go negative), so the gate is the only protection. The
threshold escalates because it's a genuine authorization judgment — auto-approving defeats having a threshold,
auto-blocking refuses legitimate affordable requests. Flag-and-proceed is rejected outright: proceeding after
detecting a violation defeats the point of detecting it. On timeout: silence is not consent — no answer is a refusal,
never an approval.

**Data handling:** no credentials ever enter an artifact, log, or result — the service-account login comes only from
`.env`. Redaction happens at the write boundary: a generic pattern (`\b\d{5,12}\b`) masks account-number-shaped runs,
with one deliberate exception — `run_id`/`ticket_id`, our own correlation IDs, never customer data (a UUID segment
that happens to be all-digits was found live to be mangled otherwise; fixed in the redactor). Three trust surfaces
are handled differently on purpose: the operator's terminal sees real values; the HTTP caller sees IDs and
`side_effects` but never a raw value or procedure; persisted evidence is always redacted. Discovery's prompt states
only the goal, never a page name or element ID, so the guardrails aren't something the agent could route around by
being told where things are.

**Limits:** redaction is pattern-based, not structural — it reasons about shape, not meaning (hence a cosmetic quirk
on sub-cent digits). The allowlist is fixed per capability, not learned. These guarantees hold only as far as
`PlaywrightAdapter`, which is trusted to actually enforce what it's told.

## 7. Cuts

**Deliberately left out**, because the core thesis didn't need it, not because it was missed:

- **`drop_response` fault injection** (a request that never returns, vs. one merely delayed) — a stretch goal, gated
  behind "only after the rest is solid."
- **A live browser handoff with in-run resume** for the dispatch-unverified trigger — `Escalation.decision` already
  reserves `complete`/`retry_step`/`abort` values for this, unused today.
- **Engine-side balance reconciliation** — an independent post-transfer balance check beyond the checkpoint text
  match.
- **A tenant profile as a first-class concept** — designed for (heading 4), not implemented.
- **Input constraints and `locator_rationale`** on the schema.
- **A second adapter** (accessibility-tree or vision-based) — argued for, not built.
- **A uniqueness guard on the `transaction_id` lookup** — correctness of the newest-match pick (`nth: -1`) rests on
  ParaBank's verified oldest-first ordering, with no redundant check confirming only one match exists; a
  compiler-emitted guard that degrades to `None` on ambiguity, instead of trusting position alone, is the designed
  next step.
- **Known-dialog dismissal** — an unexpected pop-up mid-flow is never silently clicked past; it fails as a classified
  `HARD_FAILURE` at the exact step, coarse but safe. A whitelist of dismissible patterns is the next robustness
  build, not attempted here since no live run has ever hit one.

**Next, in order:** the tenant profile (smallest change, largest generalization payoff); in-run resume for the
dispatch-unverified trigger; a second adapter, as the real test of the four-method boundary.

**Untested edges, named honestly:** amounts of $1000+ (needs a source balance over $1000 and a live supervisor
click; a thousands separator on the confirmation would surface as a flagged `dispatch_unverified`, never a silent
error); session expiry deeper than step 0 (the scope boundary §3 names; only the first-step case is proven live); a terminal `RECOVERABLE` where
the one retry also fails (unit-tested, not live); a failure after a transfer is already confirmed (tested against a
fake bank double, since deliberately breaking a shared sandbox live isn't safe).
