"""scripts/audit_evidence.py: the checker an evaluator can run to confirm every committed run's
correlation ID agrees with its folder, its own files, and whatever cites it.

A checker that has never been shown to fail proves nothing, so most of these tests build a tree
with one specific defect and assert the audit names it.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "audit_evidence.py"
_spec = importlib.util.spec_from_file_location("audit_evidence", _SCRIPT)
audit_mod = importlib.util.module_from_spec(_spec)
sys.modules["audit_evidence"] = audit_mod
_spec.loader.exec_module(audit_mod)

RUN = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
TICKET = "33333333-3333-4333-8333-333333333333"


def write_run(folder: Path, run_id: str = RUN, *, log_id: str | None = None, ticket: str | None = None) -> Path:
    folder.mkdir(parents=True)
    (folder / "trace.json").write_text(json.dumps({"run_id": run_id, "steps": []}), encoding="utf-8")
    (folder / "result.json").write_text(
        json.dumps({"run_id": run_id, "status": "SUCCESS", "escalations": []}), encoding="utf-8"
    )
    (folder / "log.jsonl").write_text(
        json.dumps({"run_id": log_id or run_id, "event": "act"}) + "\n", encoding="utf-8"
    )
    if ticket:
        (folder / f"ticket-{ticket}.json").write_text(
            json.dumps({"ticket_id": ticket, "run_id": run_id, "status": "open"}), encoding="utf-8"
        )
    return folder


def curated(evidence: Path, outcome: str, run_id: str, readme: str | None = None) -> Path:
    folder = write_run(evidence / "by-outcome" / "replay" / outcome, run_id)
    if readme is None:
        readme = f"# {outcome}\n\nSource run: `evidence/replay/{run_id}` (copied here in full).\n"
    (folder / "README.md").write_text(readme, encoding="utf-8")
    return folder


def by_path(reports):
    return {r.path: r for r in reports}


def test_a_consistent_raw_replay_run_passes(tmp_path):
    write_run(tmp_path / "replay" / RUN, ticket=TICKET)
    reports, warnings = audit_mod.audit(tmp_path)
    assert [r.problems for r in reports] == [[]]
    assert warnings == []


def test_a_consistent_raw_discovery_run_passes(tmp_path):
    write_run(tmp_path / RUN)
    (report,) = audit_mod.audit(tmp_path)[0]
    assert report.kind == "raw-discovery" and report.problems == []


def test_a_run_id_that_differs_from_the_folder_name_is_named(tmp_path):
    # The exact defect found live: trace/result carried a different id than their own folder.
    write_run(tmp_path / "replay" / RUN, run_id=OTHER)
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("trace.json" in p and OTHER in p for p in report.problems)
    assert any("result.json" in p and OTHER in p for p in report.problems)


def test_a_single_file_that_disagrees_is_caught_even_when_the_rest_agree(tmp_path):
    write_run(tmp_path / "replay" / RUN, log_id=OTHER)
    (report,) = audit_mod.audit(tmp_path)[0]
    assert len(report.problems) == 1 and "log.jsonl" in report.problems[0]


def test_a_ticket_carrying_another_runs_id_is_caught(tmp_path):
    folder = write_run(tmp_path / "replay" / RUN, ticket=TICKET)
    (folder / f"ticket-{TICKET}.json").write_text(
        json.dumps({"ticket_id": TICKET, "run_id": OTHER}), encoding="utf-8"
    )
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any(f"ticket-{TICKET}.json" in p for p in report.problems)


def test_a_ticket_whose_file_name_and_ticket_id_disagree_is_caught(tmp_path):
    folder = write_run(tmp_path / "replay" / RUN)
    (folder / f"ticket-{TICKET}.json").write_text(
        json.dumps({"ticket_id": OTHER, "run_id": RUN}), encoding="utf-8"
    )
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("ticket_id disagree" in p for p in report.problems)


def test_a_run_id_nested_inside_a_result_is_checked_too(tmp_path):
    folder = write_run(tmp_path / "replay" / RUN)
    (folder / "result.json").write_text(
        json.dumps({"run_id": RUN, "escalations": [{"run_id": OTHER}]}), encoding="utf-8"
    )
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("result.json" in p and OTHER in p for p in report.problems)


def test_a_partly_redacted_run_id_is_a_mismatch(tmp_path):
    # An older redaction bug masked a digit-only UUID segment as if it were an account number.
    write_run(tmp_path / "replay" / RUN, run_id="[REDACTED]-1914-4043-95e8-1bc8f5b29b28")
    (report,) = audit_mod.audit(tmp_path)[0]
    assert report.problems


def test_an_incomplete_run_is_a_problem(tmp_path):
    folder = write_run(tmp_path / "replay" / RUN)
    (folder / "result.json").unlink()
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("result.json missing" in p for p in report.problems)


def test_a_file_that_carries_no_run_id_is_a_problem(tmp_path):
    folder = write_run(tmp_path / "replay" / RUN)
    (folder / "trace.json").write_text(json.dumps({"steps": []}), encoding="utf-8")
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("trace.json" in p and "no run_id" in p for p in report.problems)


def test_invalid_json_is_reported_not_crashed_on(tmp_path):
    folder = write_run(tmp_path / "replay" / RUN)
    (folder / "trace.json").write_text("{not json", encoding="utf-8")
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("not valid JSON" in p for p in report.problems)


def test_a_folder_that_is_not_named_by_a_run_id_is_a_problem(tmp_path):
    write_run(tmp_path / "replay" / "some-name")
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("not a run id" in p for p in report.problems)


def test_a_curated_folder_passes_when_its_readme_source_matches_every_file(tmp_path):
    curated(tmp_path, "SUCCESS", RUN)
    (report,) = audit_mod.audit(tmp_path)[0]
    assert report.kind == "curated" and report.expected == RUN and report.problems == []


def test_a_curated_folder_whose_files_disagree_with_its_readme_source_fails(tmp_path):
    curated(tmp_path, "SUCCESS", RUN, readme=f"Source run: `evidence/replay/{OTHER}` (copied here in full).\n")
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("trace.json" in p for p in report.problems)


def test_a_curated_folder_without_a_readme_cannot_be_traced(tmp_path):
    folder = curated(tmp_path, "SUCCESS", RUN)
    (folder / "README.md").unlink()
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("README.md missing" in p for p in report.problems)


def test_a_curated_readme_that_names_no_source_run_fails(tmp_path):
    curated(tmp_path, "SUCCESS", RUN, readme="# SUCCESS\n\nA nice run.\n")
    (report,) = audit_mod.audit(tmp_path)[0]
    assert any("no 'Source run:'" in p for p in report.problems)


def test_a_curated_file_edited_after_copying_is_only_a_warning(tmp_path):
    curated(tmp_path, "SUCCESS", RUN)
    write_run(tmp_path / "replay" / RUN)
    (tmp_path / "by-outcome" / "replay" / "SUCCESS" / "result.json").write_text(
        json.dumps({"run_id": RUN, "status": "EDITED"}), encoding="utf-8"
    )
    reports, _ = audit_mod.audit(tmp_path)
    curated_report = by_path(reports)["by-outcome/replay/SUCCESS"]
    assert curated_report.problems == []
    assert any("differs from its source run" in w for w in curated_report.warnings)


def test_a_transcript_citing_a_run_that_is_not_in_scope_is_a_warning(tmp_path):
    write_run(tmp_path / "replay" / RUN)
    (tmp_path / "agent_demo").mkdir()
    (tmp_path / "agent_demo" / "transcript-1.json").write_text(
        json.dumps({"invoke_response": {"run_id": OTHER}}), encoding="utf-8"
    )
    reports, warnings = audit_mod.audit(tmp_path)
    assert [r.problems for r in reports] == [[]]
    assert any(OTHER in w for w in warnings)


def test_a_transcript_citing_a_run_that_is_present_is_clean(tmp_path):
    write_run(tmp_path / "replay" / RUN)
    (tmp_path / "agent_demo").mkdir()
    (tmp_path / "agent_demo" / "transcript-1.json").write_text(
        json.dumps({"invoke_response": {"run_id": RUN}}), encoding="utf-8"
    )
    assert audit_mod.audit(tmp_path)[1] == []


def test_a_container_readme_alone_is_not_mistaken_for_a_run(tmp_path):
    (tmp_path / "by-outcome" / "replay").mkdir(parents=True)
    (tmp_path / "by-outcome" / "replay" / "README.md").write_text("# index\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# evidence\n", encoding="utf-8")
    assert audit_mod.audit(tmp_path) == ([], [])


def test_the_audit_only_looks_at_the_files_it_is_given(tmp_path):
    write_run(tmp_path / "replay" / RUN, run_id=OTHER)  # broken, but not "tracked"
    good = write_run(tmp_path / "replay" / OTHER, run_id=OTHER)
    reports, _ = audit_mod.audit(tmp_path, [p for p in good.iterdir()])
    assert [r.problems for r in reports] == [[]]


@pytest.mark.parametrize("defect,expected_code", [(False, 0), (True, 1)])
def test_the_exit_code_reflects_the_result(tmp_path, capsys, defect, expected_code):
    write_run(tmp_path / "replay" / RUN, run_id=OTHER if defect else RUN)
    code = audit_mod.main(["--evidence-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == expected_code
    assert ("FAIL" in out) == defect


def test_strict_turns_a_warning_into_a_failure(tmp_path):
    write_run(tmp_path / "replay" / RUN)
    (tmp_path / "agent_demo").mkdir()
    (tmp_path / "agent_demo" / "t.json").write_text(json.dumps({"run_id": OTHER}), encoding="utf-8")
    assert audit_mod.main(["--evidence-dir", str(tmp_path)]) == 0
    assert audit_mod.main(["--evidence-dir", str(tmp_path), "--strict"]) == 1


def test_the_report_never_prints_file_contents(tmp_path, capsys):
    folder = write_run(tmp_path / "replay" / RUN, run_id=OTHER)
    (folder / "result.json").write_text(
        json.dumps({"run_id": OTHER, "secret_field": "hunter2-do-not-print"}), encoding="utf-8"
    )
    audit_mod.main(["--evidence-dir", str(tmp_path)])
    assert "hunter2-do-not-print" not in capsys.readouterr().out
