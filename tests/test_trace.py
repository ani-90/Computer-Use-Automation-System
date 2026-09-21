import pytest
from pydantic import ValidationError

from cua.enums import StopReason
from cua.trace import DiscoveryResult, TraceStep


def test_secret_step_cannot_carry_a_value():
    with pytest.raises(ValidationError):
        TraceStep(step_no=1, tool="type", provenance="secret", value="hunter2", result="ok")
    TraceStep(step_no=1, tool="type", provenance="secret", result="ok")


def test_result_round_trips_and_summarizes():
    steps = [
        TraceStep(step_no=1, tool="click", result="ok"),
        TraceStep(step_no=2, tool="report_done", counted=False, result="ok"),
    ]
    result = DiscoveryResult(
        run_id="run-1", stop_reason=StopReason.SUCCESS, steps=steps, outputs={"a": "b"}, llm_calls=3
    )
    assert DiscoveryResult.model_validate_json(result.model_dump_json()) == result
    summary = result.summary()
    assert (summary["steps"], summary["counted_steps"]) == (2, 1)
    assert (summary["stop_reason"], summary["llm_calls"]) == ("SUCCESS", 3)
