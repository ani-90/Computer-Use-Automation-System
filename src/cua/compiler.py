"""Artifact Compiler: turns a successful discovery trace into a versioned Capability.

Operates on the in-memory DiscoveryResult, never on a saved evidence/trace.json — that file is
redacted before it hits disk, so the real tagged values (account numbers, amount) only ever exist
in memory. A frozen trace is an audit record, not a recompilable input; compiler iteration during
development runs against synthetic fixture traces (see tests/test_compiler.py), and each live
compile costs one discovery run.

Login is not compiled as steps. It is identical for every capability against this app and belongs
to the session layer, not the business flow: in production it would itself be a discovered,
per-app session capability; here it is a fixed, gate-covered, checkpoint-verified prelude run by
the Replay Engine before any artifact step (Phase 5). The prelude is detected structurally (a run
of secret-provenance type actions, followed by the click that submits them) so this holds for any
number of secret fields, not just the two ParaBank happens to have.
"""

import re
from typing import Literal

from cua.enums import StopReason
from cua.goal import GoalSpec
from cua.models import (
    Capability,
    Condition,
    Locator,
    LocatorCandidate,
    ParamSpec,
    Step,
    WaitStrategy,
)
from cua.trace import DiscoveryResult, TraceStep

_ACTION_TOOLS = {"click", "type", "select", "navigate", "extract"}
_FIELD_TOOLS = {"type", "select"}


class CompileError(ValueError):
    """The trace cannot be compiled — e.g. it did not end in SUCCESS."""


def compile_capability(result: DiscoveryResult, spec: GoalSpec) -> Capability:
    if result.stop_reason != StopReason.SUCCESS:
        raise CompileError(f"only a successful run can be compiled, got {result.stop_reason}")

    ok_steps = [s for s in result.steps if s.result == "ok" and s.tool in _ACTION_TOOLS]
    prelude_end = _prelude_length(ok_steps)
    last_extract_at = _last_extract_index(ok_steps)
    literal_to_placeholder = _tag_map(ok_steps)

    amount_placeholder = f"{{{{{spec.amount_input}}}}}"
    steps: list[Step] = []
    prev_checkpoint: list[Condition] = []
    since_nav: list[Condition] = []
    prev_url: str | None = None
    submission_marked = False

    for i, ts in enumerate(ok_steps):
        emit = i >= prelude_end and not (
            ts.tool == "extract" and last_extract_at[ts.extract_name] != i
        )
        if emit:
            precondition = _dedupe(prev_checkpoint + since_nav)
            # The first click/navigate whose precondition already asserts the amount is set is
            # the one action that actually moves money — the same "first submit" idea Discovery's
            # own guard already uses, so escalation (Phase 7) gates only that one step, not every
            # click that happens to follow the amount being typed (e.g. a later, read-only search
            # reusing the same value).
            is_submission = (
                not submission_marked
                and ts.tool in {"click", "navigate"}
                and any(c.kind == "field_value_equals" and c.value == amount_placeholder for c in precondition)
            )
            if is_submission:
                submission_marked = True
            steps.append(
                _to_step(ts, precondition, literal_to_placeholder, spec.balance_extract, is_submission)
            )

        if ts.tool in _FIELD_TOOLS:
            since_nav = _dedupe(since_nav + ts.checkpoint)
        # A click/navigate invalidates prior field-state assertions even with no URL change: an
        # in-page AJAX submission (like this app's own Transfer button) can hide the very fields
        # since_nav was tracking, so a later step's precondition must not assert they still hold.
        if ts.tool in {"click", "navigate"} or (prev_url is not None and ts.url_after != prev_url):
            since_nav = []
        prev_checkpoint = ts.checkpoint
        prev_url = ts.url_after

    return Capability(
        schema_version="1.0",
        version="1",
        name=spec.name,
        inputs=spec.inputs,
        outputs={
            n: ParamSpec(type="string", required=x.required)
            for n, x in spec.extracts.items()
            if x.purpose == "output"
        },
        amount_input=spec.amount_input,
        distinct_inputs=spec.distinct_inputs,
        steps=steps,
    )


def _prelude_length(steps: list[TraceStep]) -> int:
    """How many leading steps are the login prelude: secret types, then their submit click."""
    i = 0
    saw_secret = False
    while i < len(steps):
        s = steps[i]
        if s.provenance == "secret":
            saw_secret = True
            i += 1
            continue
        if saw_secret and s.tool == "click":
            return i + 1
        break
    return i


def _last_extract_index(steps: list[TraceStep]) -> dict[str | None, int]:
    """extract_name -> the index of its LAST occurrence (dedup is not just consecutive)."""
    last: dict[str | None, int] = {}
    for i, s in enumerate(steps):
        if s.tool == "extract":
            last[s.extract_name] = i
    return last


def _tag_map(steps: list[TraceStep]) -> dict[str, str]:
    """Real value -> {{param}}, longest values first so a substring never wins first.

    The same parameter can be typed in more than one literal form across a run (e.g. "12" on
    one page, "12.00" on another — both are the same amount, a Decimal comparison treats them
    as equal). Keeping every variant is dangerous: a longer variant like "12.00" can exactly
    match ParaBank's own "$12.00" formatting elsewhere and swallow its ".00" along with the
    digits, even though that ".00" was never part of what was typed. Only the shortest variant
    per parameter is kept — never at risk of over-matching a coincidental longer occurrence.
    """
    by_param: dict[str, str] = {}
    for s in steps:
        if s.provenance == "parameter" and s.value and s.param:
            current = by_param.get(s.param)
            if current is None or len(s.value) < len(current):
                by_param[s.param] = s.value
    pairs = {v: p for p, v in by_param.items()}
    return dict(sorted(((v, f"{{{{{p}}}}}") for v, p in pairs.items()), key=lambda kv: -len(kv[0])))


def _substitute(text: str, tag_map: dict[str, str]) -> str:
    for literal, placeholder in tag_map.items():
        text = re.sub(r"\b" + re.escape(literal) + r"\b", placeholder, text)
    return text


def _param_locator(locator: Locator, tag_map: dict[str, str]) -> Locator:
    return Locator(
        description=_substitute(locator.description, tag_map),
        chain=[
            LocatorCandidate(
                strategy=c.strategy,
                value=_substitute(c.value, tag_map) if c.value else c.value,
                role=c.role,
                nth=c.nth,
                column=_substitute(c.column, tag_map) if c.column else c.column,
                col=c.col,
            )
            for c in locator.chain
        ],
    )


def _param_condition(cond: Condition, tag_map: dict[str, str]) -> Condition:
    return Condition(
        kind=cond.kind,
        target=_param_locator(cond.target, tag_map) if cond.target else None,
        value=_substitute(cond.value, tag_map) if cond.value else cond.value,
    )


def _dedupe(conditions: list[Condition]) -> list[Condition]:
    seen: set[str] = set()
    out: list[Condition] = []
    for c in conditions:
        key = c.model_dump_json()
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _wait_strategy(ts: TraceStep, target: Locator) -> WaitStrategy:
    if ts.tool == "select":
        cond = next((c for c in ts.checkpoint if c.kind == "option_selected"), None)
        if cond is not None:
            return WaitStrategy(kind="option_present", target=target, value=cond.value)
        return WaitStrategy(kind="element_visible", target=target)
    if ts.tool in {"type", "extract"}:
        return WaitStrategy(kind="element_visible", target=target)
    # click / navigate: mirror the primary verified condition, whichever kind it is. This is
    # also what makes an in-page AJAX swap (no URL change) fall out naturally as element_visible,
    # with no special case for it.
    primary = next((c for c in ts.checkpoint if c.kind == "url_matches"), None)
    if primary is not None:
        return WaitStrategy(kind="url_change", value=primary.value)
    primary = next((c for c in ts.checkpoint if c.kind == "element_visible"), None)
    if primary is not None:
        return WaitStrategy(kind="element_visible", target=primary.target)
    return WaitStrategy(kind="network_idle")


def _parameters(ts: TraceStep) -> dict[str, str]:
    if ts.provenance == "parameter" and ts.param:
        return {"value": f"{{{{{ts.param}}}}}"}
    return {}


_NAMED_OUTPUTS = {"confirmation_text", "new_balance", "transaction_id"}


def _extract_as(ts: TraceStep, balance_extract: str) -> Literal[
    "confirmation_text", "new_balance", "transaction_id", "policy_balance"
] | None:
    if ts.tool != "extract":
        return None
    if ts.extract_name == balance_extract:
        return "policy_balance"
    if ts.extract_name in _NAMED_OUTPUTS:
        return ts.extract_name  # type: ignore[return-value]
    return None  # an extract whose value this capability never keeps


def _to_step(
    ts: TraceStep, precondition: list[Condition], tag_map: dict[str, str], balance_extract: str,
    is_submission: bool,
) -> Step:
    action: Literal["click", "type", "select", "navigate", "extract"] = ts.tool  # type: ignore[assignment]
    target = _param_locator(ts.target, tag_map) if ts.target else _no_target_error(ts)
    checkpoint = [_param_condition(c, tag_map) for c in ts.checkpoint]
    return Step(
        precondition=[_param_condition(c, tag_map) for c in precondition],
        action=action,
        target=target,
        parameters=_parameters(ts),
        wait_strategy=_wait_strategy(ts, target),
        checkpoint=checkpoint,
        error_mapping=[],  # real content comes from the Phase 6 probes
        extract_as=_extract_as(ts, balance_extract),
        is_submission=is_submission,
    )


def _no_target_error(ts: TraceStep) -> Locator:
    # A `navigate` step acts on a URL, not an element, so the trace records no locator for it.
    # Step.target is required by the schema, so a recorded `navigate` cannot compile today —
    # a known, narrow gap (this recording never used it; the agent always clicked a link
    # instead), left for whenever a real trace actually needs it, rather than invented now.
    raise CompileError(f"step {ts.step_no} ({ts.tool}) has no target and cannot be compiled")
