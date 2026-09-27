"""Trace of a discovery run: one record per step, plus the run result."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.enums import StopReason, Verdict
from cua.models import Condition, Escalation, Locator, SideEffects


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateRecord(_Model):
    verdict: Verdict
    reason: str


class TraceStep(_Model):
    step_no: int
    counted: bool = True  # counts against MAX_STEPS
    tool: Literal["click", "type", "select", "navigate", "extract", "report_done", "report_stuck"]
    ref: int | None = None
    target: Locator | None = None  # the resolved locator, never the discovery-only index
    value: str | None = None  # typed / selected / extracted text; never a secret
    provenance: Literal["parameter", "secret", "other"] = "other"
    param: str | None = None
    extract_name: str | None = None
    expect: str | None = None
    expectation: Literal["confirmed", "weak", "refuted"] | None = None
    checkpoint_status: Literal["verified", "unverified", "failed"] | None = None
    checkpoint: list[Condition] = Field(default_factory=list)
    gate: GateRecord | None = None
    result: Literal["ok", "error", "blocked"]
    error: str | None = None
    url_after: str | None = None
    reasoning: str = ""
    screenshot: str | None = None  # path of the masked screenshot

    @model_validator(mode="after")
    def _no_secret_values(self) -> Self:
        if self.provenance == "secret" and self.value is not None:
            raise ValueError("a secret value must never be stored in the trace")
        return self


class ReplayTraceStep(_Model):
    """One record per replay step, written to evidence/<run_id>/trace.json — the Replay
    counterpart to TraceStep, so a replay run is exactly as inspectable as a discovery run."""

    step_no: int  # -1 for the login prelude's own steps
    phase: Literal["prelude", "step"]
    action: Literal["click", "type", "select", "navigate", "extract", "fault"]
    target: str  # the rendered locator description (redacted at write time like everything else)
    value: str | None = None  # an extract's own value; never a secret (prelude never logs one)
    gate: GateRecord | None = None
    precondition_status: Literal["ok", "failed", "n/a"] = "n/a"
    wait_status: Literal["ok", "timed_out", "n/a"] = "n/a"
    checkpoint_status: Literal["ok", "failed", "n/a"] = "n/a"
    # "recovered": Phase 8 — a genuinely expired session was auto re-authenticated mid-run, and
    # the step that failed because of it is about to be retried. Not a fault-specific marker: the
    # exact same event a real, un-injected session expiry would produce.
    # "abandoned": a best-effort trailing lookup (transaction_id) failed and the run is finishing
    # as SUCCESS anyway, with that output left unset — the step's own "error" record above this
    # one already shows what actually failed; this just marks that it was allowed to fail.
    result: Literal["ok", "blocked", "error", "recovered", "abandoned", "injected"]
    error: str | None = None
    url_after: str | None = None
    screenshot: str | None = None

    @model_validator(mode="after")
    def _no_secret_values(self) -> Self:
        # The prelude is the only phase that ever handles a secret (typing the credentials);
        # it never sets value at all, so nothing here should either.
        if self.phase == "prelude" and self.action == "type" and self.value is not None:
            raise ValueError("a secret value must never be stored in the replay trace")
        return self


class DiscoveryResult(_Model):
    run_id: str
    stop_reason: StopReason
    detail: str | None = None  # e.g. "blocked_by_policy"
    steps: list[TraceStep] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)  # captured extracts by name
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: float = 0.0
    max_steps: int = 30  # the limits this run was allowed, so a result is honest about its bound
    timeout_s: float = 300.0
    # The same retry contract Replay carries (see SideEffects): "unverified" means discovery
    # dispatched the one submission action (the first button pressed after the tagged amount was
    # typed) and then did not reach SUCCESS afterward — money may have moved and nothing
    # confirmed it; a ticket opens (see DiscoveryRun._result). "committed" means the dispatch
    # happened and SUCCESS is exactly what confirmed it — never retry the whole goal again on a
    # later, unrelated failure. "none" means no dispatch happened at all, safe to just re-run.
    side_effects: SideEffects = "none"
    escalations: list[Escalation] = Field(default_factory=list)

    def summary(self) -> dict:
        data = self.model_dump(mode="json", exclude={"steps"})
        data["steps"] = len(self.steps)
        data["counted_steps"] = sum(1 for s in self.steps if s.counted)
        return data
