from pathlib import Path

from cua.cli import main

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


def test_a_real_run_is_not_wired_yet(monkeypatch):
    set_env(monkeypatch)
    assert main(["discover", "--goal", SPEC_PATH, *PARAMS]) == 2
