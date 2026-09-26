"""The whole HTTP path with the real route and the real invoke_capability(), only the browser
faked: POST /capabilities/<name>/invoke -> ReplayEngine -> a redacted, caller-safe JSON body.

test_api.py only checks the thin route with invoke_capability() mocked; test_dispatch_unverified.py
checks the engine directly. Neither proves what an outside caller actually receives when a money-moving
click is dispatched but never confirmed — the outcome a caller can never trigger on purpose over
HTTP (fault injection is CLI-only), so only a test can cover it.
"""

import json

import pytest
from fastapi.testclient import TestClient

import cua.adapter
import cua.config
from cua.api import app
from tests.test_dispatch_unverified import DroppedConfirmationBank
from tests.test_replay import BASE, FROM, PARAMS, SECRETS, TO, FakeBank

client = TestClient(app)


class _FastConfig(cua.config.Config):
    submit_confirmation_wait_ms: int = 50  # the real default is 60s; a test must not wait that long


@pytest.fixture
def http_env(tmp_path, monkeypatch):
    """Point invoke_capability() at a fake browser, a temp evidence dir, and fake credentials —
    nothing here can reach a real ParaBank, a real .env, or the repo's own evidence/ folder."""
    monkeypatch.chdir(tmp_path)  # invoke_capability writes to a cwd-relative evidence/replay
    monkeypatch.setenv("PARABANK_BASE_URL", BASE)
    monkeypatch.setenv("PARABANK_USERNAME", SECRETS["username"])
    monkeypatch.setenv("PARABANK_PASSWORD", SECRETS["password"])
    monkeypatch.setattr(cua.config, "Config", _FastConfig)

    holder = {}

    def use(bank):
        holder["bank"] = bank

        class _Browser:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return bank

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(cua.adapter, "PlaywrightAdapter", _Browser)
        return bank

    use.evidence = tmp_path / "evidence" / "replay"
    return use


def _invoke(args):
    response = client.post("/capabilities/transfer_funds/invoke", json=args)
    assert response.status_code == 200
    return response.json()


def test_an_unverified_dispatch_over_http_is_flagged_with_correlation_ids_and_no_procedure(http_env):
    bank = http_env(DroppedConfirmationBank())

    body = _invoke(PARAMS)

    assert body["status"] == "HARD_FAILURE"
    assert body["business_outcome"] == "dispatch_unverified"
    assert "verify before any retry" in body["failure_detail"]["observed"]
    assert bank.transfer_clicks == 1  # the engine never re-clicked

    (ticket,) = body["escalations"]
    assert ticket["status"] == "open"
    assert ticket["ticket_id"]
    assert ticket["run_id"] == body["run_id"]
    assert "procedure" not in ticket  # operator-only: it embeds this run's own parameters

    # an outside caller never sees the real account numbers or the service-account login
    text = json.dumps(body)
    for secret in (FROM, TO, SECRETS["username"], SECRETS["password"]):
        assert secret not in text


def test_the_unverified_dispatch_still_leaves_a_redacted_ticket_on_disk_for_the_operator(http_env):
    http_env(DroppedConfirmationBank())

    body = _invoke(PARAMS)

    run_dir = http_env.evidence / body["run_id"]  # the folder is named after the same run_id
    (ticket_file,) = run_dir.glob("ticket-*.json")
    saved = json.loads(ticket_file.read_text(encoding="utf-8"))
    assert saved["ticket_id"] == body["escalations"][0]["ticket_id"]
    assert saved["run_id"] == body["run_id"]
    assert "Inputs:" in saved["procedure"]  # the operator's copy keeps the procedure...
    assert FROM not in ticket_file.read_text(encoding="utf-8")  # ...but never a real account number


def test_the_same_account_twice_over_http_is_a_policy_block_like_the_cli(http_env):
    bank = http_env(FakeBank())

    body = _invoke({**PARAMS, "to_account": FROM})

    assert body["status"] == "POLICY_BLOCK"
    assert body["escalations"] == []
    assert bank.page == "login"  # the browser was never touched


def _spy_on_replay(monkeypatch):
    """Record the `fault` argument replay() actually receives; return a minimal SUCCESS."""
    import cua.replay
    from cua.enums import Outcome
    from cua.models import ReplayResult

    seen = []

    def spy(self, capability, params, secrets, start_url, on_escalate=None, fault=None):
        seen.append(fault)
        return ReplayResult(run_id="r", status=Outcome.SUCCESS)

    monkeypatch.setattr(cua.replay.ReplayEngine, "replay", spy)
    return seen


def test_no_fault_reaches_replay_when_the_operator_set_none(http_env, monkeypatch):
    http_env(FakeBank())
    seen = _spy_on_replay(monkeypatch)

    _invoke(PARAMS)

    assert seen == [None]


def test_the_operators_fault_reaches_replay_on_every_invocation(http_env, monkeypatch):
    import cua.api
    from cua.models import FaultInjection

    http_env(FakeBank())
    fault = FaultInjection(step_index=5, fault_type="transient_fail", url_pattern="**/*transfer*", delay_ms=90000)
    monkeypatch.setattr(cua.api, "_FAULT", fault)
    seen = _spy_on_replay(monkeypatch)

    _invoke(PARAMS)
    _invoke(PARAMS)

    assert seen == [fault, fault]


def test_a_caller_can_never_switch_a_fault_on_through_the_request(http_env, monkeypatch):
    http_env(FakeBank())
    seen = _spy_on_replay(monkeypatch)

    # Whatever an outside caller puts in the body, the only source of a fault is the operator's env.
    _invoke({**PARAMS, "fault": "transient_fail:5:**/*transfer*:90000", "inject_faults": "true"})

    assert seen == [None]


def test_a_normal_transfer_over_http_is_a_plain_success(http_env):
    http_env(FakeBank())

    body = _invoke(PARAMS)

    assert body["status"] == "SUCCESS"
    assert body["business_outcome"] is None
    assert body["escalations"] == []
    assert body["outputs"]["new_balance"]


def test_an_amount_with_too_many_decimals_is_a_policy_block_over_http_and_never_reaches_the_app(http_env):
    # The target accepts such an amount, moves the unrounded value and is left unusable: the contract
    # must refuse it before any browser opens, whichever caller (CLI, HTTP, agent) sent it.
    bank = http_env(FakeBank())

    body = _invoke({**PARAMS, "amount": "1.98484"})

    assert body["status"] == "POLICY_BLOCK"
    assert "at most 2 decimal places" in body["failure_detail"]["observed"]
    assert body["escalations"] == []
    assert bank.page == "login" and not bank.transferred
