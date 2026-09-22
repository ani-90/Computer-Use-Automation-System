"""Replay Engine: runs a compiled Capability with no LLM. Happy-path core (Phase 5), plus the
primary escalation trigger (Phase 7): a supervisor approving an over-threshold amount.

Error classification beyond "checkpoint failed" (the auth probe, session-expiry recovery,
error_mapping content) is Phase 6. The secondary escalation trigger (an ambiguous post-submit
state) is Phase 9, a stretch goal — not here. With no `on_escalate` callback supplied, an
ESCALATE verdict still falls back to today's placeholder: treated as a block, since there is
genuinely no one to hand off to (matches Discovery's own behavior with no human available).

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
from urllib.parse import urlparse

from cua import escalation
from cua.adapter import Action, ActionFailed, LocatorNotFound
from cua.enums import Outcome, SessionOwner, Verdict
from cua.evidence import EvidenceLogger, new_run_id
from cua.models import (
    Capability,
    Condition,
    Escalation,
    FailureDetail,
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
_LOGIN_CHECK = [
    Condition(kind="url_matches", value="/parabank/overview.htm"),
    Condition(
        kind="element_visible",
        target=Locator(
            description='heading "Accounts Overview"',
            chain=[LocatorCandidate(strategy="role_name", role="heading", value="Accounts Overview")],
        ),
    ),
]


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

    def replay(
        self, capability: Capability, params: Mapping[str, str], secrets: Mapping[str, str], start_url: str,
        on_escalate: EscalateCallback | None = None,
    ) -> ReplayResult:
        run_id = new_run_id()
        self._steps = []
        self._escalations = []
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

        prelude_problem = self._login(secrets)
        if prelude_problem:
            return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, -1, *prelude_problem))

        balance: Decimal | None = None
        collected: dict[str, str] = {}
        for i, step in enumerate(capability.steps):
            obs = self.adapter.observe()
            precondition_status: Literal["ok", "failed", "n/a"] = "n/a" if not step.precondition else "ok"
            unmet = next((c for c in step.precondition if not evaluate(c, self.adapter, obs, params)), None)
            if unmet is not None:
                self._record(i, "step", step, params, GateRecord(verdict=Verdict.ALLOW, reason="n/a"),
                             "failed", "n/a", "n/a", "error", f"precondition {unmet.kind} not met", obs.url, obs)
                return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, i, f"precondition {unmet.kind}", "not met"))

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
                return self._finish(run_id, self._fail(run_id, Outcome.POLICY_BLOCK, i, "policy allow", decision.reason))

            human_did_it = False
            if decision.verdict == Verdict.ESCALATE:
                if on_escalate is None:
                    self._record(i, "step", step, params, gate_record, precondition_status, "n/a", "n/a",
                                 "blocked", decision.reason, obs.url, obs)
                    return self._finish(run_id, self._fail(run_id, Outcome.POLICY_BLOCK, i, "policy allow", decision.reason))
                terminal = self._handle_escalation(run_id, i, step, params, decision, on_escalate)
                if terminal is not None:
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
                return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, i, f"wait {step.wait_strategy.kind}", "timed out"))
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
                    return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, i, "action succeeds", str(e)))

            if not human_did_it and not pre_wait and not self._wait(step.wait_strategy, params):
                timeout_obs = self.adapter.observe()
                self._record(i, "step", step, params, gate_record, precondition_status, "timed_out", "n/a",
                             "error", f"wait {step.wait_strategy.kind} timed out", timeout_obs.url, timeout_obs)
                return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, i, f"wait {step.wait_strategy.kind}", "timed out"))
            wait_status = "ok"

            after = self.adapter.observe()
            if step.action == "extract":
                shape = step.checkpoint[0].value if step.checkpoint else "nonempty"
                checkpoint_ok = evaluate_shape(extracted, shape)
                checkpoint_status: Literal["ok", "failed", "n/a"] = "ok" if checkpoint_ok else "failed"
                if not checkpoint_ok:
                    self._record(i, "step", step, params, gate_record, precondition_status, wait_status,
                                 checkpoint_status, "error", f"shape {shape} not met", after.url, after, extracted)
                    return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, i, f"shape {shape}", str(extracted)))
            else:
                failed = next((c for c in step.checkpoint if not evaluate(c, self.adapter, after, params)), None)
                checkpoint_status = "n/a" if not step.checkpoint else ("failed" if failed else "ok")
                if failed is not None:
                    self._record(i, "step", step, params, gate_record, precondition_status, wait_status,
                                 checkpoint_status, "error", f"checkpoint {failed.kind} not met", after.url, after)
                    return self._finish(run_id, self._fail(run_id, Outcome.HARD_FAILURE, i, f"checkpoint {failed.kind}", "not met"))

            self._record(i, "step", step, params, gate_record, precondition_status, wait_status,
                         checkpoint_status, "ok", None, after.url, after, extracted)

            if step.extract_as == "policy_balance":
                balance = _parse_money(extracted)
            elif step.extract_as:
                collected[step.extract_as] = extracted or ""

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

    def _login(self, secrets: Mapping[str, str]) -> tuple[str, str] | None:
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
                return ("policy allow", decision.reason)
            try:
                handle = self.adapter.resolve(locator)
                self.adapter.act(handle, Action("type", value))
            except (LocatorNotFound, ActionFailed) as e:
                error_obs = self.adapter.observe()
                self._record(n, "prelude", None, {}, gate_record, "n/a", "n/a", "n/a", "error", str(e),
                             error_obs.url, error_obs, action="type", target_desc=locator.description)
                return ("login field present", str(e))
            self._record(n, "prelude", None, {}, gate_record, "n/a", "n/a", "n/a", "ok", None, url, None,
                         action="type", target_desc=locator.description)
        try:
            handle = self.adapter.resolve(_LOG_IN)
            self.adapter.act(handle, Action("click", None))
        except (LocatorNotFound, ActionFailed) as e:
            error_obs = self.adapter.observe()
            self._record(2, "prelude", None, {}, None, "n/a", "n/a", "n/a", "error", str(e),
                         error_obs.url, error_obs, action="click", target_desc=_LOG_IN.description)
            return ("Log In button present", str(e))
        obs = self.adapter.observe()
        unmet = next((c for c in _LOGIN_CHECK if not evaluate(c, self.adapter, obs, {})), None)
        checkpoint_status: Literal["ok", "failed"] = "failed" if unmet is not None else "ok"
        self._record(2, "prelude", None, {}, None, "n/a", "n/a", checkpoint_status,
                     "error" if unmet else "ok", f"login {unmet.kind} not met" if unmet else None, obs.url, obs,
                     action="click", target_desc=_LOG_IN.description)
        if unmet is not None:
            return (f"login {unmet.kind}", "not met after Log In")
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

    def _fail(self, run_id: str, status: Outcome, step_index: int, expected: str, observed: str) -> ReplayResult:
        return ReplayResult(
            run_id=run_id, status=status,
            failure_detail=FailureDetail(step_index=step_index, expected=expected, observed=observed),
            escalations=self._escalations,
        )


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
