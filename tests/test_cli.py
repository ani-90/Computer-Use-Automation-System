from pathlib import Path

import pytest

from cua.cli import main
from cua.enums import Outcome
from cua.llm import LLMError
from cua.models import Outputs

SPEC_PATH = str(Path(__file__).resolve().parent.parent / "goals" / "transfer_funds.json")
CAP_PATH = str(Path(__file__).resolve().parent.parent / "capabilities" / "transfer_funds.json")
PARAMS = ["--param", "from_account=acct-a", "--param", "to_account=acct-b", "--param", "amount=5"]



@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """replay and discover write evidence to a cwd-relative folder. Without this, every test run left
    empty run folders in the repository's real evidence/ tree."""
    monkeypatch.chdir(tmp_path)

def set_env(monkeypatch):
    monkeypatch.setenv("PARABANK_BASE_URL", "http://h/p")
    monkeypatch.setenv("PARABANK_USERNAME", "svc-user-x")
    monkeypatch.setenv("PARABANK_PASSWORD", "svc-pass-x")


def test_dry_run_shows_the_goal_and_hides_secret_values(monkeypatch, capsys):
    set_env(monkeypatch)
    code = main(["discover", "--goal", SPEC_PATH, *PARAMS, "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "http://h/p/index.htm" in out and "acct-a" in out
    assert "from_account" in out and "password" in out  # names only
    assert "svc-user-x" not in out and "svc-pass-x" not in out


def test_bad_arguments_exit_with_an_error(monkeypatch, capsys):
    set_env(monkeypatch)
    assert main(["discover", "--goal", SPEC_PATH, "--param", "oops", "--dry-run"]) == 2
    assert main(["discover", "--goal", SPEC_PATH, "--param", "amount=5", "--dry-run"]) == 2
    assert "error:" in capsys.readouterr().err


def test_missing_environment_variable_is_reported(monkeypatch, capsys):
    set_env(monkeypatch)
    monkeypatch.delenv("PARABANK_PASSWORD")
    monkeypatch.setattr("cua.cli.load_dotenv", lambda: None)  # do not reload the real .env
    assert main(["discover", "--goal", SPEC_PATH, *PARAMS, "--dry-run"]) == 2
    assert "PARABANK_PASSWORD" in capsys.readouterr().err


def test_the_same_account_twice_is_a_policy_block_before_anything_starts(monkeypatch, capsys):
    set_env(monkeypatch)
    started = []
    monkeypatch.setattr("cua.cli._run_live", lambda *args: started.append(args) or 0)
    same = ["--param", "from_account=acct-a", "--param", "to_account=acct-a", "--param", "amount=5"]
    assert main(["discover", "--goal", SPEC_PATH, *same]) == 2
    assert "policy block: from_account and to_account must be different" in capsys.readouterr().err
    assert started == []  # nothing was dispatched


def test_a_real_run_starts_the_live_path_with_the_parsed_arguments(monkeypatch):
    set_env(monkeypatch)
    calls = []
    monkeypatch.setattr("cua.cli._run_live", lambda *args: calls.append(args) or 0)
    assert main(["discover", "--goal", SPEC_PATH, *PARAMS, "--max-steps", "2", "--timeout", "20"]) == 0
    _spec, params, tagged, start_url, max_steps, timeout, capability_out = calls[0]
    assert params["amount"] == "5.00" and start_url == "http://h/p/index.htm"  # canonical money form
    assert (max_steps, timeout, capability_out) == (2, 20.0, None)
    assert tagged.secrets["password"] == "svc-pass-x"


def test_replay_output_is_redacted_before_printing(monkeypatch, capsys):
    # Regression: a live replay once printed real account numbers straight to the terminal —
    # discover's output was redacted, replay's was not.
    set_env(monkeypatch)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.start", lambda _self: None)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.close", lambda _self: None)

    class FakeResult:
        run_id = "x"
        escalations = ()
        status = Outcome.SUCCESS
        llm_calls = 0
        failure_detail = None
        outputs = Outputs(
            confirmation_text="$5.00 has been transferred from account #12345 to account #67890.",
            new_balance="$100.00", transaction_id="998877",
        )

        def model_dump(self, mode="json"):
            return {
                "run_id": self.run_id, "status": self.status.value, "llm_calls": self.llm_calls,
                "failure_detail": self.failure_detail, "business_outcome": None,
                "outputs": {
                    "confirmation_text": self.outputs.confirmation_text,
                    "new_balance": self.outputs.new_balance, "transaction_id": self.outputs.transaction_id,
                },
                "escalations": [],
            }

    monkeypatch.setattr("cua.replay.ReplayEngine.replay", lambda self, *a, **k: FakeResult())
    code = main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS])
    out = capsys.readouterr().out
    assert code == 0
    assert "12345" not in out and "67890" not in out and "998877" not in out
    assert "[REDACTED]" in out


def test_an_open_ticket_shows_the_operator_the_real_procedure_but_the_summary_stays_redacted(monkeypatch, capsys):
    # The saved ticket is redacted, so on its own it cannot tell the operator which account to
    # check. The terminal is the operator's own surface: real values there, nowhere persisted.
    from cua.models import Escalation

    set_env(monkeypatch)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.start", lambda _self: None)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.close", lambda _self: None)
    ticket = Escalation(
        ticket_id="t-1", run_id="r-1", reason="dispatched but unverified",
        procedure="Inputs: from_account=12345, to_account=67890, amount=5.",
    )

    class FakeResult:
        run_id = "x"
        escalations = (ticket,)
        status = Outcome.HARD_FAILURE
        llm_calls = 0
        failure_detail = None
        outputs = None

        def model_dump(self, mode="json"):
            return {
                "run_id": self.run_id, "status": self.status.value, "llm_calls": 0, "failure_detail": None,
                "business_outcome": "dispatch_unverified", "outputs": None,
                "escalations": [ticket.model_dump(mode="json")],
            }

    monkeypatch.setattr("cua.replay.ReplayEngine.replay", lambda self, *a, **k: FakeResult())
    code = main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS])
    out = capsys.readouterr().out
    assert code == 1
    assert "OPERATOR: verify before any retry" in out
    assert "from_account=12345, to_account=67890" in out  # the operator sees the truth
    summary_part = out.split("=== OPERATOR")[0]
    assert "12345" not in summary_part and "67890" not in summary_part  # the summary is still redacted


def test_replay_prints_business_outcome_when_present(monkeypatch, capsys):
    # Regression: status alone ("BUSINESS_OUTCOME") tells an operator nothing about *why* — a
    # live invalid_account run showed only that, with the classification itself never printed.
    set_env(monkeypatch)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.start", lambda _self: None)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.close", lambda _self: None)

    class FakeResult:
        run_id = "x"
        escalations = ()
        status = Outcome.BUSINESS_OUTCOME
        llm_calls = 0
        failure_detail = None
        outputs = None
        business_outcome = "invalid_account"

        def model_dump(self, mode="json"):
            return {
                "run_id": self.run_id, "status": self.status.value, "llm_calls": self.llm_calls,
                "failure_detail": self.failure_detail, "outputs": None,
                "business_outcome": self.business_outcome, "escalations": [],
            }

    monkeypatch.setattr("cua.replay.ReplayEngine.replay", lambda self, *a, **k: FakeResult())
    code = main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS])
    out = capsys.readouterr().out
    assert code == 1  # BUSINESS_OUTCOME is not SUCCESS
    assert "business_outcome: invalid_account" in out


def test_fault_flags_without_inject_faults_are_refused(monkeypatch, capsys):
    set_env(monkeypatch)
    started = []
    monkeypatch.setattr("cua.cli._run_replay", lambda *args: started.append(args) or 0)
    code = main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS,
                 "--fault-step", "0", "--fault-type", "clear_session"])
    assert code == 2
    assert "--inject-faults" in capsys.readouterr().err
    assert started == []  # nothing was dispatched


def test_inject_faults_without_both_step_and_type_is_refused(monkeypatch, capsys):
    set_env(monkeypatch)
    started = []
    monkeypatch.setattr("cua.cli._run_replay", lambda *args: started.append(args) or 0)
    code = main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS, "--inject-faults"])
    assert code == 2
    assert "--inject-faults" in capsys.readouterr().err
    assert started == []


def test_transient_fail_without_url_pattern_is_refused(monkeypatch, capsys):
    set_env(monkeypatch)
    started = []
    monkeypatch.setattr("cua.cli._run_replay", lambda *args: started.append(args) or 0)
    code = main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS,
                 "--inject-faults", "--fault-step", "3", "--fault-type", "transient_fail"])
    assert code == 2
    assert started == []


def test_a_normal_replay_call_never_constructs_a_fault(monkeypatch):
    # Grep-able proof at the code level, exercised here: the only place a FaultInjection is ever
    # built is _build_fault, and it is only reached from main() on the replay subcommand — a
    # normal invocation with none of the --fault-* flags passes fault=None through.
    set_env(monkeypatch)
    calls = []
    monkeypatch.setattr("cua.cli._run_replay", lambda *args: calls.append(args) or 0)
    assert main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS]) == 0
    _cap_path, _goal_path, _params, fault = calls[0]
    assert fault is None


def test_a_valid_fault_combination_is_passed_through_to_replay(monkeypatch):
    set_env(monkeypatch)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.start", lambda _self: None)
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.close", lambda _self: None)
    captured = {}

    class FakeResult:
        run_id = "x"
        escalations = ()
        status = Outcome.SUCCESS
        llm_calls = 0
        failure_detail = None
        outputs = Outputs(confirmation_text="ok")

        def model_dump(self, mode="json"):
            return {
                "run_id": self.run_id, "status": self.status.value, "llm_calls": self.llm_calls,
                "failure_detail": None, "business_outcome": None,
                "outputs": {"confirmation_text": "ok", "new_balance": None, "transaction_id": None},
                "escalations": [],
            }

    def fake_replay(self, capability, params, secrets, start_url, on_escalate=None, fault=None):
        captured["fault"] = fault
        return FakeResult()

    monkeypatch.setattr("cua.replay.ReplayEngine.replay", fake_replay)
    code = main(["replay", "--goal", SPEC_PATH, "--capability", CAP_PATH, *PARAMS,
                 "--inject-faults", "--fault-step", "3", "--fault-type", "transient_fail",
                 "--fault-url-pattern", "**/transfer.htm", "--fault-delay-ms", "1500"])
    assert code == 0
    fault = captured["fault"]
    assert (fault.step_index, fault.fault_type, fault.url_pattern, fault.delay_ms) == (3, "transient_fail", "**/transfer.htm", 1500)


def test_a_live_run_without_credentials_stops_before_a_browser_opens(monkeypatch, capsys):
    set_env(monkeypatch)

    def refuse(_self):
        raise LLMError("no Anthropic credentials: none are set")

    monkeypatch.setattr("cua.cli.load_dotenv", lambda: None)
    monkeypatch.setattr("cua.anthropic_llm.AnthropicLLM.check_credentials", refuse)
    opened = []
    monkeypatch.setattr("cua.adapter.PlaywrightAdapter.start", lambda _self: opened.append(1))
    assert main(["discover", "--goal", SPEC_PATH, *PARAMS]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert opened == []  # the check runs before the browser starts
