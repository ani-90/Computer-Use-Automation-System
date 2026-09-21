"""Checkpoint derivation: diff two observations, judge the agent's expectation, derive conditions.

Pure logic, no browser. It works on visible content only: the accessibility snapshot excludes
hidden nodes, so a result panel that exists in the page but is hidden counts as absent until
it is shown.
"""

import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

from cua.adapter import Observation
from cua.models import Condition, Locator

SHAPES = {"nonempty": r".+", "money": r"-?\$-?\d+(?:\.\d+)?"}
_VOLATILE = re.compile(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{1,2}:\d{2}")  # dates, times
_ERROR_WORDS = ("error", "failed", "invalid", "not found", "denied")
_STATIC = {"heading", "paragraph", "text"}  # table cells are data, never structure
_RANK = {"heading": 0, "paragraph": 1, "text": 1}  # interactive elements rank last
_MAX_APPEARED = 3
_MAX_DISAPPEARED = 2

Expectation = Literal["confirmed", "weak", "refuted"]


@dataclass(frozen=True)
class Element:
    kind: str
    text: str
    locator: Locator | None


@dataclass(frozen=True)
class Delta:
    url_changed: bool
    path_after: str
    appeared: list[Element] = field(default_factory=list)
    disappeared: list[Element] = field(default_factory=list)
    options_gained: list[tuple[Locator, list[str]]] = field(default_factory=list)


@dataclass(frozen=True)
class StepContext:
    tool: Literal["click", "type", "select", "navigate", "extract"]
    target: Locator | None  # None for navigate
    value: str | None = None  # typed/selected value, or the text an extract read
    provenance: Literal["parameter", "secret", "other"] = "other"
    param: str | None = None  # placeholder name for a parameter value
    params: dict[str, str] = field(default_factory=dict)  # tagged values: name -> value
    expect: str | None = None
    shape: str | None = None  # declared shape for an extract


@dataclass(frozen=True)
class Derived:
    status: Literal["verified", "unverified", "failed"]
    conditions: list[Condition]
    expectation: Expectation
    delta: Delta
    note: str = ""


def _path(url: str) -> str:
    return urlparse(url).path


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _key(locator: Locator) -> str:
    return locator.model_dump_json()


def _cond(kind: str, target: Locator | None = None, value: str | None = None) -> Condition:
    return Condition(kind=kind, target=target, value=value)


def _elements(obs: Observation) -> dict[tuple[str, str], Element]:
    found: dict[tuple[str, str], Element] = {}
    for c in obs.candidates:
        text = c.name or c.hint or ""
        if text:
            found[(c.role, text)] = Element(c.role, text, c.locator)
    for t in obs.texts:
        if t.kind in _STATIC and not _VOLATILE.search(t.text):
            found[(t.kind, t.text)] = Element(t.kind, t.text, t.locator)
    return found


def _visible_text(obs: Observation) -> str:
    parts = [t.text for t in obs.texts]
    parts += [c.name or c.hint or "" for c in obs.candidates]
    parts.append(_path(obs.url))
    return " | ".join(_norm(p) for p in parts)


def diff(before: Observation, after: Observation) -> Delta:
    was, now = _elements(before), _elements(after)
    rank = lambda e: _RANK.get(e.kind, 2)
    appeared = sorted((e for k, e in now.items() if k not in was), key=rank)
    disappeared = sorted((e for k, e in was.items() if k not in now), key=rank)
    earlier = {_key(c.locator): set(c.options) for c in before.candidates if c.role == "combobox"}
    gained: list[tuple[Locator, list[str]]] = []
    for c in after.candidates:
        if c.role == "combobox" and _key(c.locator) in earlier:
            new = sorted(set(c.options) - earlier[_key(c.locator)])
            if new:
                gained.append((c.locator, new))
    return Delta(_path(before.url) != _path(after.url), _path(after.url), appeared, disappeared, gained)


def judge_expectation(expect: str | None, before: Observation, after: Observation) -> Expectation:
    needle = _norm(expect or "")
    if not needle:
        return "weak"
    if needle not in _visible_text(after):
        return "refuted"
    return "weak" if needle in _visible_text(before) else "confirmed"


def refutation_message(expect: str, after: Observation) -> str:
    headings = [t.text for t in after.texts if t.kind == "heading"][-2:]
    errors = [t.text for t in after.texts if any(w in t.text.lower() for w in _ERROR_WORDS)][:2]
    parts = [f'you expected "{expect}"; the page now shows: {_path(after.url)}']
    if headings:
        parts.append("headings: " + "; ".join(headings))
    if errors:
        parts.append("visible errors: " + "; ".join(errors))
    return " | ".join(parts)


def _placeholder(ctx: StepContext) -> str | None:
    if ctx.provenance == "parameter" and ctx.param:
        return "{{" + ctx.param + "}}"
    return ctx.value


def _field_conditions(ctx: StepContext, after: Observation) -> tuple[list[Condition], str | None]:
    cand = next((c for c in after.candidates if c.locator == ctx.target), None)
    if cand is None:
        return [], "the field is not on the page after the action"
    if ctx.provenance == "secret":  # the value is never recorded, only that the field is filled
        if not (cand.filled or cand.selected is not None):
            return [], "the field is empty after the action"
        return [_cond("field_filled", ctx.target)], None
    if ctx.tool == "select":
        if cand.selected != ctx.value:  # compared with the option's visible label
            return [], "the option was not selected"
        return [_cond("option_selected", ctx.target, _placeholder(ctx))], None
    if cand.value != ctx.value:
        return [], "the field does not hold the typed value"
    return [_cond("field_value_equals", ctx.target, _placeholder(ctx))], None


def _extract_conditions(ctx: StepContext) -> tuple[list[Condition], str | None]:
    shape = ctx.shape or "nonempty"
    if not re.fullmatch(SHAPES[shape], (ctx.value or "").strip(), flags=re.DOTALL):
        return [], f"the extracted value does not match the {shape} shape"
    return [_cond("shape_matches", ctx.target, shape)], None


def _page_conditions(
    ctx: StepContext, delta: Delta, after: Observation, expectation: Expectation
) -> list[Condition]:
    conds: list[Condition] = []
    if ctx.tool == "navigate" or delta.url_changed:
        conds.append(_cond("url_matches", value=delta.path_after))
    for el in [e for e in delta.appeared if e.locator][:_MAX_APPEARED]:
        conds.append(_cond("element_visible", el.locator))
    if not delta.url_changed and ctx.tool != "navigate":  # after a page change, "gone" is noise
        for el in [e for e in delta.disappeared if e.locator][:_MAX_DISAPPEARED]:
            conds.append(_cond("element_absent", el.locator))
    tagged = {value: name for name, value in ctx.params.items()}
    for locator, options in delta.options_gained:
        for option in options:
            if option in tagged:
                conds.append(_cond("option_present", locator, "{{" + tagged[option] + "}}"))
    if expectation == "confirmed":
        needle = _norm(ctx.expect or "")
        node = next(
            (e for e in _elements(after).values() if e.locator and needle in _norm(e.text)), None
        )
        if node is not None:
            cond = _cond("element_visible", node.locator)
            if cond not in conds:
                conds.append(cond)
    return conds


def derive_checkpoint(ctx: StepContext, before: Observation, after: Observation) -> Derived:
    delta = diff(before, after)
    expectation = judge_expectation(ctx.expect, before, after)
    failure: str | None = None
    if ctx.tool in {"type", "select"}:
        conditions, failure = _field_conditions(ctx, after)
    elif ctx.tool == "extract":
        conditions, failure = _extract_conditions(ctx)
    else:
        conditions = _page_conditions(ctx, delta, after, expectation)
    if failure:
        return Derived("failed", [], expectation, delta, failure)
    if not conditions:
        return Derived("unverified", [], expectation, delta, "no observable change")
    return Derived("verified", conditions, expectation, delta)
