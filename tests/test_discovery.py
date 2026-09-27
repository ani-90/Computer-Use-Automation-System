"""The discovery loop against a scripted fake site and a scripted fake model.

Both fakes exist only in this file. They stand in for the browser adapter and the language model
so the loop's own rules can be checked deterministically. Nothing here reaches a real agent.
"""

import copy
import itertools
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from cua.adapter import ActionFailed, Candidate, LocatorNotFound, Observation, TextNode
from cua.config import Config
from cua.discovery import DiscoveryConfig, run_discovery
from cua.enums import StopReason
from cua.escalation import open_ticket
from cua.evidence import EvidenceLogger, new_run_id
from cua.goal import GoalSpec, TaggedValues
from cua.llm import LLMError, LLMResponse
from cua.models import Locator, LocatorCandidate
from cua.policy_gate import PolicyGate
from cua.redaction import Redactor
from cua.tools import tool_definitions

ROOT = Path(__file__).resolve().parent.parent
SPEC = GoalSpec.model_validate_json((ROOT / "goals" / "transfer_funds.json").read_text("utf-8"))
SECRETS = {"username": "svc-user", "password": "svc-pass"}
PARAMS = {"from_account": "acct-a", "to_account": "acct-b", "amount": "5"}
LOGIN, OVERVIEW, TRANSFER = (f"/parabank/{p}.htm" for p in ("index", "overview", "transfer"))


def loc(description: str) -> Locator:
    return Locator(
        description=description, chain=[LocatorCandidate(strategy="text", value=description)]
    )


class Handle:
    def __init__(self, description: str):
        self.description = description


class FakeSite:
    """A tiny scripted site standing in for the browser adapter."""

    def __init__(self, balance: int = 100, path: str = LOGIN):
        self.path = path
        self.balance = balance
        self.fields: dict[str, str] = {}
        self.selected = {"From": "acct-a", "To": "acct-a"}
        self.transferred = False
        self.login_failed = False
        self.transfer_clicks = 0

    @property
    def url(self) -> str:
        return "http://h" + self.path

    def navigate(self, target: str) -> None:
        self.path = urlparse(target).path

    def _page(self) -> tuple[list[tuple], list[tuple]]:
        """(candidates, texts) for the current page: (role, name, hint, extra) and (kind, text, desc)."""
        if self.path == LOGIN:
            cands = [
                ("textbox", "", "Username", {}),
                ("textbox", "", "Password", {}),
                ("button", "Log In", None, {}),
                ("link", "Admin Page", None, {"href": "http://h/parabank/admin.htm"}),
            ]
            texts = [("heading", "Customer Login", None)]
            if self.login_failed:
                texts.append(("paragraph", "Invalid credentials. Please try again.", None))
            return cands, texts
        if self.path == OVERVIEW:
            cands = [
                ("link", "Transfer Funds", None, {"href": "http://h/parabank/transfer.htm"}),
                ("link", "Accounts Overview", None, {"href": "http://h/parabank/overview.htm"}),
            ]
            return cands, [
                ("heading", "Accounts Overview", None),
                ("cell", f"${self.balance}.00", "balance cell"),
            ]
        if self.path == TRANSFER:
            cands = [
                ("textbox", "", "Amount", {}),
                ("combobox", "", "From", {"options": ("acct-a", "acct-b")}),
                ("combobox", "", "To", {"options": ("acct-a", "acct-b")}),
                ("button", "Transfer", None, {}),
                ("link", "Accounts Overview", None, {"href": "http://h/parabank/overview.htm"}),
            ]
            texts = [("heading", "Transfer Funds", None)]
            if self.transferred:  # like the live app: the message is split over two elements
                texts.append(("heading", "Transfer Complete!", "confirmation heading"))
                paragraph = f"${self.fields['Amount']}.00 has been transferred "
                paragraph += "from account acct-a to account acct-b."
                texts.append(("paragraph", paragraph, "confirmation"))
            return cands, texts
        return [], []

    def observe(self) -> Observation:
        cands, texts = self._page()
        candidates = []
        for i, (role, name, hint, extra) in enumerate(cands, start=1):
            desc = name or hint
            kwargs = dict(extra)
            if role == "textbox":
                typed = self.fields.get(desc, "")
                kwargs["filled"] = bool(typed)
                kwargs["value"] = typed if typed and typed not in SECRETS.values() else None
            if role == "combobox":
                kwargs["selected"] = self.selected[desc]
            candidates.append(Candidate(i, role, name, hint, None, loc(desc), **kwargs))
        nodes = [
            TextNode(len(cands) + 1 + k, kind, text, loc(desc or f"text:{text}"))
            for k, (kind, text, desc) in enumerate(texts)
        ]
        return Observation(self.url, b"raw", b"masked", candidates, nodes)

    def resolve(self, locator: Locator) -> Handle:
        obs = self.observe()
        known = {c.locator.description for c in obs.candidates}
        known |= {t.locator.description for t in obs.texts if t.locator}
        if locator.description not in known:
            raise LocatorNotFound(locator.description)
        return Handle(locator.description)

    def act(self, handle: Handle, action) -> str | None:
        d = handle.description
        if action.kind == "extract":
            texts = {t.locator.description: t.text for t in self.observe().texts if t.locator}
            return texts.get(d, d)
        if action.kind == "type":
            self.fields[d] = action.value
        elif action.kind == "select":
            if action.value not in ("acct-a", "acct-b"):
                raise ActionFailed("option not found")
            self.selected[d] = action.value
        elif d == "Log In":
            ok = self.fields.get("Username") == "svc-user" and self.fields.get("Password") == "svc-pass"
            self.login_failed = not ok
            self.path = OVERVIEW if ok else LOGIN
        elif d == "Transfer Funds":
            self.path = TRANSFER
        elif d == "Accounts Overview":
            self.path = OVERVIEW
        elif d == "Admin Page":
            self.path = "/parabank/admin.htm"
        elif d == "Transfer":
            self.transfer_clicks += 1
            if self.fields.get("Amount"):
                self.transferred = True
                self.balance -= int(self.fields["Amount"])
        return None


class FakeLLM:
    def __init__(self, site: FakeSite, script: list):
        self.site, self.script, self.calls = site, list(script), []

    def complete(self, *, system, messages, tools) -> LLMResponse:
        self.calls.append({"system": system, "messages": copy.deepcopy(messages), "tools": tools})
        assert self.script, "the fake model's script ran out"
        step = self.script.pop(0)
        out = step(self.site) if callable(step) else step
        if isinstance(out, LLMResponse):
            return out
        blocks = out if isinstance(out, list) else [out]
        return LLMResponse(blocks, "tool_use", input_tokens=10, output_tokens=5, request_id="req")


_ids = itertools.count(1)


def call(tool: str, /, **args) -> dict:  # positional-only: extract has an argument called name
    return {"type": "tool_use", "id": f"tu_{next(_ids)}", "name": tool, "input": args}


def ref(site: FakeSite, needle: str, exact: bool = False) -> int:
    obs = site.observe()
    for c in obs.candidates:
        label = c.name or c.hint or ""
        if label == needle or (not exact and needle in label):
            return c.index
    for t in obs.texts:
        if t.text == needle or (not exact and needle in t.text):
            return t.index
    raise AssertionError(f"nothing on the page matches {needle!r}")


def bal(site: FakeSite) -> str:
    return f"${site.balance}.00"


def login() -> list:
    return [
        lambda s: call("type", ref=ref(s, "Username"), text="svc-user", expect="Password"),
        lambda s: call("type", ref=ref(s, "Password"), text="svc-pass", expect="Log In"),
        lambda s: call("click", ref=ref(s, "Log In", True), expect="Accounts Overview"),
    ]


def read_balance() -> list:
    return [lambda s: call("extract", ref=ref(s, bal(s)), name="source_balance_before")]


def open_transfer() -> list:
    return [lambda s: call("click", ref=ref(s, "Transfer Funds", True), expect="Transfer Funds")]


def fill(amount: str = "5") -> list:
    return [
        lambda s: call("select", ref=ref(s, "From", True), value="acct-a", expect="acct-a"),
        lambda s: call("select", ref=ref(s, "To", True), value="acct-b", expect="acct-b"),
        lambda s: call("type", ref=ref(s, "Amount"), text=amount, expect=amount),
    ]


def submit() -> list:
    return [lambda s: call("click", ref=ref(s, "Transfer", True), expect="Transfer Complete")]


def finish() -> list:
    return [
        lambda s: call("extract", ref=ref(s, "Transfer Complete"), name="confirmation_text"),
        lambda s: call("extract", ref=ref(s, "has been transferred"), name="confirmation_text"),
        lambda s: call("click", ref=ref(s, "Accounts Overview", True), expect="Accounts Overview"),
        lambda s: call("extract", ref=ref(s, bal(s)), name="new_balance"),
        lambda s: call("report_done", reasoning="all recorded"),
    ]


def success_script() -> list:
    return login() + read_balance() + open_transfer() + fill() + submit() + finish()


def run(tmp_path, script, *, site=None, params=None, config=None, clock=None, logger=None):
    site = site or FakeSite()
    params = params or PARAMS
    kinds = {n: p.type for n, p in SPEC.inputs.items()}
    llm = FakeLLM(site, script)
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = logger or EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    result = run_discovery(
        spec=SPEC, params=params, tagged=TaggedValues(params, kinds, SECRETS), adapter=site,
        llm=llm, gate=PolicyGate(Config()), logger=logger, system_prompt="SYSTEM",
        start_url=site.url, config=config, **({"clock": clock} if clock else {}),
    )
    return result, site, llm, logger


def count_images(value) -> int:
    if isinstance(value, dict):
        return int(value.get("type") == "image") + sum(count_images(v) for v in value.values())
    if isinstance(value, list):
        return sum(count_images(v) for v in value)
    return 0


def last_user_text(llm: FakeLLM, index: int = -1) -> str:
    return json.dumps(llm.calls[index]["messages"][-1])


def assert_api_rules(messages: list[dict]) -> None:
    """The structural rules the real API enforces and the fake model cannot."""
    assert messages[0]["role"] == "user" and messages[-1]["role"] == "user"
    for before, after in itertools.pairwise(messages):
        assert before["role"] != after["role"], "roles must alternate"
    for i, message in enumerate(messages):
        blocks = message["content"]
        assert blocks, "a message needs content"
        kinds = [b["type"] for b in blocks]
        if "tool_result" in kinds:  # tool results must come before any other block
            first_other = next((k for k, kind in enumerate(kinds) if kind != "tool_result"), len(kinds))
            assert "tool_result" not in kinds[first_other:], "tool results must come first"
        for block in blocks:
            if block["type"] == "text":
                assert block["text"].strip(), "no empty text blocks"
            if block["type"] == "tool_result":
                assert block["content"]
                if block["is_error"]:  # the crash we hit live: an error result may hold only text
                    assert all(c["type"] == "text" for c in block["content"])
        uses = [b["id"] for b in blocks if b["type"] == "tool_use"]
        if uses:
            following = messages[i + 1]["content"] if i + 1 < len(messages) else []
            answered = [b["tool_use_id"] for b in following if b["type"] == "tool_result"]
            assert sorted(answered) == sorted(uses), "every tool call needs exactly one result"


# -- the success path ---------------------------------------------------------------------
def test_full_success_path(tmp_path):
    result, site, llm, _ = run(tmp_path, success_script())
    assert result.stop_reason == StopReason.SUCCESS
    assert result.outputs["source_balance_before"] == "$100.00"
    assert result.outputs["new_balance"] == "$95.00"
    assert "Transfer Complete" in result.outputs["confirmation_text"]
    counted = [s for s in result.steps if s.counted]
    assert result.llm_calls == len(llm.calls) == 14 and len(counted) == 13
    assert all(s.result == "ok" for s in result.steps)
    assert all(s.checkpoint_status == "verified" for s in counted)
    assert (result.input_tokens, result.output_tokens) == (140, 70)
    assert site.transfer_clicks == 1


def test_secret_steps_never_hold_a_value_and_params_are_labelled(tmp_path):
    result, *_ = run(tmp_path, success_script())
    typed = [s for s in result.steps if s.tool == "type"]
    assert [(s.provenance, s.value) for s in typed[:2]] == [("secret", None), ("secret", None)]
    assert (typed[2].provenance, typed[2].param, typed[2].value) == ("parameter", "amount", "5")
    selects = [s for s in result.steps if s.tool == "select"]
    assert [s.param for s in selects] == ["from_account", "to_account"]


def test_the_model_gets_the_goal_and_credentials_but_the_prompt_stays_generic(tmp_path):
    _, _, llm, _ = run(tmp_path, success_script())
    first = json.dumps(llm.calls[0]["messages"][0])
    assert "acct-a" in first and "svc-pass" in first
    assert llm.calls[0]["system"] == "SYSTEM"


def test_only_the_latest_observation_keeps_its_screenshot(tmp_path):
    _, _, llm, _ = run(tmp_path, success_script())
    assert all(count_images(c["messages"]) == 1 for c in llm.calls)
    older = llm.calls[-1]["messages"][2]["content"][0]["content"]  # the first tool result
    assert len(older) == 1 and older[0]["text"].startswith("Step 1:")


def test_evidence_holds_no_password_and_no_screenshot_data(tmp_path):
    result, _, _, log = run(tmp_path, success_script())
    for name in ("transcript.jsonl", "trace.json", "log.jsonl", "result.json"):
        text = (log.dir / name).read_text(encoding="utf-8")
        assert "svc-pass" not in text and "svc-user" not in text, name
    transcript = (log.dir / "transcript.jsonl").read_text(encoding="utf-8")
    assert "cmF3" not in transcript and '"omitted": true' in transcript  # base64 of the raw image
    assert (log.dir / "step-00.png").read_bytes() == b"masked"
    assert (log.dir / result.steps[2].screenshot).read_bytes() == b"masked"
    assert json.loads((log.dir / "result.json").read_text("utf-8"))["stop_reason"] == "SUCCESS"


# -- stop reasons -------------------------------------------------------------------------
def test_max_steps_is_enforced(tmp_path):
    result, *_ = run(tmp_path, login(), config=DiscoveryConfig(max_steps=2))
    assert result.stop_reason == StopReason.MAX_STEPS_EXCEEDED
    assert result.llm_calls == 3 and len([s for s in result.steps if s.counted]) == 2


def test_timeout_is_enforced(tmp_path):
    clock_time = [0.0]

    def slow(_site):
        clock_time[0] += 400
        return call("click", ref=1, expect="x")

    result, *_ = run(tmp_path, [slow], clock=lambda: clock_time[0])
    assert result.stop_reason == StopReason.TIMEOUT and result.steps == []


def test_repeated_identical_failures_are_a_dead_end(tmp_path):
    bad = lambda s: call("select", ref=ref(s, "From", True), value="nope", expect="x")
    result, *_ = run(tmp_path, [bad, bad, bad], site=FakeSite(path=TRANSFER))
    assert (result.stop_reason, result.detail) == (StopReason.DEAD_END, None)
    assert [s.result for s in result.steps] == ["error"] * 3


def test_report_stuck_is_a_dead_end(tmp_path):
    result, *_ = run(tmp_path, [call("report_stuck", reasoning="no idea")])
    assert (result.stop_reason, result.detail) == (StopReason.DEAD_END, "report_stuck")


def test_repeated_rejected_done_claims_end_the_run_long_before_the_timeout(tmp_path):
    claim = lambda s: call("report_done", reasoning="done")
    result, _, llm, log = run(tmp_path, [claim] * 4)
    assert (result.stop_reason, result.detail) == (StopReason.DEAD_END, "done_rejected")
    assert result.llm_calls == len(llm.calls) == 3 and result.elapsed_seconds < 5
    claims = [s for s in result.steps if s.tool == "report_done"]
    assert len(claims) == 3 and not any(s.counted for s in claims)
    assert all("was not captured" in s.error for s in claims)  # the reasons are on the record
    meta = json.loads((log.dir / "result.json").read_text(encoding="utf-8"))
    assert meta["detail"] == "done_rejected"


def test_an_action_between_done_claims_resets_the_count(tmp_path):
    claim = lambda s: call("report_done", reasoning="done")
    script = [
        claim, claim,
        lambda s: call("type", ref=ref(s, "Username"), text="svc-user", expect="x"),
        claim, claim,
        call("report_stuck", reasoning="stop"),
    ]
    result, *_ = run(tmp_path, script)
    assert result.detail == "report_stuck"  # never three in a row


def test_a_crash_leaves_a_labelled_partial_record_and_still_propagates(tmp_path):
    site = FakeSite()
    honest_act = site.act

    def crashing(handle, action):
        if action.kind == "click":
            raise RuntimeError("boom")  # a bug in our code, not an agent failure
        return honest_act(handle, action)

    site.act = crashing
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        run(tmp_path, login(), site=site, logger=logger)
    meta = json.loads((logger.dir / "result.json").read_text(encoding="utf-8"))
    assert (meta["stop_reason"], meta["detail"]) == ("DEAD_END", "crashed: RuntimeError")
    trace = json.loads((logger.dir / "trace.json").read_text(encoding="utf-8"))
    assert [s["tool"] for s in trace["steps"]] == ["type", "type"]  # everything before the crash
    assert "svc-pass" not in (logger.dir / "trace.json").read_text(encoding="utf-8")


def test_the_bounds_of_the_run_are_recorded(tmp_path):
    config = DiscoveryConfig(max_steps=2, timeout_s=99)
    result, _, _, log = run(tmp_path, login(), config=config)
    for name in ("result.json", "trace.json"):
        meta = json.loads((log.dir / name).read_text(encoding="utf-8"))
        assert (meta["max_steps"], meta["timeout_s"]) == (2, 99.0), name
    assert result.stop_reason == StopReason.MAX_STEPS_EXCEEDED


def test_every_message_sent_follows_the_apis_structural_rules(tmp_path):
    claim = lambda s: call("report_done", reasoning="done")
    admin = lambda s: call("click", ref=ref(s, "Admin Page"), expect="x")
    bad_select = lambda s: call("select", ref=ref(s, "From", True), value="nope", expect="x")
    idle = LLMResponse([{"type": "text", "text": "hmm"}], "end_turn", 1, 1)
    mixed = [
        call("teleport"),
        lambda s: call("click", ref=99, expect="x"),
        lambda s: [
            call("type", ref=ref(s, "Username"), text="svc-user", expect="x"),
            call("type", ref=ref(s, "Password"), text="svc-pass", expect="x"),
        ],
        call("report_stuck", reasoning="stop"),
    ]
    scenarios = [
        (success_script(), None),  # ordinary results
        ([admin, admin, admin], None),  # blocked: the live crash
        ([bad_select, bad_select, bad_select], FakeSite(path=TRANSFER)),  # failed actions
        ([claim, claim, claim], None),  # rejected done claims
        (mixed, None),  # invalid call, bad reference, parallel calls
        ([idle, idle, idle], None),  # replies with no tool call
    ]
    for script, site in scenarios:
        _, _, llm, _ = run(tmp_path, script, site=site)
        for sent in llm.calls:
            assert_api_rules(sent["messages"])
            assert count_images(sent["messages"]) <= 1  # only the latest screenshot


def test_a_blocked_result_keeps_its_screenshot_as_a_separate_block(tmp_path):
    admin = lambda s: call("click", ref=ref(s, "Admin Page"), expect="x")
    _, _, llm, _ = run(tmp_path, [admin, call("report_stuck", reasoning="stop")])
    answer = llm.calls[1]["messages"][-1]["content"]
    assert [b["type"] for b in answer] == ["tool_result", "image"]
    assert answer[0]["is_error"] is True and [c["type"] for c in answer[0]["content"]] == ["text"]
    assert "Blocked" in answer[0]["content"][0]["text"]


def test_a_blocked_action_tells_the_model_not_to_repeat_it_and_to_find_another_way(tmp_path):
    # Found live: an agent whose first instinct was a page the policy forbids repeated the same
    # blocked click three times and hit the dead-end rule. The feedback it gets at that moment is
    # generic (names nothing about the target) and says so plainly.
    admin = lambda s: call("click", ref=ref(s, "Admin Page"), expect="x")
    _, _, llm, _ = run(tmp_path, [admin, call("report_stuck", reasoning="stop")])
    text = llm.calls[1]["messages"][-1]["content"][0]["content"][0]["text"]
    assert text.startswith("Blocked: ")
    assert "do not repeat it" in text and "find another way to reach the goal" in text


def test_the_submit_is_blocked_until_every_input_was_entered(tmp_path):
    script = (
        read_balance() + open_transfer()
        + [fill()[0], fill()[2]]  # source and amount only: the destination is left at its default
        + submit()  # blocked: nothing is sent to the site
        + [fill()[1]]  # now the destination
        + submit() + finish()
    )
    result, site, llm, _ = run(tmp_path, script, site=FakeSite(path=OVERVIEW))
    blocked = next(s for s in result.steps if s.result == "blocked")
    assert blocked.tool == "click"
    assert "the value for to_account was never entered" in blocked.error
    assert result.stop_reason == StopReason.SUCCESS and site.transfer_clicks == 1
    steps = result.steps
    chosen = next(i for i, s in enumerate(steps) if s.param == "to_account")
    sent = max(
        i for i, s in enumerate(steps)
        if s.tool == "click" and s.result == "ok" and s.target.description == "Transfer"
    )
    assert chosen < sent  # the destination was chosen before the transfer that moved the money
    for call_sent in llm.calls:
        assert_api_rules(call_sent["messages"])


def test_the_model_receives_the_config_derived_descriptions_and_goal(tmp_path):
    _, _, llm, _ = run(tmp_path, [call("report_stuck", reasoning="x")])
    extract = next(t for t in llm.calls[0]["tools"] if t["name"] == "extract")
    for name, meaning in SPEC.extract_descriptions(PARAMS).items():
        assert f"{name}: {meaning}" in extract["description"]
    assert "record each part under this name" in extract["description"]
    assert "Optional: if it cannot be found after a genuine search, skip it." in (
        extract["description"]
    )
    assert SPEC.goal_text(PARAMS) in json.dumps(llm.calls[0]["messages"][0])


def test_a_model_failure_ends_the_run_and_still_writes_the_result(tmp_path):
    def broken(_site):
        raise LLMError("APIConnectionError: down")

    result, _, _, log = run(tmp_path, [broken])
    assert result.stop_reason == StopReason.DEAD_END
    assert result.detail == "llm_error: APIConnectionError: down"
    assert (log.dir / "trace.json").exists() and (log.dir / "result.json").exists()


def test_refusal_and_idle_replies_end_the_run(tmp_path):
    refusal = LLMResponse([], "refusal", 1, 1)
    result, *_ = run(tmp_path, [refusal])
    assert (result.stop_reason, result.detail) == (StopReason.DEAD_END, "model_refused")
    idle = LLMResponse([{"type": "text", "text": "hmm"}], "end_turn", 1, 1)
    result, _, llm, _ = run(tmp_path, [idle, idle, idle])
    assert (result.stop_reason, result.detail) == (StopReason.DEAD_END, "no_tool_call")
    assert "Reply with exactly one tool call" in last_user_text(llm, 1)
    assert count_images(llm.calls[1]["messages"]) == 1  # the nudge still carries the page


# -- the guard inside the loop ---------------------------------------------------------------
def test_a_denied_link_is_blocked_and_fed_back(tmp_path):
    admin = lambda s: call("click", ref=ref(s, "Admin Page"), expect="x")
    result, site, llm, _ = run(tmp_path, [admin, admin, admin])
    assert site.path == LOGIN  # never went there
    assert [s.result for s in result.steps] == ["blocked"] * 3
    assert "Blocked" in last_user_text(llm, 1) and "path denied" in last_user_text(llm, 1)
    assert (result.stop_reason, result.detail) == (StopReason.DEAD_END, "blocked_by_policy")


def test_the_amount_is_blocked_until_a_covering_balance_is_known(tmp_path):
    site = FakeSite(path=TRANSFER)
    result, site, *_ = run(
        tmp_path, [lambda s: call("type", ref=ref(s, "Amount"), text="5", expect="5"),
                   call("report_stuck", reasoning="blocked")], site=site,
    )
    assert result.steps[0].result == "blocked" and "balance unknown" in result.steps[0].error
    assert "Amount" not in site.fields


def test_an_amount_above_the_balance_is_blocked(tmp_path):
    script = read_balance() + open_transfer() + [
        lambda s: call("type", ref=ref(s, "Amount"), text="500", expect="500"),
        call("report_stuck", reasoning="blocked"),
    ]
    result, site, *_ = run(
        tmp_path, script, site=FakeSite(path=OVERVIEW), params={**PARAMS, "amount": "500"}
    )
    typed = result.steps[2]
    assert typed.result == "blocked" and "exceeds balance" in typed.error
    assert "Amount" not in site.fields


def test_an_amount_needing_approval_is_blocked_in_discovery(tmp_path):
    script = read_balance() + open_transfer() + [
        lambda s: call("type", ref=ref(s, "Amount"), text="150", expect="150"),
        call("report_stuck", reasoning="blocked"),
    ]
    result, *_ = run(
        tmp_path, script, site=FakeSite(balance=1000, path=OVERVIEW),
        params={**PARAMS, "amount": "150"},
    )
    assert result.steps[2].result == "blocked" and "approval" in result.steps[2].error


def test_a_submitted_transfer_is_never_submitted_twice(tmp_path):
    again = lambda s: call("click", ref=ref(s, "Transfer", True), expect="x")
    script = read_balance() + open_transfer() + fill() + submit() + [
        again, call("report_stuck", reasoning="stop"),
    ]
    result, site, *_ = run(tmp_path, script, site=FakeSite(path=OVERVIEW))
    assert site.transfer_clicks == 1
    repeat = result.steps[-2]
    assert repeat.result == "blocked" and "already submitted" in repeat.error


# -- report_done, bad calls ---------------------------------------------------------------
def test_report_done_is_rejected_until_every_output_is_recorded(tmp_path):
    early = [lambda s: call("report_done", reasoning="done")]
    script = read_balance() + open_transfer() + fill() + submit() + early + finish()
    result, _, llm, _ = run(tmp_path, script, site=FakeSite(path=OVERVIEW))
    assert result.stop_reason == StopReason.SUCCESS
    rejected = next(s for s in result.steps if s.tool == "report_done" and s.result == "error")
    assert "confirmation_text was not captured" in rejected.error
    assert "Not done yet" in last_user_text(llm, 7)  # what the model sees right after its early claim
    assert "Transfer Complete" not in rejected.error  # the required phrases are never quoted back


def test_a_confirmation_split_over_two_elements_is_combined_not_duplicated(tmp_path):
    heading = lambda s: call("extract", ref=ref(s, "Transfer Complete"), name="confirmation_text")
    paragraph = lambda s: call(
        "extract", ref=ref(s, "has been transferred"), name="confirmation_text"
    )
    script = read_balance() + open_transfer() + fill() + submit() + [
        heading,
        heading,  # a repeat adds nothing
        lambda s: call("report_done", reasoning="early"),
        paragraph,
        lambda s: call("click", ref=ref(s, "Accounts Overview", True), expect="Accounts Overview"),
        lambda s: call("extract", ref=ref(s, bal(s)), name="new_balance"),
        lambda s: call("report_done", reasoning="all recorded"),
    ]
    result, *_ = run(tmp_path, script, site=FakeSite(path=OVERVIEW))
    early = next(s for s in result.steps if s.tool == "report_done" and s.result == "error")
    assert "confirmation_text does not confirm the goal was met" in early.error
    assert result.stop_reason == StopReason.SUCCESS
    text = result.outputs["confirmation_text"]
    assert text.count("Transfer Complete!") == 1 and "has been transferred" in text


def test_typing_and_selecting_get_no_expectation_feedback(tmp_path):
    script = [
        lambda s: call("type", ref=ref(s, "Username"), text="svc-user", expect="zzz not on page"),
        call("report_stuck", reasoning="stop"),
    ]
    result, _, llm, _ = run(tmp_path, script)
    assert result.steps[0].expectation is None and result.steps[0].checkpoint_status == "verified"
    assert "you expected" not in last_user_text(llm, 1)


def test_bad_and_parallel_tool_calls_get_error_results(tmp_path):
    script = [
        call("teleport"),
        lambda s: call("click", ref=99, expect="x"),
        lambda s: [
            call("type", ref=ref(s, "Username"), text="svc-user", expect="x"),
            call("type", ref=ref(s, "Password"), text="svc-pass", expect="x"),
        ],
        call("report_stuck", reasoning="stop"),
    ]
    result, site, llm, _ = run(tmp_path, script)
    assert site.fields == {"Username": "svc-user"}  # only the first call of the pair ran
    assert "no tool called" in last_user_text(llm, 1) and "no element [99]" in last_user_text(llm, 2)
    results = llm.calls[3]["messages"][-1]["content"]
    assert len(results) == 2 and [r["is_error"] for r in results] == [False, True]
    assert result.stop_reason == StopReason.DEAD_END


def test_a_failed_login_is_reported_back_to_the_model(tmp_path):
    script = [
        lambda s: call("type", ref=ref(s, "Username"), text="wrong-user", expect="x"),
        lambda s: call("type", ref=ref(s, "Password"), text="svc-pass", expect="x"),
        lambda s: call("click", ref=ref(s, "Log In", True), expect="Accounts Overview"),
        call("report_stuck", reasoning="cannot log in"),
    ]
    result, _, llm, _ = run(tmp_path, script)
    click = result.steps[2]
    assert click.expectation == "refuted"
    assert "you expected" in last_user_text(llm, 3) and "Invalid credentials" in last_user_text(llm, 3)


# -- what may reach the agent ------------------------------------------------------------------
def test_the_prompt_and_tool_schemas_name_no_pages_or_technical_details():
    prompt = (ROOT / "prompts" / "discovery_system.md").read_text(encoding="utf-8")
    tools = json.dumps(tool_definitions(SPEC.extract_descriptions(PARAMS)))
    for text in (prompt.lower(), tools.lower()):
        for word in ("parabank", "htm", "http", "admin", "jsp", "#", "showresult", "services"):
            assert word not in text, word


# -- dispatched, then stuck: discovery's own dispatch_unverified ticket -------------------------
def test_dispatched_then_stuck_opens_a_ticket_and_marks_the_result_unverified(tmp_path):
    # The real submit fires, then discovery loses its way immediately afterward — the exact
    # shape this fix exists for: money may have moved, and nothing here confirms it.
    script = (
        read_balance() + open_transfer() + fill() + submit()
        + [call("report_stuck", reasoning="lost track after the transfer")]
    )
    result, site, _, log = run(tmp_path, script, site=FakeSite(path=OVERVIEW))
    assert site.transfer_clicks == 1  # the dispatch genuinely happened
    assert (result.stop_reason, result.detail) == (StopReason.DEAD_END, "report_stuck")
    assert result.side_effects == "unverified"
    assert len(result.escalations) == 1
    ticket = result.escalations[0]
    assert ticket.status == "open" and ticket.run_id == result.run_id
    assert "DEAD_END" in ticket.reason
    submission_step = next(s for s in result.steps if s.tool == "click" and s.target.description == "Transfer")
    assert ticket.step_index == submission_step.step_no
    assert ticket.step_description == "Transfer"
    assert ticket.screenshot == submission_step.screenshot
    # The ticket is a real file, findable the same way any other ticket is, and self-consistent
    # the same way scripts/audit_evidence.py checks: filename matches the ticket_id inside it.
    ticket_path = log.dir / f"ticket-{ticket.ticket_id}.json"
    assert ticket_path.exists()
    on_disk = json.loads(ticket_path.read_text(encoding="utf-8"))
    assert on_disk["ticket_id"] == ticket.ticket_id and on_disk["run_id"] == result.run_id
    meta = json.loads((log.dir / "result.json").read_text(encoding="utf-8"))
    assert meta["side_effects"] == "unverified"


def test_stuck_before_any_dispatch_opens_no_ticket(tmp_path):
    # The ordinary, low-stakes case must stay exactly as before: nothing moved, nothing to verify.
    result, _, _, log = run(tmp_path, [call("report_stuck", reasoning="no idea")])
    assert result.stop_reason == StopReason.DEAD_END
    assert result.side_effects == "none" and result.escalations == []
    assert list(log.dir.glob("ticket-*.json")) == []
    meta = json.loads((log.dir / "result.json").read_text(encoding="utf-8"))
    assert meta["side_effects"] == "none" and meta["escalations"] == []


def test_a_successful_run_opens_no_ticket_but_reports_committed(tmp_path):
    # Dispatch alone is not the ticket trigger — only dispatch *followed by a failure to reach
    # SUCCESS* opens one. But a dispatch that SUCCESS then confirms is not "none" either: it
    # genuinely moved money, so it reports the same "committed" replay would report.
    result, site, _, log = run(tmp_path, success_script())
    assert result.stop_reason == StopReason.SUCCESS and site.transfer_clicks == 1
    assert result.side_effects == "committed" and result.escalations == []
    assert list(log.dir.glob("ticket-*.json")) == []
    meta = json.loads((log.dir / "result.json").read_text(encoding="utf-8"))
    assert meta["side_effects"] == "committed"


def test_the_unverified_ticket_carries_a_real_verification_procedure(tmp_path):
    script = (
        read_balance() + open_transfer() + fill() + submit()
        + [call("report_stuck", reasoning="lost track after the transfer")]
    )
    result, *_ = run(tmp_path, script, site=FakeSite(path=OVERVIEW))
    ticket = result.escalations[0]
    proc = ticket.procedure
    assert proc is not None
    assert "acct-a" in proc and "acct-b" in proc  # the real accounts, for the operator only
    assert "amount=5" not in proc  # amount is reported separately, not folded into "Inputs:"
    assert "Amount: 5." in proc
    assert "Balance before the step: 100" in proc  # the balance read earlier in this same run
    assert "Dispatched at: " in proc and "(UTC)" in proc
    assert "check the target account's transaction or activity history" in proc.lower()


def test_the_saved_ticket_redacts_real_account_numbers_from_its_procedure(tmp_path):
    # PARAMS elsewhere in this file uses letter accounts ("acct-a"), which the digit-pattern
    # redactor never touches — that would prove nothing about real redaction. Real ParaBank
    # accounts are 5-digit numbers (recon-notes.md); this drives open_ticket() directly with that
    # shape, the same call discovery itself makes, to prove the write path actually redacts it —
    # independent of whatever the FakeSite harness elsewhere in this file happens to select.
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    procedure = (
        "A money-moving step was clicked during discovery.\n"
        "Inputs: from_account=10001, to_account=20002.\nAmount: 5.\n"
        "Balance before the step: 100."
    )
    ticket = open_ticket(logger, logger.run_id, 3, "test", procedure=procedure)
    assert "10001" in ticket.procedure and "20002" in ticket.procedure  # unredacted, in memory
    on_disk = json.loads((logger.dir / f"ticket-{ticket.ticket_id}.json").read_text(encoding="utf-8"))
    assert "10001" not in on_disk["procedure"] and "20002" not in on_disk["procedure"]
    assert "[REDACTED]" in on_disk["procedure"]


def test_a_crash_after_dispatch_still_opens_a_ticket(tmp_path):
    # The crash-labelling rule ("crashed: ...") and the dispatch ticket are orthogonal: a bug in
    # our own code is still not what a caller needs to hear about first if money already moved.
    site = FakeSite(path=OVERVIEW)
    honest_act = site.act

    def crashing(handle, action):
        if handle.description == "Accounts Overview" and site.transferred:
            raise RuntimeError("boom")  # a bug in our code, after the real transfer already fired
        return honest_act(handle, action)

    site.act = crashing
    script = read_balance() + open_transfer() + fill() + submit() + finish()
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        run(tmp_path, script, site=site, logger=logger)
    meta = json.loads((logger.dir / "result.json").read_text(encoding="utf-8"))
    assert meta["stop_reason"] == "DEAD_END" and meta["detail"] == "crashed: RuntimeError"
    assert meta["side_effects"] == "unverified" and len(meta["escalations"]) == 1
    assert list(logger.dir.glob("ticket-*.json"))  # the ticket is a real file, not just in the result


def test_a_click_that_raises_on_the_submission_is_unverified_and_blocks_a_retry(tmp_path):
    # The request may already be in flight — only LocatorNotFound proves nothing was clicked.
    # ActionFailed on the one candidate submission click must count as a possible dispatch too,
    # not "nothing happened, try again" — a retry could double-dispatch if the first one worked.
    site = FakeSite(path=OVERVIEW)
    honest_act = site.act
    raised = {"once": False}

    def flaky(handle, action):
        if handle.description == "Transfer" and not raised["once"]:
            raised["once"] = True
            raise ActionFailed("timeout waiting for a response")
        return honest_act(handle, action)

    site.act = flaky
    again = lambda s: call("click", ref=ref(s, "Transfer", True), expect="x")
    script = read_balance() + open_transfer() + fill() + [
        submit()[0], again, call("report_stuck", reasoning="stop"),
    ]
    result, site, _, log = run(tmp_path, script, site=site)
    assert site.transfer_clicks == 0  # the retry never reached the site — blocked by the guard
    assert result.side_effects == "unverified" and len(result.escalations) == 1
    failed_step = next(s for s in result.steps if s.tool == "click" and s.result == "error")
    assert result.escalations[0].step_index == failed_step.step_no
    retry = next(s for s in result.steps if s.result == "blocked" and "already submitted" in (s.error or ""))
    assert retry is not None
    ticket_path = log.dir / f"ticket-{result.escalations[0].ticket_id}.json"
    assert ticket_path.exists()


def test_a_dispatch_blocked_by_the_no_resubmit_rule_does_not_reopen_the_ticket(tmp_path):
    # A second click on Transfer is blocked ("already submitted"), never a second dispatch — the
    # ticket must trace back to the one real submission, not to the run's last action.
    again = lambda s: call("click", ref=ref(s, "Transfer", True), expect="x")
    script = read_balance() + open_transfer() + fill() + submit() + [
        again, call("report_stuck", reasoning="stop"),
    ]
    result, site, _, _ = run(tmp_path, script, site=FakeSite(path=OVERVIEW))
    assert site.transfer_clicks == 1  # the repeat never reached the site
    assert result.side_effects == "unverified" and len(result.escalations) == 1
    submission_step = next(s for s in result.steps if s.tool == "click" and s.target.description == "Transfer")
    assert result.escalations[0].step_index == submission_step.step_no
