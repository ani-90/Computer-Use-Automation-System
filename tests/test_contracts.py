import json
import uuid

import pytest
from pydantic import ValidationError

from cua.classifier import classify
from cua.config import Config
from cua.enums import Outcome
from cua.evidence import EvidenceLogger, new_run_id
from cua.models import Capability, Condition, Locator, LocatorCandidate, Step, WaitStrategy
from cua.redaction import Redactor

SECRET = "not-a-real-secret"


def _locator() -> Locator:
    return Locator(
        description="Transfer button",
        chain=[LocatorCandidate(strategy="role_name", role="button", value="Transfer")],
    )


def _step(target) -> dict:
    return {
        "precondition": [],
        "action": "click",
        "target": target,
        "parameters": {},
        "wait_strategy": WaitStrategy(kind="network_idle"),
        "checkpoint": [],
        "error_mapping": [],
    }


def test_redaction_strips_secrets_and_digit_runs():
    r = Redactor(Config(), secrets=[SECRET])
    out = r.redact({"password": "x", "note": f"pw {SECRET} acct {'7' * 7}", "n": [1, "ok"]})
    assert out["password"] == "[REDACTED]"
    assert SECRET not in out["note"] and "7777777" not in out["note"]
    assert out["n"] == [1, "ok"]


def test_evidence_log_is_redacted_and_run_id_survives(tmp_path):
    run_id = new_run_id()
    uuid.UUID(run_id)
    log = EvidenceLogger(run_id, Redactor(Config(), secrets=[SECRET]), base_dir=tmp_path)
    log.log("login", password=SECRET, detail=f"typed {SECRET} acct {'7' * 7}")
    text = (tmp_path / run_id / "log.jsonl").read_text(encoding="utf-8")
    assert SECRET not in text and "7777777" not in text
    assert json.loads(text)["run_id"] == run_id


def test_run_id_with_an_all_digit_uuid_segment_is_never_mangled():
    # Regression: a random run_id occasionally has a UUID segment that's entirely digits (e.g.
    # "27288655"), which the account-number pattern (\b\d{5,12}\b) would otherwise redact,
    # corrupting the one ID meant to correlate a result with its own logged trace — this was
    # found live, not hypothetical.
    run_id = "27288655-d03c-4b10-b4e4-0a6da3f53b61"
    out = Redactor(Config()).redact({"run_id": run_id, "note": "acct 1234567"})
    assert out["run_id"] == run_id
    assert out["note"] == "acct [REDACTED]"  # the pattern still applies to everything else


def test_ticket_id_is_also_exempt_same_reasoning_as_run_id():
    ticket_id = "12345678-90ab-cdef-1234-567890abcdef"
    out = Redactor(Config()).redact({"ticket_id": ticket_id})
    assert out["ticket_id"] == ticket_id


def test_write_json_protects_run_id_not_just_the_log_method(tmp_path):
    # The bug specifically hit write_json (trace.json/result.json), not log() (log.jsonl), which
    # already had its own separate, narrower workaround (see evidence.py). This proves the fix
    # covers the path that actually broke, not just the one that happened to already work.
    run_id = "12345678-1234-1234-1234-123456789012"
    logger = EvidenceLogger(run_id, Redactor(Config()), base_dir=tmp_path)
    logger.write_json("trace.json", {"run_id": run_id, "steps": []})
    saved = json.loads((tmp_path / run_id / "trace.json").read_text(encoding="utf-8"))
    assert saved["run_id"] == run_id


def test_unmapped_condition_is_hard_failure():
    assert classify("something_new") == Outcome.HARD_FAILURE
    assert classify(None) == Outcome.HARD_FAILURE
    assert classify("session_expired") == Outcome.RECOVERABLE


def test_step_rejects_raw_selector_target():
    with pytest.raises(ValidationError):
        Step(**_step("#transferForm input"))
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="css", value="#x")


def test_condition_requires_its_target_and_value():
    with pytest.raises(ValidationError):
        Condition(kind="element_visible")  # needs a target
    with pytest.raises(ValidationError):
        Condition(kind="text_equals", target=_locator())  # needs a value
    with pytest.raises(ValidationError):
        Condition(kind="url_matches")  # needs a value
    Condition(kind="text_equals", target=_locator(), value="{{from_account}}")


def test_locator_candidate_field_rules():
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="role_name", value="Transfer")  # needs a role
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="label")  # needs a value
    LocatorCandidate(strategy="role_name", role="textbox", nth=0)  # role-only match is fine


def test_new_condition_kinds_require_their_fields():
    with pytest.raises(ValidationError):
        Condition(kind="field_filled")  # needs a target
    with pytest.raises(ValidationError):
        Condition(kind="shape_matches", target=_locator())  # needs a shape name
    Condition(kind="field_filled", target=_locator())
    Condition(kind="field_value_equals", target=_locator(), value="{{amount}}")


def test_table_cell_locator_field_rules():
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="table_cell", column="Balance*")  # needs an anchor value
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="table_cell", value="row")  # needs a column or a position
    with pytest.raises(ValidationError):
        LocatorCandidate(strategy="table_cell", value="row", column="Balance*", col=1)
    LocatorCandidate(strategy="table_cell", value="{{from_account}}", column="Balance*")
    LocatorCandidate(strategy="table_cell", value="Transaction ID:", col=1)


def test_capability_round_trips_through_json():
    cap = Capability(
        schema_version="1",
        version="1.0.0",
        name="transfer_funds",
        inputs={"amount": {"type": "decimal"}},
        outputs={"transaction_id": {"type": "string", "required": False}},
        steps=[Step(**_step(_locator()))],
    )
    assert Capability.model_validate_json(cap.model_dump_json()) == cap
