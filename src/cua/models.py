"""Pydantic contracts: capability artifact, replay result, escalation ticket."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.enums import Outcome


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Shapes a captured value must match; declared once, shared by the outputs contract,
# the goal spec and the extract checkpoints.
SHAPES = {"nonempty": r".+", "money": r"-?\$-?\d+(?:\.\d+)?"}


class LocatorCandidate(_Strict):
    # No css/xpath strategy on purpose: a target is a description, never a raw selector.
    strategy: Literal["role_name", "label", "text", "placeholder", "table_cell"]
    # accessible name / label / text; for table_cell the row's anchor (its first cell's text).
    # None for a role-only match.
    value: str | None = None
    role: str | None = None  # used with role_name
    # 0-based pick among several matches; -1 means the last match. For table_cell it picks
    # among rows that share the anchor.
    nth: int | None = None
    column: str | None = None  # table_cell: the column header name
    col: int | None = None  # table_cell: 0-based position, for tables without headers

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        if self.strategy == "table_cell":
            if self.value is None or (self.column is None) == (self.col is None):
                raise ValueError("table_cell requires value and exactly one of column / col")
        elif self.strategy == "role_name":
            if self.role is None:
                raise ValueError("role_name requires a role")
        elif self.value is None:
            raise ValueError(f"{self.strategy} requires a value")
        return self


class Locator(_Strict):
    description: str
    chain: list[LocatorCandidate] = Field(min_length=1)  # ranked, best first


class Condition(_Strict):
    """Used for precondition, checkpoint and error_mapping triggers.

    `value` may hold {{param}} placeholders, e.g. text_equals "{{from_account}}".
    """

    kind: Literal[
        "url_matches", "element_visible", "element_absent", "text_present", "text_equals",
        "field_value_equals", "field_filled", "option_selected", "option_present", "shape_matches",
    ]
    target: Locator | None = None
    value: str | None = None

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        needs_target = self.kind != "url_matches"
        needs_value = self.kind in {
            "url_matches", "text_present", "text_equals",
            "field_value_equals", "option_selected", "option_present", "shape_matches",
        }
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
    # Both are all_of lists: every condition must hold. Empty means nothing is asserted.
    precondition: list[Condition] = Field(default_factory=list)
    action: Literal["click", "type", "select", "navigate", "extract"]
    target: Locator
    parameters: dict[str, str]  # may hold {{param}} placeholders, never literal values
    wait_strategy: WaitStrategy
    checkpoint: list[Condition] = Field(default_factory=list)
    error_mapping: list[ErrorMapping]
    # Only for action="extract": which capability output this fills, or the policy-only
    # balance read (never a declared output). None for every other action and for an extract
    # whose value isn't kept at all.
    extract_as: Literal["confirmation_text", "new_balance", "transaction_id", "policy_balance"] | None = None
    # True on exactly one step: the first click/navigate whose precondition already asserts the
    # amount is set — the one action that actually moves money. Not every click that happens to
    # follow the amount being typed (e.g. a later read-only search reusing the same value).
    is_submission: bool = False
    # True for every step that only ever feeds transaction_id — a read-only lookup that runs
    # after the real transfer and new_balance have already succeeded. Its own failure must never
    # fail the whole run: transaction_id is documented as best-effort, None on a failed or
    # ambiguous match, never a guessed ID (see CLAUDE.md / review_checklist_4.md section 5).
    best_effort: bool = False


class ParamSpec(_Strict):
    type: Literal["string", "decimal"]
    required: bool = True


class Capability(_Strict):
    schema_version: str  # the schema itself is versioned
    version: str  # this artifact instance
    name: str
    inputs: dict[str, ParamSpec]
    outputs: dict[str, ParamSpec]
    # Which declared input the Policy Gate's amount rules apply to. None if this capability
    # never moves money (not every capability needs it, so it's optional, not assumed).
    amount_input: str | None = None
    # Groups of inputs that must differ (e.g. from_account and to_account) — checked before the
    # browser is touched, same as Discovery's own parameter validation.
    distinct_inputs: list[list[str]] = Field(default_factory=list)
    steps: list[Step]
    # The discovery run this artifact was compiled from (its run_id, and so its evidence folder's
    # name): the artifact -> discovery evidence link an auditor follows. None for an artifact
    # written before this field existed.
    created_from: str | None = None


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
    # "timeout": no human response arrived within the escalation window — distinct from "reject"
    # (an active decision) so the evidence honestly shows *why* nothing was approved.
    decision: Literal["approve", "reject", "timeout", "complete", "retry_step", "abort"] | None = None
    captured_human_actions: list[dict[str, str]] = Field(default_factory=list)
    # Set only when the human's word and the page disagree (e.g. "reject" typed after a real
    # Transfer click): the conflict is recorded as data, never silently resolved.
    note: str | None = None
    # Set only on a dispatched-but-unverified ticket: what a human needs to check the ledger.
    # Never returned to an HTTP caller (it embeds the run's real parameters).
    procedure: str | None = None


class FaultInjection(_Strict):
    """Phase 8, explicit opt-in only. Never constructed on a normal replay() call: built only by the
    CLI when `--inject-faults` is given, or by the capability service when its operator sets
    CUA_FAULT in the environment before starting it. Never from a request body."""

    step_index: int
    fault_type: Literal["transient_fail", "clear_session"]
    # transient_fail: a URL pattern (glob, as Playwright's page.route expects) whose next
    # matching request is delayed, not dropped. Required only for transient_fail.
    url_pattern: str | None = None
    delay_ms: int = 3000

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        if self.fault_type == "transient_fail" and not self.url_pattern:
            raise ValueError("transient_fail requires url_pattern")
        return self


class ReplayResult(_Strict):
    run_id: str  # our own correlation ID, not the bank's
    status: Outcome
    outputs: Outputs | None = None
    business_outcome: str | None = None
    failure_detail: FailureDetail | None = None
    escalations: list[Escalation] = Field(default_factory=list)
    llm_calls: int = 0  # always 0: computed, never set by anything, never hardcoded elsewhere
