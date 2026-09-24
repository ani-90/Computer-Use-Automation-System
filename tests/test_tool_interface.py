"""tool_interface.py: the pure, no-browser parts of the agent-facing capability catalog.

invoke_capability()'s own happy path (a real replay) is exercised live, not here — these tests
cover what's testable without ParaBank or Playwright: the catalog scan, the schema translation,
and the one error path that never touches the browser at all.
"""

import pytest
from pydantic import ValidationError

from cua.models import Capability, LocatorCandidate, ParamSpec, Step, WaitStrategy
from cua.tool_interface import (
    CapabilityNotFound,
    invoke_capability,
    list_capabilities,
    to_tool_schema,
)


def _target():
    from cua.models import Locator

    return Locator(description='button "Go"', chain=[LocatorCandidate(strategy="role_name", role="button", value="Go")])


def _capability(name: str = "transfer_funds") -> Capability:
    step = Step(
        precondition=[], action="click", target=_target(), parameters={},
        wait_strategy=WaitStrategy(kind="network_idle"), checkpoint=[], error_mapping=[],
    )
    return Capability(
        schema_version="1.0", version="1", name=name,
        inputs={
            "from_account": ParamSpec(type="string", required=True),
            "to_account": ParamSpec(type="string", required=True),
            "amount": ParamSpec(type="decimal", required=True),
        },
        outputs={"confirmation_text": ParamSpec(type="string", required=True)},
        amount_input="amount", steps=[step],
    )


def test_to_tool_schema_translates_inputs_into_the_anthropic_tool_shape():
    schema = to_tool_schema(_capability())
    assert schema["name"] == "transfer_funds"
    assert schema["input_schema"]["type"] == "object"
    assert schema["input_schema"]["properties"] == {
        "from_account": {"type": "string"},
        "to_account": {"type": "string"},
        "amount": {"type": "string"},  # decimal -> string: replay() takes Mapping[str, str]
    }
    assert set(schema["input_schema"]["required"]) == {"from_account", "to_account", "amount"}


def test_to_tool_schema_only_lists_required_inputs_as_required():
    cap = _capability()
    cap = cap.model_copy(update={
        "inputs": {**cap.inputs, "note": ParamSpec(type="string", required=False)},
    })
    schema = to_tool_schema(cap)
    assert "note" in schema["input_schema"]["properties"]
    assert "note" not in schema["input_schema"]["required"]


def test_list_capabilities_finds_every_json_file_in_the_given_directory(tmp_path):
    (tmp_path / "transfer_funds.json").write_text(_capability("transfer_funds").model_dump_json(), encoding="utf-8")
    (tmp_path / "bill_pay.json").write_text(_capability("bill_pay").model_dump_json(), encoding="utf-8")
    (tmp_path / "not_a_capability.txt").write_text("ignored", encoding="utf-8")

    caps = list_capabilities(capabilities_dir=tmp_path)

    assert sorted(c.name for c in caps) == ["bill_pay", "transfer_funds"]


def test_list_capabilities_on_an_empty_directory_returns_nothing(tmp_path):
    assert list_capabilities(capabilities_dir=tmp_path) == []


def test_a_malformed_capability_file_fails_loudly_not_silently(tmp_path):
    (tmp_path / "broken.json").write_text('{"not": "a valid capability"}', encoding="utf-8")
    with pytest.raises(ValidationError):
        list_capabilities(capabilities_dir=tmp_path)


def test_invoke_capability_raises_for_an_unknown_name_before_touching_anything(tmp_path):
    # No capability file, no goal file, no browser — CapabilityNotFound must be the very first
    # thing checked, before any of that machinery is even reached.
    with pytest.raises(CapabilityNotFound):
        invoke_capability("nonexistent", {}, capabilities_dir=tmp_path, goals_dir=tmp_path)
