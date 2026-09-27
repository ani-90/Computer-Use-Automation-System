"""The discovery loop: a model observes, decides and acts; this code enforces every rule.

Each turn the model sees a screenshot and numbered lists and answers with one tool call. Every
action passes the guard first. The run ends in exactly one of four ways, always recorded.
"""

import contextlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from cua.adapter import Action, ActionFailed, LocatorNotFound, Observation
from cua.checkpoints import StepContext, derive_checkpoint, refutation_message
from cua.enums import StopReason, Verdict
from cua.escalation import open_ticket
from cua.evidence import EvidenceLogger
from cua.goal import GoalSpec, TaggedValues
from cua.guard import ActionGuard
from cua.llm import LLM, LLMError, LLMResponse
from cua.models import Escalation, SideEffects
from cua.policy_gate import PolicyGate
from cua.render import history_line, image_block, render_observation, strip_images, user_content
from cua.tools import ActionError, AgentAction, parse_action, tool_definitions
from cua.trace import DiscoveryResult, GateRecord, TraceStep

Stop = tuple[StopReason, str | None]

_REPORTS = {"report_done", "report_stuck"}
_TOOLS = {"click", "type", "select", "navigate", "extract"} | _REPORTS
_NUDGE = "Reply with exactly one tool call."


@dataclass(frozen=True)
class DiscoveryConfig:
    max_steps: int = 30
    timeout_s: float = 300.0
    dead_end_repeats: int = 3
    max_idle_replies: int = 3  # consecutive replies with no tool call


@dataclass
class _Turn:
    assistant: list[dict]  # the model's blocks, kept verbatim (thinking included)
    results: list[dict] = field(default_factory=list)


def _parts(result: dict, latest: bool) -> tuple[list[dict], list[dict]]:
    """(tool_result blocks, trailing blocks) for one result.

    Old results collapse to one line of text; only the latest carries the screenshot. The API
    accepts only text inside an error result, so there the screenshot follows as its own block.
    """
    full = result.get("full") if latest else None
    text = full if full is not None else result["collapsed"]
    image = result.get("screenshot") if full is not None else None
    if result["id"] is None:  # a plain message, not the answer to a tool call
        return [], user_content(text, image)
    if result["is_error"]:
        head = {
            "type": "tool_result",
            "tool_use_id": result["id"],
            "is_error": True,
            "content": [{"type": "text", "text": text}],
        }
        return [head], [image_block(image)] if image else []
    head = {
        "type": "tool_result",
        "tool_use_id": result["id"],
        "is_error": False,
        "content": user_content(text, image),
    }
    return [head], []


def _user_blocks(results: list[dict], latest: bool) -> list[dict]:
    """All tool results first, then everything else: the order the API requires."""
    parts = [_parts(result, latest) for result in results]
    return [b for head, _ in parts for b in head] + [b for _, tail in parts for b in tail]


def _build_messages(first_text: str, first_image: bytes, turns: list[_Turn]) -> list[dict]:
    messages = [{"role": "user", "content": user_content(first_text, None if turns else first_image)}]
    for i, turn in enumerate(turns):
        messages.append({"role": "assistant", "content": turn.assistant})
        messages.append({"role": "user", "content": _user_blocks(turn.results, i == len(turns) - 1)})
    return messages


class DiscoveryRun:
    def __init__(
        self,
        *,
        spec: GoalSpec,
        params: Mapping[str, str],
        tagged: TaggedValues,
        adapter: Any,
        llm: LLM,
        gate: PolicyGate,
        logger: EvidenceLogger,
        system_prompt: str,
        start_url: str,
        config: DiscoveryConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.spec, self.params, self.tagged = spec, dict(params), tagged
        self.adapter, self.llm, self.logger = adapter, llm, logger
        self.system_prompt, self.start_url = system_prompt, start_url
        self.config = config or DiscoveryConfig()
        self.clock = clock
        self.guard = ActionGuard(gate, self.params, spec.amount_input, tuple(spec.inputs))
        self.tools = tool_definitions(spec.extract_descriptions(self.params))
        self.extract_names = set(spec.extracts)
        self.steps: list[TraceStep] = []
        self.captured: dict[str, str] = {}
        self.turns: list[_Turn] = []
        self.llm_calls = self.tokens_in = self.tokens_out = 0
        self.counted = self.idle = 0
        self.repeat_key: tuple | None = None
        self.repeat_count = self.repeat_policy = 0
        # The step that dispatched the one submission action, if it happened yet — the discovery
        # counterpart of `is_submission`, which does not exist until the compiler writes it. Once
        # set, it never clears: this run has, for real, clicked the button that moves money.
        self.dispatch_step: TraceStep | None = None
        self.dispatch_time: str | None = None  # UTC, for the human verifying the ledger afterward
        self.started = clock()
        self.obs: Observation
        self.first_text = ""
        self.first_image = b""

    # -- the run -----------------------------------------------------------
    def run(self) -> DiscoveryResult:
        try:
            stop, detail = self._loop()
        except BaseException as e:
            # Leave a partial record, then let the crash surface. The "crashed:" prefix is
            # greppable, so a bug in our code never reads as an agent dead end.
            with contextlib.suppress(Exception):  # never hide the original error
                self._write(self._result(StopReason.DEAD_END, f"crashed: {type(e).__name__}"))
            raise
        return self._write(self._result(stop, detail))

    def _result(self, stop: StopReason, detail: str | None) -> DiscoveryResult:
        side_effects: SideEffects = "none"
        escalations: list[Escalation] = []
        if self.dispatch_step is not None:
            if stop == StopReason.SUCCESS:
                # Dispatched and confirmed — SUCCESS is exactly what confirms it. Same meaning as
                # replay's own "committed": never retry the whole thing again on a later, unrelated
                # failure.
                side_effects = "committed"
            else:
                # Dispatched, and this run still did not reach SUCCESS: money may have moved and
                # nothing here confirms it. Opens the same ticket machinery the threshold trigger
                # uses — discovery names no page of any app either, same as replay's own
                # dispatch_unverified ticket.
                side_effects = "unverified"
                escalations = [open_ticket(
                    self.logger,
                    self.logger.run_id,
                    self.dispatch_step.step_no,
                    reason=f"discovery dispatched the submission, then ended {stop} without reaching SUCCESS",
                    procedure=self._verification_procedure(),
                    capability=None,
                    step_description=self.dispatch_step.target.description if self.dispatch_step.target else None,
                    screenshot=self.dispatch_step.screenshot,
                )]
        return DiscoveryResult(
            run_id=self.logger.run_id,
            stop_reason=stop,
            detail=detail,
            steps=self.steps,
            outputs=self.captured,
            llm_calls=self.llm_calls,
            input_tokens=self.tokens_in,
            output_tokens=self.tokens_out,
            elapsed_seconds=self.clock() - self.started,
            max_steps=self.config.max_steps,  # the bounds this run was allowed, on the record
            timeout_s=self.config.timeout_s,
            side_effects=side_effects,
            escalations=escalations,
        )

    def _verification_procedure(self) -> str:
        """Generic on purpose — discovery has no compiled artifact yet to build a page-specific
        checklist from (unlike replay's own version of this). Real values: redacted the same way
        as everything else at the write boundary, so this is only ever seen unredacted where the
        CLI prints it live, exactly like replay's own procedure."""
        amount_input = self.spec.amount_input
        inputs = ", ".join(
            f"{name}={value}" for name, value in self.params.items() if name != amount_input
        )
        amount = self.params.get(amount_input) if amount_input else None
        lines = [
            (
                "A money-moving step was clicked during discovery, and the run did not reach "
                "SUCCESS afterward. The action may or may not have posted. Do not re-run this "
                "goal until it is verified."
            ),
        ]
        if inputs:
            lines.append(f"Inputs: {inputs}.")
        if amount is not None:
            lines.append(f"Amount: {amount}.")
        if self.guard.balance is not None:
            lines.append(f"Balance before the step: {self.guard.balance}.")
        if self.dispatch_time:
            lines.append(f"Dispatched at: {self.dispatch_time} (UTC).")
        lines.append(
            "To verify: check the target account's transaction or activity history for a "
            "transfer matching the amount above, made at or after the dispatch time. If found, "
            "no action needed. If not found, the transfer likely did not complete."
        )
        return "\n".join(lines)

    def _write(self, result: DiscoveryResult) -> DiscoveryResult:
        self.logger.write_json("trace.json", result.model_dump(mode="json"))
        self.logger.write_json("result.json", result.summary())
        return result

    def _loop(self) -> Stop:
        try:
            self.adapter.navigate(self.start_url)
        except ActionFailed as e:
            return StopReason.DEAD_END, f"start page failed: {e}"
        self.obs = self.adapter.observe()
        self.logger.write_bytes("step-00.png", self.obs.masked_screenshot)
        self.first_text = self._first_message()
        self._transcript("user", user_content(self.first_text, self.obs.screenshot))
        while True:
            if self._timed_out():
                return StopReason.TIMEOUT, None
            try:
                response = self._ask()
            except LLMError as e:  # end cleanly so the trace and result are still written
                self.logger.log("llm_error", error=str(e))
                return StopReason.DEAD_END, f"llm_error: {e}"
            if response.stop_reason == "refusal":
                return StopReason.DEAD_END, "model_refused"
            turn = _Turn(assistant=response.content)
            self.turns.append(turn)
            self._transcript("assistant", response.content)
            calls = [b for b in response.content if b.get("type") == "tool_use"]
            if not calls:
                self.idle += 1
                if self.idle >= self.config.max_idle_replies:
                    return StopReason.DEAD_END, "no_tool_call"
                self._answer(turn, None, _NUDGE, f"{_NUDGE}\n\n{render_observation(self.obs)}")
                self._flush(turn)
                continue
            self.idle = 0
            stop = self._handle(calls[0], turn, self._reasoning(response.content))
            for extra in calls[1:]:  # every tool call needs an answer
                message = "Only one tool call is allowed per turn; this one was not run."
                turn.results.append(
                    {"id": extra["id"], "collapsed": message, "is_error": True}
                )
            if stop:
                return stop
            self._flush(turn)

    # -- one tool call -----------------------------------------------------
    def _handle(self, call: dict, turn: _Turn, reasoning: str) -> Stop | None:
        name, args = call["name"], call.get("input") or {}
        counted = name not in _REPORTS
        if counted and self.counted >= self.config.max_steps:
            return StopReason.MAX_STEPS_EXCEEDED, None
        if self._timed_out():
            return StopReason.TIMEOUT, None
        if counted:
            self.counted += 1
        try:
            action = parse_action(name, args, self.obs, self.extract_names)
        except ActionError as e:
            return self._reject(turn, call["id"], name, str(e), counted, reasoning)
        if action.tool == "report_stuck":
            self._record(tool="report_stuck", counted=False, result="ok", reasoning=reasoning)
            return StopReason.DEAD_END, "report_stuck"
        if action.tool == "report_done":
            return self._done(turn, call["id"], action, reasoning)
        return self._act(turn, call["id"], action, reasoning)

    def _done(self, turn: _Turn, call_id: str, action: AgentAction, reasoning: str) -> Stop | None:
        # An input left at the page's default was never bound, so a compiled artifact would
        # not carry it: every declared input must have been typed or selected in a step.
        entered = {
            s.param
            for s in self.steps
            if s.provenance == "parameter" and s.result == "ok" and s.checkpoint_status != "failed"
        }
        problems = self.spec.check_done(self.captured, self.params, entered)
        if not problems:
            self._record(tool="report_done", counted=False, result="ok", reasoning=reasoning)
            return StopReason.SUCCESS, None
        message = "Not done yet: " + "; ".join(problems) + "."
        step = self._record(
            tool="report_done", counted=False, result="error", error=message, reasoning=reasoning
        )
        self._answer(
            turn, call_id, history_line(step), f"{message}\n\n{render_observation(self.obs)}",
            is_error=True,
        )
        # Claims are not steps, so without this an agent that keeps claiming "done" would loop
        # (one paid call each) until the timeout. Any action in between resets the count.
        return self._track(("report_done", message), failed=True, kind="done_rejected")

    def _reject(
        self, turn: _Turn, call_id: str, name: str, message: str, counted: bool, reasoning: str
    ) -> Stop | None:
        step = None
        if name in _TOOLS:
            step = self._record(
                tool=name, counted=counted, result="error", error=message, reasoning=reasoning
            )
        collapsed = history_line(step) if step else f"Invalid call to {name}: {message}"
        self._answer(
            turn, call_id, collapsed, f"{message}\n\n{render_observation(self.obs)}", is_error=True
        )
        return self._track(("invalid", name, message), failed=True, policy=False)

    def _act(self, turn: _Turn, call_id: str, action: AgentAction, reasoning: str) -> Stop | None:
        before = self.obs
        provenance, param = "other", None
        if action.tool in {"type", "select"}:
            provenance, param = self.tagged.classify(action.text or "")
        target = action.element.locator if action.element is not None else None
        fields: dict[str, Any] = {
            "tool": action.tool,
            "ref": action.ref,
            "target": target,
            "value": action.text if provenance != "secret" else None,
            "provenance": provenance,
            "param": param,
            "extract_name": action.name,
            "expect": action.expect,
            "reasoning": reasoning,
        }
        key = self._key(action)

        decision = self.guard.check(action, before.url, provenance, param, before.candidates)
        gate = GateRecord(verdict=decision.verdict, reason=decision.reason)
        if decision.verdict != Verdict.ALLOW:
            step = self._record(**fields, gate=gate, result="blocked", error=decision.reason,
                                url_after=before.url)
            self._answer(
                turn, call_id, history_line(step),
                f"Blocked: {decision.reason}. This action is not permitted for this task; do not repeat it, "
                f"find another way to reach the goal.\n\n{render_observation(before)}", is_error=True,
            )
            return self._track(key, failed=True, policy=True)

        try:
            extracted = self._execute(action)
        except (LocatorNotFound, ActionFailed) as e:
            self.obs = self.adapter.observe()
            step = self._record(**fields, gate=gate, result="error", error=str(e),
                                url_after=self.obs.url)
            # A raised error counts the same as a missing confirmation for the one action that
            # moves money — the request may already be in flight. Only LocatorNotFound proves
            # nothing was clicked; ActionFailed on what would have been the dispatch is treated
            # as a possible dispatch anyway, blocking any retry the same way a real one would.
            if isinstance(e, ActionFailed) and self.guard.is_submission_candidate(action):
                self.guard.mark_possible_dispatch(action)
                self.dispatch_step = step
                self.dispatch_time = datetime.now(UTC).isoformat(timespec="seconds")
            self._answer(
                turn, call_id, history_line(step),
                f"That action failed: {e}\n\n{render_observation(self.obs)}", is_error=True,
            )
            return self._track(key, failed=True, policy=False)

        after = self.adapter.observe()
        landing = self.guard.landing(after.url)
        if landing.verdict != Verdict.ALLOW:  # it went somewhere it must not: go back
            try:
                self.adapter.navigate(before.url)
            except ActionFailed:
                pass
            self.obs = self.adapter.observe()
            step = self._record(
                **fields, gate=GateRecord(verdict=landing.verdict, reason=landing.reason),
                result="blocked", error=landing.reason, url_after=self.obs.url,
            )
            self._answer(
                turn, call_id, history_line(step),
                f"Blocked: that led somewhere not allowed ({landing.reason}); you are back on "
                f"the previous page.\n\n{render_observation(self.obs)}",
                is_error=True,
            )
            return self._track(key, failed=True, policy=True)
        self.obs = after

        is_extract = action.tool == "extract"
        ctx = StepContext(
            tool=action.tool,
            target=target,
            value=extracted if is_extract else action.text,
            provenance=provenance,
            param=param,
            params=self.params,
            expect=action.expect,
            shape=self.spec.extracts[action.name].shape if is_extract else None,
        )
        derived = derive_checkpoint(ctx, before, after)
        # Typing and selecting change no visible text, so only their field checkpoint counts;
        # an expectation is judged (and refutations fed back) for clicks and navigation only.
        judged = action.tool in {"click", "navigate"} and bool(action.expect)
        was_submitted = self.guard.submitted
        self.guard.after(action, before.url, after.url, derived.status != "failed", provenance, param)
        if is_extract:
            fields["value"] = extracted
            if derived.status == "verified":
                text = (extracted or "").strip()
                earlier = self.captured.get(action.name)
                if self.spec.extracts[action.name].combine and earlier:
                    if text not in earlier:  # parts of one value are joined, repeats are not
                        self.captured[action.name] = f"{earlier}\n{text}"
                else:
                    self.captured[action.name] = text
                if action.name == self.spec.balance_extract:
                    self.guard.set_balance(text)

        screenshot = f"step-{len(self.steps) + 1:02d}.png"
        self.logger.write_bytes(screenshot, after.masked_screenshot)
        step = self._record(
            **fields,
            gate=gate,
            expectation=derived.expectation if judged else None,
            checkpoint_status=derived.status,
            checkpoint=derived.conditions,
            result="ok",
            url_after=after.url,
            screenshot=screenshot,
        )
        if not was_submitted and self.guard.submitted:  # this action IS the dispatch, right now
            self.dispatch_step = step
            self.dispatch_time = datetime.now(UTC).isoformat(timespec="seconds")
        notes = []
        if derived.status == "failed":
            notes.append(f"The action did not take effect: {derived.note}.")
        elif derived.status == "unverified":
            notes.append("Nothing observable changed.")
        if judged and derived.expectation == "refuted":
            notes.append(refutation_message(action.expect, after))
        feedback = "\n".join(["Result: ok", *notes])
        self._answer(
            turn, call_id, history_line(step), f"{feedback}\n\n{render_observation(after)}",
            is_error=False,
        )
        return self._track(key, failed=derived.status == "failed", policy=False)

    def _execute(self, action: AgentAction) -> str | None:
        if action.tool == "navigate":
            self.adapter.navigate(urljoin(self.obs.url, action.text or ""))
            return None
        handle = self.adapter.resolve(action.element.locator)
        return self.adapter.act(handle, Action(action.tool, action.text))

    # -- bookkeeping -------------------------------------------------------
    def _track(
        self, key: tuple, failed: bool, policy: bool = False, kind: str | None = None
    ) -> Stop | None:
        if not failed:
            self.repeat_key, self.repeat_count, self.repeat_policy = None, 0, 0
            return None
        if key == self.repeat_key:
            self.repeat_count += 1
            self.repeat_policy += int(policy)
        else:
            self.repeat_key, self.repeat_count, self.repeat_policy = key, 1, int(policy)
        if self.repeat_count >= self.config.dead_end_repeats:
            blocked = self.repeat_policy == self.repeat_count
            return StopReason.DEAD_END, "blocked_by_policy" if blocked else kind
        return None

    @staticmethod
    def _key(action: AgentAction) -> tuple:
        where = action.element.locator.description if action.element else action.text
        return (action.tool, where, action.text or action.name)

    def _timed_out(self) -> bool:
        return self.clock() - self.started >= self.config.timeout_s

    def _record(self, **fields: Any) -> TraceStep:
        step = TraceStep(step_no=len(self.steps) + 1, **fields)
        self.steps.append(step)
        self.logger.log(
            "action",
            step=step.step_no,
            tool=step.tool,
            target=step.target.description if step.target else None,
            gate=step.gate.verdict if step.gate else None,
            result=step.result,
            expectation=step.expectation,
            checkpoint=step.checkpoint_status,
            url=step.url_after,
            error=step.error,
        )
        return step

    def _answer(
        self,
        turn: _Turn,
        call_id: str | None,
        collapsed: str,
        full: str,
        is_error: bool = False,
    ) -> None:
        turn.results.append(
            {
                "id": call_id,
                "collapsed": collapsed,
                "full": full,
                "screenshot": self.obs.screenshot,
                "is_error": is_error,
            }
        )

    def _ask(self) -> LLMResponse:
        messages = _build_messages(self.first_text, self.first_image, self.turns)
        response = self.llm.complete(system=self.system_prompt, messages=messages, tools=self.tools)
        self.llm_calls += 1
        self.tokens_in += response.input_tokens
        self.tokens_out += response.output_tokens
        self.logger.log(
            "llm_call",
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            stop_reason=response.stop_reason,
            request_id=response.request_id,
        )
        return response

    def _first_message(self) -> str:
        self.first_image = self.obs.screenshot
        creds = "; ".join(f"{name}: {value}" for name, value in self.tagged.secrets.items())
        return (
            f"Goal: {self.spec.goal_text(self.params)}\n\n"
            f"Credentials to use: {creds}\n\n{render_observation(self.obs)}"
        )

    def _transcript(self, role: str, content: list[dict]) -> None:
        self.logger.append_jsonl("transcript.jsonl", strip_images({"role": role, "content": content}))

    def _flush(self, turn: _Turn) -> None:
        self._transcript("user", _user_blocks(turn.results, True))

    @staticmethod
    def _reasoning(content: list[dict]) -> str:
        return "\n".join(b["text"] for b in content if b.get("type") == "text")


def run_discovery(**kwargs: Any) -> DiscoveryResult:
    return DiscoveryRun(**kwargs).run()
