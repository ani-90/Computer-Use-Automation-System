"""The operator-only CUA_FAULT switch: parsed once from the environment when the service starts.

The safety property under test is the one CLAUDE.md demands of all fault injection: nothing a normal
invocation does can ever turn it on. Unset means no fault; a malformed value refuses to start rather
than silently running a different test than the operator asked for.
"""

import pytest

from cua.tool_interface import FAULT_ENV, fault_from_env


def test_unset_or_blank_means_no_fault_exactly_as_before():
    assert fault_from_env({}) is None
    assert fault_from_env({FAULT_ENV: ""}) is None
    assert fault_from_env({FAULT_ENV: "   "}) is None


def test_transient_fail_with_a_delay():
    fault = fault_from_env({FAULT_ENV: "transient_fail:5:**/*transfer*:90000"})
    assert (fault.fault_type, fault.step_index, fault.url_pattern, fault.delay_ms) == (
        "transient_fail", 5, "**/*transfer*", 90000,
    )


def test_transient_fail_without_a_delay_uses_the_models_own_default():
    fault = fault_from_env({FAULT_ENV: "transient_fail:5:**/*transfer*"})
    assert fault.url_pattern == "**/*transfer*"
    assert fault.delay_ms == 3000


def test_a_url_glob_containing_colons_survives():
    fault = fault_from_env({FAULT_ENV: "transient_fail:5:http://h/parabank/*:1500"})
    assert (fault.url_pattern, fault.delay_ms) == ("http://h/parabank/*", 1500)


def test_clear_session():
    fault = fault_from_env({FAULT_ENV: "clear_session:0"})
    assert (fault.fault_type, fault.step_index) == ("clear_session", 0)


@pytest.mark.parametrize(
    "value",
    [
        "drop_response:5",  # not a fault this build has
        "transient_fail",  # no step
        "transient_fail:5",  # no url glob
        "transient_fail:five:**/*x*",  # step is not a number
        "clear_session",  # no step
        "clear_session:0:extra",
        "nonsense",
    ],
)
def test_a_malformed_value_refuses_to_start_instead_of_being_ignored(value):
    with pytest.raises(ValueError, match=FAULT_ENV):
        fault_from_env({FAULT_ENV: value})
