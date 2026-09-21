import pytest

from cua.adapter import Candidate, Observation, TextNode
from cua.models import Locator, LocatorCandidate
from cua.tools import ActionError, parse_action, tool_definitions

NAMES = {"balance": "The balance.", "confirmation": "The confirmation."}


def loc(name: str) -> Locator:
    cand = LocatorCandidate(strategy="text", value=name)
    return Locator(description=name, chain=[cand])


def cand(index: int, role: str, name: str = "x") -> Candidate:
    return Candidate(index, role, name, None, None, loc(name))


def obs() -> Observation:
    candidates = [cand(1, "button"), cand(2, "textbox"), cand(3, "combobox"), cand(4, "link")]
    texts = [
        TextNode(5, "heading", "Title", loc("Title")),
        TextNode(6, "text", "A label", None),  # context only
    ]
    return Observation("http://h/p/a.htm", b"", b"", candidates, texts)


def test_every_tool_schema_is_strict_with_all_properties_required():
    tools = tool_definitions(NAMES)
    assert {t["name"] for t in tools} == {
        "click", "type", "select", "navigate", "extract", "report_done", "report_stuck",
    }
    for tool in tools:
        schema = tool["input_schema"]
        assert tool["strict"] is True and schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])


def test_extract_names_come_from_the_spec():
    extract = next(t for t in tool_definitions(NAMES) if t["name"] == "extract")
    assert extract["input_schema"]["properties"]["name"]["enum"] == list(NAMES)
    assert "The balance." in extract["description"]


def test_actions_parse_and_resolve_their_element():
    click = parse_action("click", {"ref": 1, "expect": "Done"}, obs(), NAMES)
    assert (click.tool, click.element.index, click.expect) == ("click", 1, "Done")
    typed = parse_action("type", {"ref": 2, "text": "hi", "expect": "x"}, obs(), NAMES)
    assert typed.text == "hi"
    picked = parse_action("select", {"ref": 3, "value": "opt", "expect": "x"}, obs(), NAMES)
    assert picked.text == "opt"
    read = parse_action("extract", {"ref": 5, "name": "balance"}, obs(), NAMES)
    assert (read.name, read.element.index) == ("balance", 5)
    nav = parse_action("navigate", {"url": "/p/b.htm", "expect": "B"}, obs(), NAMES)
    assert nav.text == "/p/b.htm"
    done = parse_action("report_done", {"reasoning": "all recorded"}, obs(), NAMES)
    assert done.reasoning == "all recorded"


@pytest.mark.parametrize(
    ("tool", "args", "fragment"),
    [
        ("click", {"ref": 99, "expect": "x"}, "no element [99]"),
        ("click", {"ref": 5, "expect": "x"}, "use extract to read it"),  # text, not clickable
        ("click", {"ref": 2, "expect": "x"}, "use type or select"),
        ("type", {"ref": 1, "text": "a", "expect": "x"}, "not a text field"),
        ("select", {"ref": 1, "value": "a", "expect": "x"}, "not a dropdown"),
        ("extract", {"ref": 6, "name": "balance"}, "context only"),
        ("extract", {"ref": 5, "name": "nope"}, "not one of the names"),
        ("click", {"ref": "1", "expect": "x"}, "ref must be"),
        ("click", {"ref": True, "expect": "x"}, "ref must be"),
        ("click", {"ref": 1}, "expect must be"),
        ("teleport", {}, "no tool called"),
    ],
)
def test_bad_calls_get_a_clear_error(tool, args, fragment):
    with pytest.raises(ActionError) as excinfo:
        parse_action(tool, args, obs(), NAMES)
    assert fragment in str(excinfo.value)
