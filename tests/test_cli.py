from pathlib import Path

from cua.cli import main
from cua.llm import LLMError

SPEC_PATH = str(Path(__file__).resolve().parent.parent / "goals" / "transfer_funds.json")
PARAMS = ["--param", "from_account=acct-a", "--param", "to_account=acct-b", "--param", "amount=5"]


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


def test_a_real_run_starts_the_live_path_with_the_parsed_arguments(monkeypatch):
    set_env(monkeypatch)
    calls = []
    monkeypatch.setattr("cua.cli._run_live", lambda *args: calls.append(args) or 0)
    assert main(["discover", "--goal", SPEC_PATH, *PARAMS, "--max-steps", "2"]) == 0
    _spec, params, tagged, start_url, max_steps = calls[0]
    assert params["amount"] == "5" and start_url == "http://h/p/index.htm"
    assert (max_steps, tagged.secrets["password"]) == (2, "svc-pass-x")


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
