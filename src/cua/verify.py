"""Evaluate an already-written Condition against the live page, with no LLM and no judgment.

checkpoints.py only ever derives a NEW condition from a before/after diff, during Discovery.
Replay needs the opposite: given a condition someone already wrote down, does it hold right now?
This is deliberately dumb — every {{param}} placeholder is rendered to its real value first, then
each condition kind maps to one direct check, using the same adapter methods and the same
Observation shape Discovery itself relies on.
"""

import re
from collections.abc import Mapping
from urllib.parse import urlparse

from cua.adapter import Observation
from cua.models import SHAPES, Condition, Locator, LocatorCandidate


def render(text: str, params: Mapping[str, str]) -> str:
    for name, value in params.items():
        text = text.replace("{{" + name + "}}", value)
    return text


def render_locator(locator: Locator, params: Mapping[str, str]) -> Locator:
    return Locator(
        description=render(locator.description, params),
        chain=[
            LocatorCandidate(
                strategy=c.strategy,
                value=render(c.value, params) if c.value else c.value,
                role=c.role,
                nth=c.nth,
                column=render(c.column, params) if c.column else c.column,
                col=c.col,
            )
            for c in locator.chain
        ],
    )


def render_condition(cond: Condition, params: Mapping[str, str]) -> Condition:
    return Condition(
        kind=cond.kind,
        target=render_locator(cond.target, params) if cond.target else None,
        value=render(cond.value, params) if cond.value else cond.value,
    )


def resolves(adapter, locator: Locator) -> bool:
    # adapter.exists(), not adapter.resolve(): an existence check must not fail just because more
    # than one match exists — resolve()'s exactly-one rule is for picking a single action target,
    # a different question from "is this on the page".
    return adapter.exists(locator)


def _visible_text(obs: Observation) -> str:
    parts = [t.text for t in obs.texts] + [c.name or c.hint or "" for c in obs.candidates]
    return " | ".join(" ".join(p.lower().split()) for p in parts)


def _candidate(obs: Observation, target: Locator):
    return next((c for c in obs.candidates if c.locator == target), None)


def evaluate(cond: Condition, adapter, obs: Observation, params: Mapping[str, str]) -> bool:
    """True when `cond` (a precondition or checkpoint condition from a compiled Step) holds
    against `obs`, the most recent observation. Shape checks on an extract's own value are
    handled separately by the caller (see ReplayEngine): a Condition alone cannot see a value
    that was read and discarded, only what is currently on the page."""
    c = render_condition(cond, params)
    if c.kind == "url_matches":
        return urlparse(obs.url).path == c.value
    if c.kind == "element_visible":
        return resolves(adapter, c.target)
    if c.kind == "element_absent":
        return not resolves(adapter, c.target)
    cand = _candidate(obs, c.target)
    if c.kind == "field_filled":
        return cand is not None and (cand.filled or cand.selected is not None)
    if c.kind == "field_value_equals":
        return cand is not None and cand.value == c.value
    if c.kind == "option_selected":
        return cand is not None and cand.selected == c.value
    if c.kind == "option_present":
        return cand is not None and c.value in cand.options
    if c.kind == "text_present":
        return " ".join(c.value.lower().split()) in _visible_text(obs)
    if c.kind == "text_equals":
        return cand is not None and cand.value == c.value
    if c.kind == "shape_matches":
        # A precondition carried forward from an earlier extract's own checkpoint: re-read
        # whatever is currently shown at that target (a table cell or a paragraph) and check
        # it still matches the shape, rather than trusting a value that may since be stale.
        node = next((t for t in obs.texts if t.locator == c.target), None)
        value = node.text if node is not None else (cand.value if cand is not None else None)
        return evaluate_shape(value, c.value)
    return False


def evaluate_shape(value: str | None, shape: str) -> bool:
    return bool(re.fullmatch(SHAPES[shape], (value or "").strip(), flags=re.DOTALL))
