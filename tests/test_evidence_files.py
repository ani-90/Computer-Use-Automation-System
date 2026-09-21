import json

from cua.config import Config
from cua.evidence import EvidenceLogger, new_run_id
from cua.redaction import Redactor

SECRET = "not-a-real-secret"


def logger(tmp_path) -> EvidenceLogger:
    return EvidenceLogger(new_run_id(), Redactor(Config(), secrets=[SECRET]), base_dir=tmp_path)


def test_write_json_redacts(tmp_path):
    log = logger(tmp_path)
    log.write_json("trace.json", {"note": f"pw {SECRET} acct {'7' * 7}", "password": "x"})
    text = (log.dir / "trace.json").read_text(encoding="utf-8")
    assert SECRET not in text and "7777777" not in text
    assert json.loads(text)["password"] == "[REDACTED]"


def test_append_jsonl_adds_one_redacted_line_per_call(tmp_path):
    log = logger(tmp_path)
    log.append_jsonl("transcript.jsonl", {"text": f"first {SECRET}"})
    log.append_jsonl("transcript.jsonl", {"text": "second"})
    lines = (log.dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and SECRET not in lines[0]


def test_write_bytes_writes_exactly_what_it_is_given(tmp_path):
    log = logger(tmp_path)
    log.write_bytes("step-01.png", b"\x89PNG-masked")
    assert (log.dir / "step-01.png").read_bytes() == b"\x89PNG-masked"
