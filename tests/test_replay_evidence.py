"""Replay writes the same kind of evidence Discovery does: trace.json, screenshots, result.json —
and account numbers are still redacted at the write boundary, exactly like every other run.
"""

import json

from cua.config import Config
from cua.enums import Outcome
from cua.evidence import EvidenceLogger, new_run_id
from cua.policy_gate import PolicyGate
from cua.redaction import Redactor
from cua.replay import ReplayEngine
from tests.test_replay import BASE, FROM, PARAMS, SECRETS, TO, FakeBank, capability


def test_a_successful_replay_writes_trace_screenshots_and_a_redacted_result(tmp_path):
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    fake = FakeBank()
    engine = ReplayEngine(fake, PolicyGate(Config(approval_threshold=1000)), logger)

    result = engine.replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.SUCCESS

    files = {p.name for p in logger.dir.iterdir()}
    assert "trace.json" in files
    assert "result.json" in files
    assert any(f.startswith("prelude-") and f.endswith(".png") for f in files)
    assert any(f.startswith("step-") and f.endswith(".png") for f in files)

    trace = json.loads((logger.dir / "trace.json").read_text(encoding="utf-8"))
    result_json = json.loads((logger.dir / "result.json").read_text(encoding="utf-8"))
    # Regression: found live. trace["run_id"] and result.run_id always agreed with each other
    # (both come from the one variable replay() generates internally) — that alone never proved
    # anything, since the real bug was that this internally-generated run_id was a completely
    # different UUID from logger.run_id, the one the evidence folder is actually named after.
    # Every one of these must be the exact same value, not just internally self-consistent.
    assert result.run_id == logger.run_id
    assert trace["run_id"] == logger.run_id
    assert result_json["run_id"] == logger.run_id
    assert logger.dir.name == logger.run_id
    assert len(trace["steps"]) == 3 + 15  # 3 prelude steps + 15 compiled steps
    assert all(s["result"] == "ok" for s in trace["steps"])

    # the account numbers must never appear in cleartext, anywhere in what was written
    dumped = (logger.dir / "trace.json").read_text(encoding="utf-8")
    dumped += (logger.dir / "result.json").read_text(encoding="utf-8")
    assert FROM not in dumped
    assert TO not in dumped
    assert SECRETS["password"] not in dumped


def test_a_failed_step_still_gets_a_screenshot_and_an_honest_trace(tmp_path):
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    fake = FakeBank()
    bad_secrets = {"username": "wrong", "password": "wrong"}
    engine = ReplayEngine(fake, PolicyGate(Config()), logger)

    result = engine.replay(capability(), PARAMS, bad_secrets, BASE + "/index.htm")
    # Classified (login_rejected, Phase 6) rather than a generic hard failure — but the trace and
    # screenshot must still be written honestly regardless of how the failure was classified.
    assert result.status == Outcome.BUSINESS_OUTCOME

    trace = json.loads((logger.dir / "trace.json").read_text(encoding="utf-8"))
    last = trace["steps"][-1]
    assert last["result"] == "error"
    assert last["screenshot"] is not None
    assert (logger.dir / last["screenshot"]).exists()


def test_no_logger_at_all_still_replays_fine_only_evidence_is_skipped():
    fake = FakeBank()
    engine = ReplayEngine(fake, PolicyGate(Config(approval_threshold=1000)))  # logger defaults to None
    result = engine.replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.SUCCESS
