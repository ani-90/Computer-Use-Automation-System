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
result is written once, when the run concludes; only a genuine crash before that point would leave it missing. The
transcript and the compiled artifact stay deliberately separate from each other: the transcript (full, uncompressed,
what the model actually saw and said) is for debugging and audit, the compiled artifact (the clean, reviewed
capability) is the product. They never merge. Redaction happens at the write boundary — account-number-shaped values are masked
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
Login is a prelude, not a recorded step: authentication is session-layer infrastructure — the artifact carries zero
auth, and in production login would itself be a discovered, per-app session capability.

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
methods plus a handful of explicitly-approved extensions added only when a real need arose — what keeps the engine
and compiler surface-agnostic by construction (heading 4). The artifact is a description, not a script: locators are ranked,
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
Amount field and both account dropdowns on the transfer form have no accessible name at all, so position is the
only stable key available for them — three locators without a real fallback chain, on the mandatory path; a renamed
or reordered form fails there rather than silently misfiring. A **condition** is one of ten kinds — page, element,
text, form-state, shape — each validated to carry the target/value it needs. `error_mapping` lets the artifact
declare which visible condition means which business outcome (e.g. `invalid_account`), so that mapping is
compiler-produced data, not engine logic. The one honest exception: `login_rejected` is currently an engine-side
condition, not compiler-emitted like `invalid_account` (login precedes the artifact's own steps); the reviewed merge
path that would carry probe-derived mappings into the artifact is designed, not built.

**Why shaped this way:** every step states what must be true before and after it, so failure is detected at the step
where it happens, with a plain-language `expected`/`observed` pair, not an unexplained timeout downstream. Money is
typed at the schema level (`{{amount:money}}` renders as exactly two decimals, no `$`), so the typed field, search
box and confirmation text share one source of truth.

## 3. Determinism & error handling

**Determinism** means the same inputs, against the same app state, always take the same path — no model improvising
a different click on a re-run. Three things guarantee it together:
- No LLM in the loop.
- Event-based waits (`url_change`, `element_visible`/`_hidden`, `option_present`, `network_idle`, never a bare sleep
  as the primary strategy), each with a per-step timeout.
- Checkpoints that assert meaning (a heading's text, a Decimal-compared field value), not layout.

**The replay contract** is the composition of the artifact's `inputs`/`outputs`/`checkpoint`/`error_mapping`, the
engine's fixed per-step loop, and `ReplayResult`. What a caller depends on most is `side_effects`, the retry
contract — it reflects what the engine actually did, never which step failed:

| `side_effects` | Meaning |
| --- | --- |
| `none` | nothing was dispatched — safe to retry |
| `unverified` | dispatched, unconfirmed — check the ledger before retrying |
| `committed` | dispatched and confirmed — a later failure must never be papered over by re-running the whole capability |

Discovery carries the same contract on `DiscoveryResult`, because it dispatches the same real action to learn what
success looks like — a discovery run that dispatched and then failed to reach `SUCCESS` is `unverified` too, with
its own ticket; see heading 5.

**Runtime errors, per step:** precondition false means drift, wrong state, or an expired session; a checkpoint/action
failure checks `error_mapping` first — an unmapped failure is `RECOVERABLE` specifically when it's an expired
session, `HARD_FAILURE` otherwise. An expired session is auto-recovered in place — re-login, retry the failed step,
once, no human — for every step except the money-moving one, so a caller only sees terminal `RECOVERABLE` if that
retry also fails.

This recovery only works reliably at the first step. Re-login always lands back on the same starting page, and step
0 needs nothing more than that to proceed. A step deeper in the flow expects more — form fields already filled in,
a page reached partway through the transaction — state a fresh login can't restore. Recovering mid-flow is a known,
deliberate scope boundary, not something overlooked.

**The irreversible-action rule**, the sharpest edge in the design: once `is_submission` is dispatched, the engine
never clicks it again — no probe, no re-login, no retry. It waits 60s; if confirmation still doesn't verify, that's
`HARD_FAILURE` / `dispatch_unverified` with an open ticket. A raised error counts the same as a missing confirmation
(the request may already be in flight) — only "element not found" proves nothing was clicked. If a human types
`reject` (or times out) after actually clicking Transfer, the engine trusts the page, not the word. `is_submission`
steps use this fixed 60-second window regardless of the artifact: every step's `wait_strategy.timeout_ms` is
uniformly 10s (the schema default), and the engine overrides it for this one step only, on purpose — a false
escalation on the one irreversible action is worse than waiting longer.

**Amount validation** happens before the browser opens, identically on the CLI, engine and HTTP, as an ordered sequence
of checks:
1. Malformed input (not a number) → `HARD_FAILURE` — a contract violation, the request cannot even be evaluated,
   distinct from a policy refusal of a well-formed value (a possible `INVALID_INPUT` refinement).
2. More than two decimals, or two required-distinct inputs being equal → `POLICY_BLOCK`.
3. The ordered amount gate: `<=0` blocks, `>balance` blocks, `>threshold` escalates.

**UI drift** is the same taxonomy, not a separate mechanism: a locator that can't resolve to exactly one match, or a
checkpoint whose text no longer appears, is a classified failure pointing at the exact step index, with the run's
per-step screenshots as the richer signal — the failure says what differed; the screenshot says what the page
actually looked like when it did.

**Untested edges, named honestly** — built, not cut, but never exercised live:
- Amounts of $1000+: needs a source balance over $1000 and a live supervisor click. ParaBank's default account
  balance doesn't reach $1000, and the ordered amount gate checks balance before threshold, so this case can't be
  reached without first inflating a balance solely to test it; a thousands separator on the confirmation would
  surface as a flagged `dispatch_unverified`, never a silent error.
- Session expiry deeper than step 0: the scope boundary named above; only the first-step case is proven live.
- A terminal `RECOVERABLE` where the one retry also fails: unit-tested, not live.
- A failure after a transfer is already confirmed: tested against a fake bank double, since deliberately breaking
  a shared sandbox live isn't safe.

## 4. Heterogeneity & multi-tenant

Design, not code — nothing beyond one app, one instance, was built or tested.

**Three layers separate what's shared from what's per-tenant:**

| Layer | What it holds |
| --- | --- |
| **Artifact** | the app's flow, shareable by every institution running the same app — intent-level steps, placeholders, ranked locator descriptions, semantic checkpoints; no URL beyond a path fragment, no credentials, no literal values |
| **Tenant profile** | what legitimately differs per institution — base URL, a credential-secret reference, approval threshold, allowlist. Today that's one `.env`/`Config` per instance; a tenant key selecting among several is a straightforward extension of that seam, not a new architecture |
| **Run inputs** | the per-invocation parameters — unchanged |

**Why one artifact serves many tenants:** locators are descriptions (role, name, context), not selectors, so they
resolve against a differently themed instance of the same app; checkpoints assert meaning, not layout, so styling or
copy differences don't matter as long as the semantics match. A tenant whose flow genuinely differs fails a
checkpoint or locator resolution — a classified `HARD_FAILURE`, never silently skipped — and the fix is to re-run
discovery for that one tenant and compile a tenant-scoped artifact, still carrying its own `created_from`. The cost
of divergence is per drifting tenant, not a fixed subsystem paid up front.

**The variant spectrum** — how much a tenant's version of the app can differ before it needs its own artifact:
- **Markup differences** (the same label, found a different way) are absorbed by a locator's fallback chain — every
  candidate in the chain is still built from that same label, so a genuine label change breaks the whole chain at
  once, not just one candidate in it.
- **A label change itself** routes to a **per-tenant override**: one locator swapped in for one step, keyed by the
  step's position, merged in only at replay time, never touching the shared artifact — valid exactly as long as the
  flow's shape is unchanged.
- **The boundary test:** if a tenant's flow ever needs `is_submission` itself to sit on a different step — an
  inserted review page, say — no override can express that. That's the signal to run fresh discovery for that
  tenant, not to build a bigger override format.
- **A drift canary** — periodically resolving an artifact's checkpoint targets without acting, a read-only health
  check — would tell you which case you're in before a real invocation fails. Not built.
- **Onboarding economics:** because the discovery prompt is app-agnostic, the marginal cost of a new tenant is one
  discovery run plus one artifact review — engineering time becomes operational cost.

**Legacy web and desktop:** the engine, schema and outcome taxonomy touch the adapter only through four methods. A
desktop adapter implementing the same four over an accessibility tree (or a vision-based fallback) needs no change
above that boundary — same schema, same locator-ranking idea, same replay contract. That's the payoff of keeping
Playwright imports confined to `PlaywrightAdapter` alone: the boundary that keeps this build clean is what a second
surface would implement against.

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
live, using the first-button-after-the-amount signal that the compiler later formalizes as `is_submission` — and treats it
identically: no re-click, no assuming it's safe to re-run. If the run ends anything but `SUCCESS` after that
dispatch, it opens the same ticket machinery, with `side_effects: "unverified"` on the result. A raised error on
that one click counts the same as a missing confirmation would — only "element not found" proves nothing was
clicked, matching replay's own rule exactly.

**Live-verified:** a run given one extra, deliberately unsatisfiable requirement dispatched a real transfer,
confirmed it, then correctly failed to finish — and opened a real ticket pointing at the real step, with a real
screenshot (`evidence/by-outcome/discovery/DEAD_END_dispatch_unverified/`).

**Trust surfaces stay separate** — the same three-way split as heading 6. A ticket is always written redacted, and
additionally records timestamps, the artifact, the step's target description (placeholders, never values), a
screenshot, and — via `CUA_OPERATOR` — who acted on it.

## 6. Safety

**Three guardrail layers, same order, every action, in both Discovery and Replay:**

| Layer | What it does |
| --- | --- |
| Path allowlist | only the pages the flow needs; `/parabank/services/*` and the Admin Page are denied; only the rendered UI is ever driven |
| Ordered amount rules | `≤0` blocks, `>balance` blocks, `>threshold` escalates |
| Irreversible-action rule | once dispatched, a money-moving step is never retried or re-clicked |

**Why block & escalate, but never flag & proceed:**
- **The two hard rules block because there's no decision to defer to anyone** — no supervisor should ever be allowed
  to authorize an overdraft or a negative amount. And this isn't hypothetical: recon proved live that the app itself
  enforces neither (`recon-notes.md`: no overdraft protection, accounts can go negative). The gate is the only
  protection that exists.
- **The threshold escalates**, not blocks or auto-approves, because it's a genuine authorization judgment —
  auto-approving defeats having a threshold; auto-blocking refuses legitimate, affordable requests.
- **Flag-and-proceed is rejected outright**: proceeding after detecting a violation defeats the point of detecting it.
- **On timeout, silence is not consent** — no answer is a refusal, never an approval.

**Data handling:** no credentials ever enter an artifact, log, or result — the service-account login comes only from
`.env`. Redaction happens at the write boundary: a generic pattern (`\b\d{5,12}\b`) masks account-number-shaped runs,
with one deliberate exception — `run_id`/`ticket_id`, our own correlation IDs, never customer data (a UUID segment
that happens to be all-digits was found live to be mangled otherwise; fixed in the redactor). Discovery's prompt
states only the goal, never a page name or element ID, so the guardrails aren't something the agent could route
around by being told where things are.

**Three trust surfaces, handled differently on purpose:**

| Surface | Sees |
| --- | --- |
| Operator's terminal | real values |
| HTTP caller | IDs and `side_effects` — never a raw value or procedure |
| Persisted evidence | always redacted |

**Limits:** redaction is pattern-based, not structural — it reasons about shape, not meaning (hence a cosmetic quirk
on sub-cent digits). The allowlist is fixed per capability, not learned. These guarantees hold only as far as
`PlaywrightAdapter`, which is trusted to actually enforce what it's told.

## 7. Cuts

The following items were deliberately left out, each for a specific reason:

- **Two robustness stretch items, never exercised live:** `drop_response` fault injection (a request that never
  returns, vs. one merely delayed) and known-dialog dismissal (an unexpected pop-up mid-flow, handled today as a
  safe classified `HARD_FAILURE`). Both were scoped as later-priority work, attempted only once the rest of the
  system was solid, and neither has ever actually occurred in a live run.
- **Two generalization items, designed but with nothing to build against yet:** a tenant profile as a first-class
  concept and a second, non-web adapter (heading 4). Both argued for in detail; neither built, because there's
  nothing to test either one against yet — one tenant, one surface, no second case to validate the design.
- **Two extra safety checks that were considered and left out, since neither failure has ever actually happened:**
  the balance shown after a transfer is trusted as-is, with nothing recalculating what it should be and comparing;
  and the transaction ID lookup trusts its own best guess at which record is correct, with nothing stopping to ask
  for confirmation if more than one match is possible. Both would add real protection. Both also cost something
  real to build — the second one specifically means recompiling the artifact, which carries its own risk. Since
  neither problem has ever actually occurred, both were left as-is. A related but smaller item — writing basic
  rules for each input, and the reasoning behind each locator choice, directly into the schema — was also skipped,
  but that's just missing documentation, not a missing safety check.
- **Resuming a run once an uncertain transfer's real outcome is known** — space for this was already designed into
  the schema, just never wired up; see the implementation plan below for what it would involve.

**The following are proposed as the next things to build, in priority order:**
1. The tenant profile — smallest change, largest generalization payoff.
2. **In-run resumption** — today, when a transfer's outcome cannot be verified in the browser, the run ends there:
   everything needed to investigate it is recorded, but the run itself has no way to come back to life once the
   true outcome is later established elsewhere. Building this would let a paused run pick back up once that
   outcome is known — finishing as successful if the transfer is found to have gone through, safely attempting the
   step once more if it's found that it did not, or being formally closed out if no further action is warranted —
   all within the same run, rather than requiring a fresh one to start from nothing. Left for later specifically
   because resuming after a possibly-completed, irreversible action needs to be done carefully, not built in a
   rush.
3. A second adapter — the real test of the four-method boundary.
