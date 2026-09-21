"""Action guard: the Policy Gate plus the amount rules and the irreversible-step rule."""

import re
from collections.abc import Collection, Mapping
from decimal import Decimal
from urllib.parse import urljoin, urlparse

from cua.adapter import Candidate
from cua.enums import Verdict
from cua.policy_gate import Decision, PolicyGate
from cua.tools import AgentAction

_MONEY = re.compile(r"-?\d+(?:\.\d+)?")


def _decision(verdict: Verdict, reason: str) -> Decision:
    return Decision(verdict=verdict, reason=reason)


class ActionGuard:
    def __init__(
        self,
        gate: PolicyGate,
        params: Mapping[str, str],
        amount_input: str,
        inputs: Collection[str] = (),
    ):
        self._gate = gate
        self._amount_input = amount_input
        self._amount = Decimal(params[amount_input])
        self._inputs = tuple(inputs)  # every one must be entered before the first submit
        self.balance: Decimal | None = None  # set from the goal's balance read
        self.armed = False  # the tagged amount was typed on this page
        self.entered: set[str] = set()  # inputs typed or selected on this page
        self.submitted = False  # the first button pressed after the amount was typed
        self.dispatched: set[str] = set()  # buttons pressed after arming

    def set_balance(self, text: str) -> bool:
        cleaned = text.strip().replace("$", "").replace(",", "")
        if not _MONEY.fullmatch(cleaned):
            return False
        self.balance = Decimal(cleaned)
        return True

    def check(
        self, action: AgentAction, current_url: str, provenance: str, param: str | None
    ) -> Decision:
        decision = self._gate.check(self._target_url(action, current_url))
        if decision.verdict != Verdict.ALLOW:
            return decision
        if self._types_amount(action, provenance, param) or (self.armed and action.tool == "click"):
            decision = self._gate.check(current_url, self._amount, self.balance)
            if decision.verdict == Verdict.ESCALATE:  # no human handoff during discovery
                return _decision(Verdict.BLOCK, "needs approval, which is unavailable in discovery")
            if decision.verdict != Verdict.ALLOW:
                return decision
        if self.armed and self._is_button(action) and not self.submitted:
            # The first button pressed after the amount is the one that moves the money: a value
            # left at its page default would never be recorded, so every input must be entered.
            missing = [name for name in self._inputs if name not in self.entered]
            if missing:
                which = " and ".join(missing)
                return _decision(
                    Verdict.BLOCK,
                    f"the value for {which} was never entered; enter every input before submitting",
                )
        if self.armed and self._is_button(action) and self._key(action) in self.dispatched:
            return _decision(Verdict.BLOCK, "already submitted; verify the outcome instead")
        return _decision(Verdict.ALLOW, "allowed")

    def after(
        self,
        action: AgentAction,
        url_before: str,
        url_after: str,
        ok: bool,
        provenance: str,
        param: str | None,
    ) -> None:
        if not ok:
            return
        was_armed = self.armed
        if urlparse(url_before).path != urlparse(url_after).path:
            self.armed = False
            self.entered.clear()  # a new page starts with fresh fields
        if provenance == "parameter" and param and action.tool in {"type", "select"}:
            self.entered.add(param)
        if self._types_amount(action, provenance, param):
            self.armed = True
        elif was_armed and self._is_button(action):
            self.dispatched.add(self._key(action))
            self.submitted = True

    def landing(self, url: str) -> Decision:
        return self._gate.check(url)

    def _types_amount(self, action: AgentAction, provenance: str, param: str | None) -> bool:
        return action.tool == "type" and provenance == "parameter" and param == self._amount_input

    @staticmethod
    def _is_button(action: AgentAction) -> bool:
        return (
            action.tool == "click"
            and isinstance(action.element, Candidate)
            and action.element.role == "button"
        )

    @staticmethod
    def _key(action: AgentAction) -> str:
        return action.element.locator.model_dump_json()

    @staticmethod
    def _target_url(action: AgentAction, current_url: str) -> str:
        if action.tool == "navigate":
            return urljoin(current_url, action.text or "")
        if isinstance(action.element, Candidate) and action.element.href:
            return action.element.href  # a link is checked before it is clicked
        return current_url
