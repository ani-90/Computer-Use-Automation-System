"""The discovery loop against a scripted fake site and a scripted fake model.

Both fakes exist only in this file. They stand in for the browser adapter and the language model
so the loop's own rules can be checked deterministically. Nothing here reaches a real agent.
"""

import copy
import itertools
import json
from pathlib import Path
from urllib.parse import urlparse

from cua.adapter import ActionFailed, Candidate, LocatorNotFound, Observation, TextNode
from cua.config import Config
from cua.discovery import DiscoveryConfig, run_discovery
from cua.enums import StopReason
from cua.evidence import EvidenceLogger, new_run_id
from cua.goal import GoalSpec, TaggedValues
from cua.llm import LLMResponse
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
            if self.transferred:
                done = f"Transfer Complete! ${self.fields['Amount']}.00 has been transferred "
                done += "from account acct-a to account acct-b."
                texts.append(("paragraph", done, "confirmation"))
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
        lambda s: call("click", ref=ref(s, "Accounts Overview", True), expect="Accounts Overview"),
        lambda s: call("extract", ref=ref(s, bal(s)), name="new_balance"),
        lambda s: call("report_done", reasoning="all recorded"),
    ]


def success_script() -> list:
    return login() + read_balance() + open_transfer() + fill() + submit() + finish()


def run(tmp_path, script, *, site=None, params=None, config=None, clock=None):
    site = site or FakeSite()
    params = params or PARAMS
    kinds = {n: p.type for n, p in SPEC.inputs.items()}
    llm = FakeLLM(site, script)
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
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


# -- the success path ---------------------------------------------------------------------
def test_full_success_path(tmp_path):
    result, site, llm, _ = run(tmp_path, success_script())
    assert result.stop_reason == StopReason.SUCCESS
    assert result.outputs["source_balance_before"] == "$100.00"
    assert result.outputs["new_balance"] == "$95.00"
    assert "Transfer Complete" in result.outputs["confirmation_text"]
    counted = [s for s in result.steps if s.counted]
    assert result.llm_calls == len(llm.calls) == 13 and len(counted) == 12
    assert all(s.result == "ok" for s in result.steps)
    assert all(s.checkpoint_status == "verified" for s in counted)
    assert (result.input_tokens, result.output_tokens) == (130, 65)
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
