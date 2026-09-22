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
    Condition,
    Escalation,
    FailureDetail,
    FaultInjection,
    Locator,
    LocatorCandidate,
    Outputs,
    ParamSpec,
    ReplayResult,
    Step,
    WaitStrategy,
)
from cua.policy_gate import PolicyGate
from cua.trace import GateRecord, ReplayTraceStep
from cua.verify import evaluate, evaluate_shape, render, render_locator, resolves

# What the caller's callback returns after showing the ticket and letting a human act on the
# live browser: "approve" (they clicked Transfer themselves) or "reject" (they declined).
EscalateCallback = Callable[[Escalation], Literal["approve", "reject"]]

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
    _steps: list[ReplayTraceStep] = field(default_factory=list, init=False, repr=False)
    _escalations: list[Escalation] = field(default_factory=list, init=False, repr=False)
    session_owner: SessionOwner = field(default=SessionOwner.AGENT, init=False)
    _recovered_once: bool = field(default=False, init=False, repr=False)

    def replay(
        self, capability: Capability, params: Mapping[str, str], secrets: Mapping[str, str], start_url: str,
        on_escalate: EscalateCallback | None = None, fault: FaultInjection | None = None,
    ) -> ReplayResult:
        run_id = new_run_id()
        self._steps = []
        self._escalations = []
        self._recovered_once = False
        self.session_owner = SessionOwner.AGENT
        problems = _validate_params(capability.inputs, params) + _validate_distinct(
            capability.distinct_inputs, params
        )
        if problems:
            return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, -1, "valid parameters", "; ".join(problems)))

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
                    terminal = self._soften(i, step, self._fail(run_id, Outcome.HARD_FAILURE, i, "action succeeds", str(e)))
                    if terminal is None:
                        break
                    return self._finish(run_id, terminal)

            if not human_did_it and not pre_wait and not self._wait(step.wait_strategy, params):
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
                    classified = self._handle_classified_failure(run_id, i, step, failed, f"checkpoint {failed.kind}", secrets)
                    if classified is None:
                        continue  # recovered: retry this same step from the top
                    terminal = self._soften(i, step, classified)
                    if terminal is None:
                        break
                    return self._finish(run_id, terminal)

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
            run_id, ReplayResult(run_id=run_id, status=Outcome.SUCCESS, outputs=outputs, escalations=self._escalations)
        )

    # -- escalation (Phase 7, primary trigger only) ---------------------

    def _handle_escalation(
        self, run_id: str, i: int, step: Step, params: Mapping[str, str], decision, on_escalate: EscalateCallback
    ) -> ReplayResult | None:
        """None means: approved, a click was genuinely captured — the caller proceeds to verify
        the checkpoint exactly like a normal step. Otherwise, the terminal result to return."""
        ticket = escalation.open_ticket(self.logger, run_id, i, decision.reason)
        self.session_owner = SessionOwner.HUMAN
        self.adapter.set_session_owner("human")
        word = on_escalate(ticket)
        captured = self.adapter.captured_actions()
        self.adapter.set_session_owner("agent")
        self.session_owner = SessionOwner.AGENT

        if word == "approve" and captured:
            self._escalations.append(escalation.resolve_ticket(self.logger, ticket, "approve", captured))
            return None

        # A typed "reject" is not, by itself, proof nothing happened: a supervisor could click
        # Transfer for real and then type reject by mistake. The word alone is never trusted
        # either way — check what was actually captured against it.
        matched_submit = word == "reject" and _matches_target(step.target, captured)
        if matched_submit:
            self._escalations.append(escalation.resolve_ticket(self.logger, ticket, "reject", captured))
            obs = self.adapter.observe()
            reason = (
                "reject was signaled but a click matching the submission button was captured; "
                "the outcome cannot be trusted either way"
            )
            self._record(i, "step", step, params, GateRecord(verdict=Verdict.ESCALATE, reason=decision.reason),
                         "ok", "n/a", "n/a", "error", reason, obs.url, obs)
            return self._fail(run_id, Outcome.HARD_FAILURE, i, "a trustworthy decision", reason)

        # A clean reject, or a claimed approval with nothing actually captured — never trust the
        # claim alone; the supervisor's own click is the only thing that counts as authorizing.
        reason = "rejected by the supervisor" if word == "reject" else "approval claimed but no click was captured"
        self._escalations.append(escalation.resolve_ticket(self.logger, ticket, "reject", captured))
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
        """The Condition an error_mapping entry would need to match, for a wait that timed out
        (only option_present is ever mapped today — a dropdown option that never appeared)."""
        if ws.kind == "option_present":
            return Condition(kind="option_present", target=ws.target, value=ws.value)
        return None

    def _classified_failure(
        self, run_id: str, i: int, step: Step, failed: Condition | None, expected: str,
    ) -> ReplayResult:
        if failed is not None:
            classified = self._classify(step, failed)
            if classified is not None:
                outcome, detail = classified
                return self._outcome(run_id, outcome, business_outcome=detail)
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
                         "session re-authenticated after a genuine expiry; retrying the step",
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
            self.logger.write_bytes("prelude-00.png", self.adapter.observe().masked_screenshot)
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
                return self._outcome(run_id, Outcome.BUSINESS_OUTCOME, business_outcome="login_rejected")
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
            screenshot = f"{phase}-{step_no:02d}.png" if phase == "prelude" else f"step-{step_no:02d}.png"
            self.logger.write_bytes(screenshot, obs.masked_screenshot)
        self._steps.append(ReplayTraceStep(
            step_no=step_no, phase=phase, action=action, target=target_desc or "", value=value, gate=gate,
            precondition_status=precondition_status, wait_status=wait_status, checkpoint_status=checkpoint_status,
            result=result, error=error, url_after=url_after, screenshot=screenshot,
        ))

    def _finish(self, run_id: str, result: ReplayResult) -> ReplayResult:
        if self.logger is not None:
            self.logger.write_json("trace.json", {"run_id": run_id, "steps": [s.model_dump(mode="json") for s in self._steps]})
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
        )

    def _fail(self, run_id: str, status: Outcome, step_index: int, expected: str, observed: str) -> ReplayResult:
        return self._outcome(run_id, status, failure_detail=FailureDetail(
            step_index=step_index, expected=expected, observed=observed,
        ))


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
                Decimal(params[name])
            except InvalidOperation:
                problems.append(f"{name} is not a valid decimal")
    return problems


def _validate_distinct(groups: list[list[str]], params: Mapping[str, str]) -> list[str]:
    problems = []
    for group in groups:
        values = [params[name] for name in group if name in params]
        if len(values) == len(group) and len(set(values)) < len(values):
            problems.append(f"{' and '.join(group)} must be different")
    return problems


def _parse_money(text: str | None) -> Decimal | None:
    cleaned = (text or "").strip().replace("$", "").replace(",", "")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None
