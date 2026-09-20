"""Pydantic contracts: capability artifact, replay result, escalation ticket."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.enums import Outcome


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocatorCandidate(_Strict):
    # No css/xpath strategy on purpose: a target is a description, never a raw selector.
    strategy: Literal["role_name", "label", "text", "placeholder"]
    value: str | None = None  # accessible name / label / text; None for a role-only match
    role: str | None = None  # used with role_name
    nth: int | None = None  # 0-based pick among several matches; -1 means the last match

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        if self.strategy == "role_name" and self.role is None:
            raise ValueError("role_name requires a role")
        if self.strategy != "role_name" and self.value is None:
            raise ValueError(f"{self.strategy} requires a value")
        return self


class Locator(_Strict):
    description: str
    chain: list[LocatorCandidate] = Field(min_length=1)  # ranked, best first


class Condition(_Strict):
    """Used for precondition, checkpoint and error_mapping triggers.

    `value` may hold {{param}} placeholders, e.g. text_equals "{{from_account}}".
    """

    kind: Literal["url_matches", "element_visible", "element_absent", "text_present", "text_equals"]
    target: Locator | None = None
    value: str | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        needs_target = self.kind != "url_matches"
        needs_value = self.kind in {"url_matches", "text_present", "text_equals"}
        if needs_target and self.target is None:
            raise ValueError(f"{self.kind} requires a target")
        if needs_value and self.value is None:
            raise ValueError(f"{self.kind} requires a value")
        return self


class WaitStrategy(_Strict):
    kind: Literal[
        "url_change", "element_visible", "element_hidden", "option_present", "network_idle", "fixed_ms"
    ]
    target: Locator | None = None  # option_present: the dropdown; value is the option
    value: str | None = None
    timeout_ms: int = 10_000


class ErrorMapping(_Strict):
    when: Condition
    outcome: Outcome
    detail: str | None = None  # e.g. "invalid_account"


class Step(_Strict):
    precondition: Condition | None
    action: Literal["click", "type", "select", "navigate", "extract"]
    target: Locator
    parameters: dict[str, str]  # may hold {{param}} placeholders, never literal values
    wait_strategy: WaitStrategy
    checkpoint: Condition | None
    error_mapping: list[ErrorMapping]


class ParamSpec(_Strict):
    type: Literal["string", "decimal"]
    required: bool = True


class Capability(_Strict):
    schema_version: str  # the schema itself is versioned
    version: str  # this artifact instance
    name: str
    inputs: dict[str, ParamSpec]
    outputs: dict[str, ParamSpec]
    steps: list[Step]


class Outputs(_Strict):
    confirmation_text: str
    new_balance: str | None = None  # as rendered, e.g. "$1100.00"
    transaction_id: str | None = None  # None when the best-effort match fails


class FailureDetail(_Strict):
    step_index: int
    expected: str
    observed: str


class Escalation(_Strict):
    ticket_id: str
    run_id: str
    step_index: int | None = None
    reason: str
    status: Literal["open", "resolved"] = "open"
    decision: Literal["approve", "reject", "complete", "retry_step", "abort"] | None = None
    captured_human_actions: list[dict[str, str]] = Field(default_factory=list)


class ReplayResult(_Strict):
    run_id: str  # our own correlation ID, not the bank's
    status: Outcome
    outputs: Outputs | None = None
    business_outcome: str | None = None
    failure_detail: FailureDetail | None = None
    escalations: list[Escalation] = Field(default_factory=list)
