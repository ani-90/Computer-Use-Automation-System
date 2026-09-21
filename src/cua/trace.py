"""Trace of a discovery run: one record per step, plus the run result."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cua.enums import StopReason, Verdict
from cua.models import Condition, Locator


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

    def summary(self) -> dict:
        data = self.model_dump(mode="json", exclude={"steps"})
        data["steps"] = len(self.steps)
        data["counted_steps"] = sum(1 for s in self.steps if s.counted)
        return data
