"""Replay Engine: runs a compiled Capability with no LLM. Happy-path core (Phase 5), the primary
escalation trigger (Phase 7: a supervisor approving an over-threshold amount), and error
classification (Phase 6): a known failure — a rejected login, an account missing from a
dropdown — is looked up in error_mapping and reported as the real answer it is (BUSINESS_OUTCOME),
not a generic crash; an unmatched failure gets one auth probe (navigate to a known-authenticated
page) before being called a genuine HARD_FAILURE, in case it is only a session expiring. A session
that is genuinely found expired this way (RECOVERABLE, business_outcome "session_expired") is now
auto-recovered in place (Phase 8): the engine re-authenticates with the fixed service account and
retries the one step that failed, at most once per run — no human is ever involved, and a caller
never even sees RECOVERABLE for this path unless the retry itself also fails. Every other
RECOVERABLE-adjacent outcome, and a HARD_FAILURE from the auth probe finding the session still
genuinely authenticated, still means: correct classification only, safe and cheap for the caller
to replay() again, not something the engine retries on its own.

Retrying the same step only actually lands correctly when that step's page is reached again by
nothing more than a fresh login — true for this capability's own first step (the Accounts
Overview balance read, right where login lands) but not guaranteed for an expiry deeper into a
multi-page flow, which would need to re-navigate back into the flow first. That's a real, known
scope boundary, not an oversight: resuming mid-flow is not attempted here.

Fault injection (`--inject-faults` only, Phase 8) makes the real app misbehave for real —
`clear_session` deletes real cookies, `transient_fail` delays one real request without dropping
it — so the step that follows fails for a genuine reason. Nothing in the classification or retry
logic above is aware a fault was ever involved; the exact same code path handles a real,
un-injected expiry or hiccup.

The secondary escalation trigger (an ambiguous post-submit state) is Phase 9, a stretch goal —
not here. With no `on_escalate` callback supplied, an ESCALATE verdict still falls back to
today's placeholder: treated as a block, since there is genuinely no one to hand off to (matches
Discovery's own behavior with no human available).

Login is not part of the compiled artifact (see compiler.py's docstring): it is a fixed,
hand-authored, checkpoint-verified prelude, run through the same adapter and gate as everything
else, never a raw Playwright call.

Every replay writes the same kind of evidence a discovery run does: a step-by-step trace.json,
a masked screenshot per step, and a result.json — via the same EvidenceLogger, so a replay run
is exactly as inspectable after the fact as a discovery run, not a black box with only a
generic action log.
"""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

from cua import escalation
from cua.adapter import Action, ActionFailed, LocatorNotFound
from cua.enums import Outcome, SessionOwner, Verdict
from cua.evidence import EvidenceLogger, new_run_id
from cua.models import (
    Capability,
    CapabilityRef,
    Condition,
    Escalation,
    FailureDetail,
    FaultInjection,
    Locator,
    LocatorCandidate,
    Outputs,
    ParamSpec,
    ReplayResult,
    SideEffects,
    Step,
    WaitStrategy,
)
from cua.money import MoneyError, parse_amount, parse_money
from cua.policy_gate import PolicyGate
from cua.trace import GateRecord, ReplayTraceStep
from cua.verify import evaluate, evaluate_shape, render, render_locator, resolves

# What the caller's callback returns after showing the ticket and letting a human act on the
# live browser: "approve" (they clicked Transfer themselves), "reject" (they declined), or
# "timeout" (nobody responded within the caller's own window — the caller decides how long that
# is and enforces it; the engine only ever sees the final word, never the waiting itself).
EscalateCallback = Callable[[Escalation], Literal["approve", "reject", "timeout"]]

_POLL_S = 0.2

_USERNAME = Locator(
    description='textbox "Username"',
    chain=[LocatorCandidate(strategy="role_name", role="textbox", nth=0)],
)
_PASSWORD = Locator(
    description='textbox "Password"',
    chain=[LocatorCandidate(strategy="role_name", role="textbox", nth=1)],
)
_LOG_IN = Locator(
    description='button "Log In"',
    chain=[
        LocatorCandidate(strategy="role_name", role="button", value="Log In"),
        LocatorCandidate(strategy="text", value="Log In"),
    ],
)
_ACCOUNTS_OVERVIEW_HEADING = Locator(
    description='heading "Accounts Overview"',
    chain=[LocatorCandidate(strategy="role_name", role="heading", value="Accounts Overview")],
)
_LOGIN_CHECK = [
    Condition(kind="url_matches", value="/parabank/overview.htm"),
    Condition(kind="element_visible", target=_ACCOUNTS_OVERVIEW_HEADING),
]
# Confirmed live (scratch/probe_bad_login.py against the real site): a wrong password lands on
# /parabank/login.htm with this exact paragraph — not a generic failure, a real answer.
_LOGIN_REJECTED = Condition(
    kind="element_visible",
    target=Locator(
        description='paragraph "The username and password could not be verified."',
        chain=[LocatorCandidate(strategy="text", value="The username and password could not be verified.")],
    ),
)
_OVERVIEW_PATH = "/parabank/overview.htm"


class ReplayError(Exception):
    """Something outside the normal outcome taxonomy: a bug, not a business result."""


@dataclass
class ReplayEngine:
    adapter: Any  # PlaywrightAdapter's four methods; never imports playwright itself
    gate: PolicyGate
    logger: EvidenceLogger | None = None  # None only in tests against a fake adapter
    operator: str | None = None  # who acts on an escalation, when someone is named; None = unattributed
    _steps: list[ReplayTraceStep] = field(default_factory=list, init=False, repr=False)
    _escalations: list[Escalation] = field(default_factory=list, init=False, repr=False)
    session_owner: SessionOwner = field(default=SessionOwner.AGENT, init=False)
    _recovered_once: bool = field(default=False, init=False, repr=False)
    _side_effects: SideEffects = field(default="none", init=False, repr=False)
    _capability_ref: CapabilityRef | None = field(default=None, init=False, repr=False)
    _shots: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def replay(
        self, capability: Capability, params: Mapping[str, str], secrets: Mapping[str, str], start_url: str,
        on_escalate: EscalateCallback | None = None, fault: FaultInjection | None = None,
    ) -> ReplayResult:
        # The one correlation ID for this whole run must be the same one the evidence folder is
        # already keyed by — never a second, independently generated ID. Found live: they had
        # been two different UUIDs since Phase 5, invisible because every individual file always
        # contained *a* valid-looking run_id, just never the *same* one as its own folder name.
        run_id = self.logger.run_id if self.logger is not None else new_run_id()
        self._steps = []
        self._escalations = []
        self._recovered_once = False
        self._side_effects = "none"
        self._shots = {}
        self._capability_ref = CapabilityRef(
            name=capability.name, version=capability.version, created_from=capability.created_from
        )
        self.session_owner = SessionOwner.AGENT
        problems = _validate_params(capability.inputs, params)
        if problems:
            return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, -1, "valid parameters", "; ".join(problems)))
        # Rules the caller's own request breaks — money precision, two inputs that must differ
        # (from/to account) — are a policy block, exactly as on the CLI: well-formed input that the
        # contract does not allow, rejected before the browser or the app is ever touched.
        broken = _validate_precision(capability.inputs, params) + _validate_distinct(
            capability.distinct_inputs, params
        )
        if broken:
            return self._finish(run_id, self._fail(run_id, Outcome.POLICY_BLOCK, -1, "policy allow", "; ".join(broken)))

        # An artifact compiled before money parameters became {{name:money}} hardcodes the app's own
        # ".00" after the amount, so it can only confirm a whole-number transfer — and would move the
        # money of any other amount, then fail to find the confirmation. Refuse those before the
        # browser opens, and hand whole numbers over in the plain form that artifact expects.
        for name in _legacy_money_inputs(capability):
            if name not in params:
                continue
            value = parse_amount(name, params[name])
            if value != value.to_integral_value():
                return self._finish(run_id, self._fail(
                    run_id, Outcome.HARD_FAILURE, -1, "valid parameters",
                    f"{name}: this artifact predates money-typed placeholders and supports whole-number "
                    "amounts only; recompile it for fractional amounts",
                ))
            params = {**params, name: str(int(value))}

        amount = self._amount(capability, params)
        # Only a non-positive amount can be judged with no balance: the gate fails closed on
        # "balance unknown" for anything else, so calling it here for a genuinely valid amount
        # would wrongly block every replay before the browser even opens.
        if amount is not None and amount <= 0:
            decision = self.gate.check(start_url, amount=amount, balance=None)
            if decision.verdict != Verdict.ALLOW:
                return self._finish(run_id, self._fail(run_id, Outcome.POLICY_BLOCK, -1, "policy allow", decision.reason))

        try:
            self.adapter.navigate(start_url)
        except ActionFailed as e:
            return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, -1, "start page reachable", str(e)))

        prelude_problem = self._login(run_id, secrets)
        if prelude_problem is not None:
            return self._finish(run_id, prelude_problem)

        balance: Decimal | None = None
        collected: dict[str, str] = {}
        fault_applied = False
        i = 0
        while i < len(capability.steps):
            step = capability.steps[i]
            if fault is not None and not fault_applied and fault.step_index == i:
                self._apply_fault(fault)
                fault_applied = True
            obs = self.adapter.observe()
            precondition_status: Literal["ok", "failed", "n/a"] = "n/a" if not step.precondition else "ok"
            unmet = next((c for c in step.precondition if not evaluate(c, self.adapter, obs, params)), None)
            if unmet is not None:
                self._record(i, "step", step, params, GateRecord(verdict=Verdict.ALLOW, reason="n/a"),
                             "failed", "n/a", "n/a", "error", f"precondition {unmet.kind} not met", obs.url, obs)
                terminal = self._soften(i, step, self._fail(run_id, Outcome.HARD_FAILURE, i, f"precondition {unmet.kind}", "not met"))
                if terminal is None:
                    break
                return self._finish(run_id, terminal)

            # The amount-vs-balance/threshold rules gate only the one step that actually moves
            # money — not the moment the amount is typed. This matches the design doc's own
            # escalation demo: the human sees a fully filled-in form (amount typed, accounts
            # picked), nothing submitted yet, not a form paused mid-fill.
            is_amount_step = amount is not None and step.is_submission
            decision = (
                self.gate.check(obs.url, amount=amount, balance=balance) if is_amount_step else self.gate.check(obs.url)
            )
            gate_record = GateRecord(verdict=decision.verdict, reason=decision.reason)
            if decision.verdict == Verdict.BLOCK:
                self._record(i, "step", step, params, gate_record, precondition_status, "n/a", "n/a",
                             "blocked", decision.reason, obs.url, obs)
                terminal = self._soften(i, step, self._fail(run_id, Outcome.POLICY_BLOCK, i, "policy allow", decision.reason))
                if terminal is None:
                    break
                return self._finish(run_id, terminal)

            human_did_it = False
            if decision.verdict == Verdict.ESCALATE:
                if on_escalate is None:
                    self._record(i, "step", step, params, gate_record, precondition_status, "n/a", "n/a",
                                 "blocked", decision.reason, obs.url, obs)
                    terminal = self._soften(i, step, self._fail(run_id, Outcome.POLICY_BLOCK, i, "policy allow", decision.reason))
                    if terminal is None:
                        break
                    return self._finish(run_id, terminal)
                escalated = self._handle_escalation(run_id, i, step, params, decision, on_escalate)
                if escalated is not None:
                    terminal = self._soften(i, step, escalated)
                    if terminal is None:
                        break
                    return self._finish(run_id, terminal)
                human_did_it = True  # approved, a click was genuinely captured
                self._side_effects = "unverified"  # the supervisor's click dispatched it; the page must still confirm

            # select/type/extract: the wait describes readiness BEFORE acting (e.g. an
            # AJAX-populated dropdown's option existing before it can be selected — the exact
            # case checklist section 5 calls out). click/navigate: the wait describes settling
            # AFTER acting (an in-page swap or a navigation completing), so it runs post-execute.
            pre_wait = step.action in {"select", "type", "extract"}
            if not human_did_it and pre_wait and not self._wait(step.wait_strategy, params):
                timeout_obs = self.adapter.observe()
                self._record(i, "step", step, params, gate_record, precondition_status, "timed_out", "n/a",
                             "error", f"wait {step.wait_strategy.kind} timed out", timeout_obs.url, timeout_obs)
                classified = self._handle_classified_failure(
                    run_id, i, step, self._wait_condition(step.wait_strategy),
                    f"wait {step.wait_strategy.kind}", secrets,
                )
                if classified is None:
                    continue  # recovered: retry this same step from the top
                terminal = self._soften(i, step, classified)
                if terminal is None:
                    break
                return self._finish(run_id, terminal)
            wait_status: Literal["ok", "timed_out", "n/a"] = "ok" if pre_wait else "n/a"

            if human_did_it:
                extracted = None  # a click the supervisor performed; nothing to extract
            else:
                try:
                    extracted = self._execute(step, params)
                except (LocatorNotFound, ActionFailed) as e:
                    error_obs = self.adapter.observe()
                    self._record(i, "step", step, params, gate_record, precondition_status, wait_status, "n/a",
                                 "error", str(e), error_obs.url, error_obs)
                    if step.is_submission and isinstance(e, ActionFailed):
                        # The click raised, but the browser may already have sent the request (a
                        # timeout after dispatch, a page closing mid-navigation). Only "no such
                        # element" proves nothing was clicked; any other failure is unverified.
                        return self._finish(run_id, self._dispatch_unverified(
                            run_id, i, capability, params, balance, "action succeeds"))
                    terminal = self._soften(i, step, self._fail(run_id, Outcome.HARD_FAILURE, i, "action succeeds", str(e)))
                    if terminal is None:
                        break
                    return self._finish(run_id, terminal)

            if not pre_wait and not self._wait(self._effective_wait(step), params):
                timeout_obs = self.adapter.observe()
                self._record(i, "step", step, params, gate_record, precondition_status, "timed_out", "n/a",
                             "error", f"wait {step.wait_strategy.kind} timed out", timeout_obs.url, timeout_obs)
                if step.is_submission:  # dispatched, and the page never confirmed it
                    return self._finish(run_id, self._dispatch_unverified(
                        run_id, i, capability, params, balance, f"wait {step.wait_strategy.kind}"))
                classified = self._handle_classified_failure(
                    run_id, i, step, self._wait_condition(step.wait_strategy),
                    f"wait {step.wait_strategy.kind}", secrets,
                )
                if classified is None:
                    continue  # recovered: retry this same step from the top
                terminal = self._soften(i, step, classified)
                if terminal is None:
                    break
                return self._finish(run_id, terminal)
            wait_status = "ok"

            after = self.adapter.observe()
            if step.action == "extract":
                shape = step.checkpoint[0].value if step.checkpoint else "nonempty"
                checkpoint_ok = evaluate_shape(extracted, shape)
                checkpoint_status: Literal["ok", "failed", "n/a"] = "ok" if checkpoint_ok else "failed"
                if not checkpoint_ok:
                    self._record(i, "step", step, params, gate_record, precondition_status, wait_status,
                                 checkpoint_status, "error", f"shape {shape} not met", after.url, after, extracted)
                    failed_shape = step.checkpoint[0] if step.checkpoint else None
                    classified = self._handle_classified_failure(run_id, i, step, failed_shape, f"shape {shape}", secrets)
                    if classified is None:
                        continue  # recovered: retry this same step from the top
                    terminal = self._soften(i, step, classified)
                    if terminal is None:
                        break
                    return self._finish(run_id, terminal)
            else:
                failed = next((c for c in step.checkpoint if not evaluate(c, self.adapter, after, params)), None)
                checkpoint_status = "n/a" if not step.checkpoint else ("failed" if failed else "ok")
                if failed is not None:
                    self._record(i, "step", step, params, gate_record, precondition_status, wait_status,
                                 checkpoint_status, "error", f"checkpoint {failed.kind} not met", after.url, after)
                    if step.is_submission:  # dispatched, and the page never confirmed it
                        return self._finish(run_id, self._dispatch_unverified(
                            run_id, i, capability, params, balance, f"checkpoint {failed.kind}"))
                    classified = self._handle_classified_failure(run_id, i, step, failed, f"checkpoint {failed.kind}", secrets)
                    if classified is None:
                        continue  # recovered: retry this same step from the top
                    terminal = self._soften(i, step, classified)
                    if terminal is None:
                        break
                    return self._finish(run_id, terminal)

            if step.is_submission:
                self._side_effects = "committed"  # dispatched AND confirmed: nothing after this may be "retried away"
            self._record(i, "step", step, params, gate_record, precondition_status, wait_status,
                         checkpoint_status, "ok", None, after.url, after, extracted)

            if step.extract_as == "policy_balance":
                balance = _parse_money(extracted)
            elif step.extract_as:
                collected[step.extract_as] = extracted or ""
            i += 1

        outputs = Outputs(
            confirmation_text=collected.get("confirmation_text", ""),
            new_balance=collected.get("new_balance"),
            transaction_id=collected.get("transaction_id"),
        )
        return self._finish(
            run_id,
            ReplayResult(
                run_id=run_id, status=Outcome.SUCCESS, outputs=outputs, escalations=self._escalations,
                side_effects=self._side_effects, capability=self._capability_ref,
            ),
        )

    # -- escalation (Phase 7, primary trigger only) ---------------------

    def _handle_escalation(
        self, run_id: str, i: int, step: Step, params: Mapping[str, str], decision, on_escalate: EscalateCallback
    ) -> ReplayResult | None:
        """None means: approved, a click was genuinely captured — the caller proceeds to verify
        the checkpoint exactly like a normal step. Otherwise, the terminal result to return."""
        ticket = escalation.open_ticket(
            self.logger, run_id, i, decision.reason, capability=self._capability_ref,
            step_description=step.target.description, screenshot=self._shot_name(f"step-{i:02d}.png", consume=False),
        )
        self.session_owner = SessionOwner.HUMAN
        self.adapter.set_session_owner("human")
        word = on_escalate(ticket)
        captured = self.adapter.captured_actions()
        self.adapter.set_session_owner("agent")
        self.session_owner = SessionOwner.AGENT

        if word == "approve" and captured:
            self._escalations.append(
                escalation.resolve_ticket(self.logger, ticket, "approve", captured, operator=self.operator))
            return None

        # A typed "reject", or a timeout with nobody there to respond, is not by itself proof
        # nothing happened: a supervisor could click Transfer for real an instant before the
        # window closes, or reject by mistake. Neither signal is trusted alone — check what was
        # actually captured against it, the same safety check either way.
        matched_submit = word in ("reject", "timeout") and _matches_target(step.target, captured)
        if matched_submit:
            self._side_effects = "unverified"  # a real click went out; only the page can say whether it took
            # The word can neither create nor erase a submission; only the page can. Verify the
            # step's own wait + checkpoint (a click can land just before the page settles, so the
            # wait runs first). Never re-clicks, never reverses: a stale signal authorizes nothing.
            settled = self._wait(self._effective_wait(step), params)
            after = self.adapter.observe()
            failed = next((c for c in step.checkpoint if not evaluate(c, self.adapter, after, params)), None)
            if settled and step.checkpoint and failed is None:
                note = (
                    "submission click captured before the signal; transfer verified executed; "
                    f"the {word} signal arrived post-dispatch and could not be applied"
                )
                self._escalations.append(
                    escalation.resolve_ticket(self.logger, ticket, word, captured, note, operator=self.operator))
                return None  # verified: the caller proceeds exactly like an approved step

            note = (
                "submission click captured but the confirmation was not verified; the transfer may or "
                "may not have posted — verify before any retry"
            )
            self._escalations.append(
                escalation.resolve_ticket(self.logger, ticket, word, captured, note, operator=self.operator))
            expected = f"checkpoint {failed.kind}" if failed is not None else "a verifiable checkpoint"
            self._record(i, "step", step, params, GateRecord(verdict=Verdict.ESCALATE, reason=decision.reason),
                         "ok", "ok" if settled else "timed_out", "failed", "error", note, after.url, after)
            return self._unverified_result(run_id, i, expected, note)

        # A clean reject, a timeout with nothing captured, or a claimed approval with nothing
        # actually captured — never trust the claim alone; the supervisor's own click is the
        # only thing that counts as authorizing.
        if word == "timeout":
            reason = "no human response within the escalation window"
        elif word == "reject":
            reason = "rejected by the supervisor"
        else:
            reason = "approval claimed but no click was captured"
        recorded_decision = "timeout" if word == "timeout" else "reject"
        self._escalations.append(
            escalation.resolve_ticket(self.logger, ticket, recorded_decision, captured, operator=self.operator))
        obs = self.adapter.observe()
        self._record(i, "step", step, params, GateRecord(verdict=Verdict.ESCALATE, reason=decision.reason),
                     "ok", "n/a", "n/a", "blocked", reason, obs.url, obs)
        return self._fail(run_id, Outcome.POLICY_BLOCK, i, "a captured approval click", reason)

    # -- error classification (Phase 6) ---------------------------------

    def _classify(self, step: Step, failed: Condition) -> tuple[Outcome, str | None] | None:
        """A known condition, already seen live and mapped at compile time — not a guess.
        `failed` is the exact, still-placeholder-bearing Condition object that didn't hold,
        compared directly against each mapping's own `when` (built the same way), so this is an
        exact match, never a fuzzy one."""
        for mapping in step.error_mapping:
            if mapping.when == failed:
                return mapping.outcome, mapping.detail
        return None

    def _probe_session(self) -> bool:
        """True if still authenticated. Navigates to a known-authenticated page and checks for
        real content there — not the URL. Confirmed live: an unauthenticated request for this
        app's own protected page is served via a server-side forward to a login/error screen
        while the URL bar keeps reading the page that was asked for, so a URL-only check gets
        fooled into reporting "still authenticated" on a session that has, in fact, expired."""
        try:
            before = self.adapter.observe().url
            self.adapter.navigate(urljoin(before, _OVERVIEW_PATH))
        except ActionFailed:
            return True  # can't tell from a failed navigation; don't claim expiry on a guess
        return resolves(self.adapter, _ACCOUNTS_OVERVIEW_HEADING)

    def _wait_condition(self, ws: WaitStrategy) -> Condition | None:
        """The Condition an error_mapping entry would need to match, for a wait that timed out.
        Two kinds are ever mapped today: a dropdown option that never appeared (a missing
        destination account) and an element that never became visible (a missing source
        account's own balance row) — both compiler-emitted, both matched here exactly, never a
        guess at what the timeout meant."""
        if ws.kind == "option_present":
            return Condition(kind="option_present", target=ws.target, value=ws.value)
        if ws.kind == "element_visible":
            return Condition(kind="element_visible", target=ws.target)
        return None

    def _classified_failure(
        self, run_id: str, i: int, step: Step, failed: Condition | None, expected: str,
    ) -> ReplayResult:
        if failed is not None:
            classified = self._classify(step, failed)
            if classified is not None:
                outcome, detail = classified
                return self._outcome(run_id, outcome, business_outcome=detail, failure_detail=FailureDetail(
                    step_index=i, expected=_describe(failed), observed=f"known condition, mapped to {detail}",
                ))
        if self._probe_session():
            return self._fail(run_id, Outcome.HARD_FAILURE, i, expected, "not met")
        return self._outcome(run_id, Outcome.RECOVERABLE, business_outcome="session_expired")

    def _handle_classified_failure(
        self, run_id: str, i: int, step: Step, failed: Condition | None, expected: str,
        secrets: Mapping[str, str],
    ) -> ReplayResult | None:
        """None means: a genuinely expired session was just auto re-authenticated — the caller
        retries this same step now. Otherwise, the terminal result to return.

        Retried at most once per run: a session that keeps expiring right after a fresh login is
        not a transient blip, and the second failure — whatever it classifies as — is returned
        as-is rather than looping. This is reached exactly the same way for a real, un-injected
        expiry as for a fault-injected one; nothing here is aware of --inject-faults."""
        result = self._classified_failure(run_id, i, step, failed, expected)
        if (
            result.status == Outcome.RECOVERABLE
            and result.business_outcome == "session_expired"
            and not self._recovered_once
        ):
            self._recovered_once = True
            login_problem = self._login(run_id, secrets)
            if login_problem is not None:
                return login_problem
            self._record(-1, "prelude", None, {}, None, "n/a", "n/a", "n/a", "recovered",
                         "session found expired (probe-confirmed); re-authenticated; retrying the step",
                         None, None, action="navigate", target_desc="session recovery")
            return None
        return result

    def _soften(self, i: int, step: Step, result: ReplayResult) -> ReplayResult | None:
        """None means: this step's own failure is fine to swallow — it's part of a best-effort
        trailing lookup (transaction_id) whose own failure must never fail a run whose real work
        (the transfer, new_balance) already succeeded. The caller abandons the rest of that
        tail — breaks the loop rather than returning — and still finishes as SUCCESS with that
        one output left unset, matching the documented contract: a failed or ambiguous match
        yields None, never a crash and never a guessed ID. A SUCCESS result is never softened —
        there is nothing to swallow."""
        if not (step.best_effort and result.status != Outcome.SUCCESS):
            return result
        self._record(i, "step", None, {}, None, "n/a", "n/a", "n/a", "abandoned",
                     f"best-effort lookup gave up: {result.status.value}", None, None,
                     action=step.action, target_desc="(best-effort tail abandoned)")
        return None

    def _apply_fault(self, fault: FaultInjection) -> None:
        """Phase 8, --inject-faults only. Makes the real app behave the way a genuine transient
        network hiccup or a genuine session expiry would — the step that follows has no idea a
        fault was ever involved, only that its wait/checkpoint failed for a real reason."""
        # Recorded as data, never consulted: nothing below reads this entry. The engine still handles
        # what follows exactly as it would a real failure of the same shape; the trace just says
        # plainly that this run was a deliberately induced one.
        self._record(-1, "prelude", None, {}, None, "n/a", "n/a", "n/a", "injected",
                     f"operator-requested fault, applied before step {fault.step_index}: {fault.fault_type}",
                     None, None, action="fault", target_desc=f"injected {fault.fault_type}")
        if fault.fault_type == "clear_session":
            self.adapter.clear_session()
        elif fault.fault_type == "transient_fail":
            self.adapter.delay_next_request(fault.url_pattern, fault.delay_ms)

    # -- steps ---------------------------------------------------------

    def _execute(self, step: Step, params: Mapping[str, str]) -> str | None:
        if self.session_owner != SessionOwner.AGENT:
            raise ReplayError("an action was attempted while the session owner was not the agent")
        target = render_locator(step.target, params)
        handle = self.adapter.resolve(target)
        value = render(step.parameters["value"], params) if "value" in step.parameters else None
        # `select` matches Playwright's option `value` attribute, not its visible label. This
        # recording's target app happens to set both to the same text (proven across every
        # discovery run); a page where they differ would need label-based matching instead.
        return self.adapter.act(handle, Action(step.action, value))

    def _wait(self, ws: WaitStrategy, params: Mapping[str, str]) -> bool:
        """Poll until `ws`'s condition holds, or its own timeout elapses. True means settled.

        `network_idle` needs no extra polling here: every observe() already waits out in-flight
        requests before returning (PlaywrightAdapter._settled_snapshot). `fixed_ms` has no
        condition to poll, only a bare delay — no compiled step uses it today.
        """
        if ws.kind == "network_idle":
            return True
        if ws.kind == "fixed_ms":
            time.sleep((ws.timeout_ms or 0) / 1000)
            return True
        target = render_locator(ws.target, params) if ws.target else None
        value = render(ws.value, params) if ws.value else ws.value
        deadline = time.monotonic() + ws.timeout_ms / 1000
        while True:
            if ws.kind == "url_change":
                ok = urlparse(self.adapter.observe().url).path == value
            elif ws.kind == "element_visible":
                ok = resolves(self.adapter, target)
            elif ws.kind == "element_hidden":
                ok = not resolves(self.adapter, target)
            elif ws.kind == "option_present":
                obs = self.adapter.observe()
                cand = next((c for c in obs.candidates if c.locator == target), None)
                ok = cand is not None and value in cand.options
            else:
                ok = True
            if ok:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_S)

    def _login(self, run_id: str, secrets: Mapping[str, str]) -> ReplayResult | None:
        """None means: logged in, the caller proceeds. Otherwise, the terminal result."""
        if self.logger is not None:
            self.logger.write_bytes(self._shot_name("prelude-00.png"), self.adapter.observe().masked_screenshot)
        for n, (locator, value) in enumerate(((_USERNAME, secrets["username"]), (_PASSWORD, secrets["password"]))):
            url = self.adapter.observe().url
            decision = self.gate.check(url)
            gate_record = GateRecord(verdict=decision.verdict, reason=decision.reason)
            if decision.verdict != Verdict.ALLOW:
                block_obs = self.adapter.observe()
                self._record(n, "prelude", None, {}, gate_record, "n/a", "n/a", "n/a", "blocked", decision.reason,
                             url, block_obs, action="type", target_desc=locator.description)
                return self._fail(run_id, Outcome.POLICY_BLOCK, -1, "policy allow", decision.reason)
            try:
                handle = self.adapter.resolve(locator)
                self.adapter.act(handle, Action("type", value))
            except (LocatorNotFound, ActionFailed) as e:
                error_obs = self.adapter.observe()
                self._record(n, "prelude", None, {}, gate_record, "n/a", "n/a", "n/a", "error", str(e),
                             error_obs.url, error_obs, action="type", target_desc=locator.description)
                return self._fail(run_id, Outcome.HARD_FAILURE, -1, "login field present", str(e))
            self._record(n, "prelude", None, {}, gate_record, "n/a", "n/a", "n/a", "ok", None, url, None,
                         action="type", target_desc=locator.description)
        try:
            handle = self.adapter.resolve(_LOG_IN)
            self.adapter.act(handle, Action("click", None))
        except (LocatorNotFound, ActionFailed) as e:
            error_obs = self.adapter.observe()
            self._record(2, "prelude", None, {}, None, "n/a", "n/a", "n/a", "error", str(e),
                         error_obs.url, error_obs, action="click", target_desc=_LOG_IN.description)
            return self._fail(run_id, Outcome.HARD_FAILURE, -1, "Log In button present", str(e))
        obs = self.adapter.observe()
        unmet = next((c for c in _LOGIN_CHECK if not evaluate(c, self.adapter, obs, {})), None)
        checkpoint_status: Literal["ok", "failed"] = "failed" if unmet is not None else "ok"
        self._record(2, "prelude", None, {}, None, "n/a", "n/a", checkpoint_status,
                     "error" if unmet else "ok", f"login {unmet.kind} not met" if unmet else None, obs.url, obs,
                     action="click", target_desc=_LOG_IN.description)
        if unmet is not None:
            if evaluate(_LOGIN_REJECTED, self.adapter, obs, {}):
                return self._outcome(
                    run_id, Outcome.BUSINESS_OUTCOME, business_outcome="login_rejected",
                    failure_detail=FailureDetail(
                        step_index=-1, expected="login lands on the accounts overview",
                        observed=f"known condition: {_LOGIN_REJECTED.target.description}",
                    ),
                )
            return self._fail(run_id, Outcome.HARD_FAILURE, -1, f"login {unmet.kind}", "not met after Log In")
        return None

    # -- evidence --------------------------------------------------------

    def _record(
        self, step_no: int, phase: Literal["prelude", "step"], step: Step | None, params: Mapping[str, str],
        gate: GateRecord | None, precondition_status: str, wait_status: str, checkpoint_status: str,
        result: Literal["ok", "blocked", "error"], error: str | None, url_after: str | None, obs: Any,
        value: str | None = None, action: str | None = None, target_desc: str | None = None,
    ) -> None:
        if step is not None:
            action = step.action
            target_desc = render_locator(step.target, params).description
        screenshot = None
        if self.logger is not None and obs is not None:
            base = f"{phase}-{step_no:02d}.png" if phase == "prelude" else f"step-{step_no:02d}.png"
            screenshot = self._shot_name(base)
            self.logger.write_bytes(screenshot, obs.masked_screenshot)
        self._steps.append(ReplayTraceStep(
            step_no=step_no, phase=phase, action=action, target=target_desc or "", value=value, gate=gate,
            precondition_status=precondition_status, wait_status=wait_status, checkpoint_status=checkpoint_status,
            result=result, error=error, url_after=url_after, screenshot=screenshot,
        ))

    def _shot_name(self, base: str, *, consume: bool = True) -> str:
        """The evidence file for this attempt. A step that runs twice (a retry after session
        recovery, a second login) gets its own file, so the first attempt's screenshot survives.
        consume=False only reports the name the next attempt will get."""
        count = self._shots.get(base, 0)
        if consume:
            self._shots[base] = count + 1
        return base if count == 0 else base.replace(".png", f"-retry{count}.png")

    def _latest_shot(self, base: str) -> str | None:
        """The most recent evidence file already written under this base name, if any."""
        count = self._shots.get(base, 0)
        if count == 0:
            return None
        return base if count == 1 else base.replace(".png", f"-retry{count - 1}.png")

    def _finish(self, run_id: str, result: ReplayResult) -> ReplayResult:
        if self.logger is not None:
            capability = self._capability_ref.model_dump(mode="json") if self._capability_ref else None
            self.logger.write_json("trace.json", {
                "run_id": run_id, "capability": capability, "steps": [s.model_dump(mode="json") for s in self._steps],
            })
            self.logger.write_json("result.json", result.model_dump(mode="json"))
        return result

    @staticmethod
    def _amount(capability: Capability, params: Mapping[str, str]) -> Decimal | None:
        if not capability.amount_input or capability.amount_input not in params:
            return None
        return Decimal(params[capability.amount_input])

    def _outcome(
        self, run_id: str, status: Outcome, business_outcome: str | None = None,
        failure_detail: FailureDetail | None = None,
    ) -> ReplayResult:
        return ReplayResult(
            run_id=run_id, status=status, business_outcome=business_outcome,
            failure_detail=failure_detail, escalations=self._escalations,
            side_effects=self._side_effects, capability=self._capability_ref,
        )

    def _fail(self, run_id: str, status: Outcome, step_index: int, expected: str, observed: str) -> ReplayResult:
        return self._outcome(run_id, status, failure_detail=FailureDetail(
            step_index=step_index, expected=expected, observed=observed,
        ))

    # -- unverified dispatch ---------------------------------------------
    # The rule: once the money-moving click has been dispatched, the engine never clicks it again.
    # No session probe, no re-login, no retry — those recovery paths would re-execute an
    # irreversible step. The only exits are a verified confirmation or a human.

    def _effective_wait(self, step: Step) -> WaitStrategy:
        """The artifact's own wait, except the money-moving step: a bank can take longer than the
        few seconds a compiled wait allows, and a false alarm there wastes a human."""
        if not step.is_submission or step.wait_strategy.kind == "fixed_ms":  # fixed_ms is a bare delay
            return step.wait_strategy
        return step.wait_strategy.model_copy(update={"timeout_ms": self.gate.config.submit_confirmation_wait_ms})

    def _unverified_result(self, run_id: str, step_index: int, expected: str, observed: str) -> ReplayResult:
        result = self._fail(run_id, Outcome.HARD_FAILURE, step_index, expected, observed)
        return result.model_copy(update={"business_outcome": "dispatch_unverified"})

    def _dispatch_unverified(
        self, run_id: str, i: int, capability: Capability, params: Mapping[str, str],
        balance: Decimal | None, expected: str,
    ) -> ReplayResult:
        """Record a durable ticket a human can act on, then stop. The ticket stays "open": there
        is no in-run resume, so nothing here ever resolves it."""
        self._side_effects = "unverified"
        ticket = escalation.open_ticket(
            self.logger, run_id, i, "money-moving step dispatched but its confirmation was never verified",
            self._verification_procedure(capability, params, balance), capability=self._capability_ref,
            step_description=capability.steps[i].target.description,
            screenshot=self._latest_shot(f"step-{i:02d}.png"),  # already written by the failing step's own record
        )
        self._escalations.append(ticket)
        return self._unverified_result(
            run_id, i, expected,
            "the transfer may or may not have posted — verify before any retry",
        )

    @staticmethod
    def _verification_procedure(capability: Capability, params: Mapping[str, str], balance: Decimal | None) -> str:
        """Built from the artifact's own read-only lookup steps, so the engine names no page or
        button of any particular app."""
        inputs = ", ".join(f"{name}={value}" for name, value in params.items())
        lines = [
            (
                "A money-moving step was clicked but its confirmation was never seen. "
                "The action may or may not have posted. Do not retry until it is verified."
            ),
            f"Inputs: {inputs}.",
        ]
        if balance is not None:
            lines.append(f"Balance before the step: {balance}.")
        lookup = [s for s in capability.steps if s.best_effort]
        if lookup:
            lines.append("To verify, look up the transaction in the app:")
            for n, s in enumerate(lookup, start=1):
                given = ", ".join(f"{k}={render(str(v), params)}" for k, v in s.parameters.items())
                lines.append(f"{n}. {s.action} {render(s.target.description, params)}" + (f" ({given})" if given else ""))
        return "\n".join(lines)


def _describe(cond: Condition) -> str:
    """A condition in words, from the artifact's own text: placeholders stay placeholders
    ({{from_account}}), so it names the parameter and never leaks its value."""
    target = cond.target.description if cond.target else ""
    return f"{cond.kind} {target}" + (f" = {cond.value}" if cond.value else "")


def _matches_target(target, captured: list[dict]) -> bool:
    """Does any captured click's text match the submission step's own target — e.g. "Transfer"
    — derived from the target itself, not hardcoded to this one capability's button name."""
    name = next((c.value for c in target.chain if c.value), None)
    if not name:
        return False
    name = name.strip().lower()
    return any((a.get("text") or "").strip().lower() == name for a in captured)


def _validate_params(inputs: Mapping[str, ParamSpec], params: Mapping[str, str]) -> list[str]:
    problems = []
    for name, spec in inputs.items():
        if name not in params:
            if spec.required:
                problems.append(f"{name} is required")
            continue
        if spec.type == "decimal":
            try:
                parse_amount(name, params[name])
            except MoneyError as e:
                if not e.is_precision:  # a precision breach is a policy block, judged separately
                    problems.append(str(e))
    return problems


def _validate_precision(inputs: Mapping[str, ParamSpec], params: Mapping[str, str]) -> list[str]:
    problems = []
    for name, spec in inputs.items():
        if spec.type == "decimal" and name in params:
            try:
                parse_amount(name, params[name])
            except MoneyError as e:
                if e.is_precision:
                    problems.append(str(e))
    return problems


def _legacy_money_inputs(capability: Capability) -> list[str]:
    """Decimal inputs this artifact still writes as a bare {{name}} (its pre-money-typed form)."""
    text = json.dumps([s.model_dump(mode="json") for s in capability.steps])
    return [n for n, p in capability.inputs.items() if p.type == "decimal" and "{{" + n + "}}" in text]


def _validate_distinct(groups: list[list[str]], params: Mapping[str, str]) -> list[str]:
    problems = []
    for group in groups:
        values = [params[name] for name in group if name in params]
        if len(values) == len(group) and len(set(values)) < len(values):
            problems.append(f"{' and '.join(group)} must be different")
    return problems


def _parse_money(text: str | None) -> Decimal | None:
    try:
        return parse_money(text or "")
    except InvalidOperation:
        return None
