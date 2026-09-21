"""The agent's tool vocabulary: the schemas sent to the model and parsing of what it sends back."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from cua.adapter import Candidate, Observation, TextNode

_TYPEABLE = {"textbox", "searchbox", "spinbutton"}
_CLICKABLE = {"button", "link", "checkbox", "radio"}
_EXPECT = {
    "type": "string",
    "description": (
        "A short phrase, written exactly as it will appear on the page, "
        "that should newly appear after this action."
    ),
}


class ActionError(Exception):
    """The model's tool call cannot be run. The message goes back to the model."""


@dataclass(frozen=True)
class AgentAction:
    tool: str
    ref: int | None = None
    text: str | None = None  # type: the text; select: the option; navigate: the address
    name: str | None = None  # extract: the name to record under
    expect: str | None = None
    reasoning: str | None = None
    element: Candidate | TextNode | None = None  # resolved from ref


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def tool_definitions(extracts: Mapping[str, str]) -> list[dict]:
    """`extracts` maps each allowed extract name to its meaning."""
    names = " ".join(f"{name}: {meaning}" for name, meaning in extracts.items())
    ref = {"type": "integer", "description": "The element's number on the current page."}
    why = {"type": "string", "description": "Why, in one or two sentences."}
    return [
        _tool("click", "Click an element.", {"ref": ref, "expect": _EXPECT}),
        _tool(
            "type",
            "Type into a text field, replacing its content.",
            {"ref": ref, "text": {"type": "string"}, "expect": _EXPECT},
        ),
        _tool(
            "select",
            "Choose a dropdown option by its visible text.",
            {"ref": ref, "value": {"type": "string"}, "expect": _EXPECT},
        ),
        _tool("navigate", "Go to an address.", {"url": {"type": "string"}, "expect": _EXPECT}),
        _tool(
            "extract",
            f"Read an element's text and record it under a name. Names: {names}",
            {"ref": ref, "name": {"type": "string", "enum": list(extracts)}},
        ),
        _tool(
            "report_done",
            "The goal is fully achieved and every requested result is recorded.",
            {"reasoning": why},
        ),
        _tool("report_stuck", "No further progress is possible.", {"reasoning": why}),
    ]


def _arg(args: Mapping, key: str, kind: type):
    value = args.get(key)
    if not isinstance(value, kind) or isinstance(value, bool):
        raise ActionError(f"{key} must be a {kind.__name__}")
    return value


def parse_action(
    tool: str, args: Mapping, obs: Observation, extract_names: Collection[str]
) -> AgentAction:
    if tool in {"report_done", "report_stuck"}:
        return AgentAction(tool, reasoning=_arg(args, "reasoning", str))
    if tool == "navigate":
        return AgentAction(tool, text=_arg(args, "url", str), expect=_arg(args, "expect", str))
    if tool not in {"click", "type", "select", "extract"}:
        raise ActionError(f"there is no tool called {tool!r}")

    ref = _arg(args, "ref", int)
    elements: dict[int, Candidate | TextNode] = {c.index: c for c in obs.candidates}
    elements |= {t.index: t for t in obs.texts}
    element = elements.get(ref)
    if element is None:
        raise ActionError(f"there is no element [{ref}] on the current page")

    if tool == "extract":
        name = _arg(args, "name", str)
        if name not in extract_names:
            raise ActionError(f"{name!r} is not one of the names you can record")
        if isinstance(element, TextNode) and element.locator is None:
            raise ActionError(f"[{ref}] is context only and cannot be read; pick a nearby element")
        return AgentAction(tool, ref=ref, name=name, element=element)

    if not isinstance(element, Candidate):
        raise ActionError(f"[{ref}] is text, not something you can {tool}; use extract to read it")
    if tool == "type" and element.role not in _TYPEABLE:
        raise ActionError(f"[{ref}] is a {element.role}, not a text field")
    if tool == "select" and element.role != "combobox":
        raise ActionError(f"[{ref}] is a {element.role}, not a dropdown")
    if tool == "click" and element.role not in _CLICKABLE:
        raise ActionError(f"[{ref}] is a {element.role}; use type or select for fields")
    text = _arg(args, "text" if tool == "type" else "value", str) if tool != "click" else None
    return AgentAction(
        tool, ref=ref, text=text, expect=_arg(args, "expect", str), element=element
    )
