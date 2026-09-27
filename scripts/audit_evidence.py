"""Audit that the evidence in evidence/ is internally consistent and traceable: every run's
correlation ID agrees with itself, with its folder, and with whatever refers to it.

    python scripts/audit_evidence.py              # everything on disk under evidence/
    python scripts/audit_evidence.py --tracked    # only what git tracks, i.e. what a reviewer sees
    python scripts/audit_evidence.py --strict     # also fail on warnings

Three kinds of folder, three rules:
  raw run      evidence/<uuid>/ (discovery) or evidence/replay/<uuid>/: the folder name IS the run_id,
               and every run_id inside every file must equal it.
  curated      evidence/by-outcome/<kind>/<OUTCOME>/: named for the outcome, so its README must name the
               run it was copied from ("Source run: `.../<uuid>`"), and every run_id inside must equal that.
  transcripts  evidence/agent_demo/*.json: any run_id they cite should resolve to a run in the repo (warning).
  artifacts    capabilities/*.json: `created_from` must name a discovery run whose evidence is present
               (an artifact that names no run is a warning: it cannot be traced back). If that discovery
               run's folder also holds a same-named copy of the artifact (a saved example artifact,
               placed in evidence/ for a reviewer), the copy must be byte-identical to the real one —
               it is never a second source of truth.

Only ids, file names and folder names are ever printed — never file contents. Exit code 0 means clean.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_UUID_RE = re.compile(rf"^{_UUID}$")
_SOURCE_RE = re.compile(rf"^\s*Source run:.*?({_UUID})", re.IGNORECASE | re.MULTILINE)
_TICKET_NAME_RE = re.compile(rf"^ticket-({_UUID})\.json$")
_REQUIRED = ("trace.json", "result.json")  # every run, discovery or replay, writes both
_MUST_CARRY_RUN_ID = ("trace.json", "result.json", "log.jsonl")


@dataclass
class FolderReport:
    path: str  # relative to the evidence dir, forward slashes
    kind: str  # raw-discovery | raw-replay | curated | artifact
    expected: str | None = None
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _find_run_ids(node) -> list[str]:
    """Every value stored under a "run_id" key, at any depth."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run_id" and isinstance(value, str):
                found.append(value)
            else:
                found += _find_run_ids(value)
    elif isinstance(node, list):
        for item in node:
            found += _find_run_ids(item)
    return found


def _read_json_values(path: Path) -> tuple[list, str | None]:
    """The parsed JSON documents in a .json (one) or .jsonl (one per line) file."""
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            return [json.loads(line) for line in text.splitlines() if line.strip()], None
        return [json.loads(text)], None
    except (OSError, ValueError) as e:
        return [], f"{path.name}: not valid JSON ({type(e).__name__})"


def _classify(parts: tuple[str, ...]) -> str | None:
    if len(parts) == 1 and _UUID_RE.match(parts[0]):
        return "raw-discovery"
    if len(parts) == 2 and parts[0] == "replay":
        return "raw-replay"
    if len(parts) == 3 and parts[0] == "by-outcome":
        return "curated"
    return None


def _audit_folder(evidence: Path, rel: tuple[str, ...], kind: str, files: list[Path]) -> FolderReport:
    report = FolderReport(path="/".join(rel), kind=kind)
    names = {f.name for f in files}
    directory = files[0].parent

    if kind == "curated":
        readme = directory / "README.md"
        match = _SOURCE_RE.search(readme.read_text(encoding="utf-8")) if readme.exists() else None
        if not readme.exists():
            report.problems.append("README.md missing: nothing says which run this folder was copied from")
        elif match is None:
            report.problems.append("README.md has no 'Source run:' line naming a run id")
        else:
            report.expected = match.group(1)
    else:
        report.expected = rel[-1]
        if not _UUID_RE.match(rel[-1]):
            report.problems.append("folder name is not a run id (uuid)")

    for required in _REQUIRED:
        if required not in names:
            report.problems.append(f"{required} missing: incomplete run")

    for path in sorted(files):
        if path.suffix not in (".json", ".jsonl"):
            continue
        documents, error = _read_json_values(path)
        if error:
            report.problems.append(error)
            continue
        ids = [i for doc in documents for i in _find_run_ids(doc)]
        if not ids and path.name in _MUST_CARRY_RUN_ID:
            report.problems.append(f"{path.name}: carries no run_id")
        if report.expected is not None:
            for wrong in sorted({i for i in ids if i != report.expected}):
                report.problems.append(f"{path.name}: run_id {wrong} != expected {report.expected}")

        ticket = _TICKET_NAME_RE.match(path.name)
        if ticket and documents:
            stored = documents[0].get("ticket_id") if isinstance(documents[0], dict) else None
            if stored != ticket.group(1):
                report.problems.append(f"{path.name}: file name and its ticket_id disagree")

    if kind == "curated" and report.expected is not None:
        source = evidence / ("replay" if rel[1] == "replay" else ".") / report.expected
        if source.is_dir():
            for path in sorted(files):
                twin = source / path.name
                if path.name != "README.md" and twin.is_file() and twin.read_bytes() != path.read_bytes():
                    report.warnings.append(f"{path.name}: differs from its source run (README says 'copied in full')")
    return report


def _group_by_folder(evidence: Path, files: list[Path]) -> dict[tuple[str, ...], list[Path]]:
    grouped: dict[tuple[str, ...], list[Path]] = {}
    for path in files:
        grouped.setdefault(path.parent.relative_to(evidence).parts, []).append(path)
    return grouped


def _audit_artifacts(evidence: Path, capabilities: Path, reports: list[FolderReport]) -> list[FolderReport]:
    """Each compiled artifact must be traceable to the discovery run that produced it, and if a copy of
    it was saved alongside that run's evidence (for a reviewer), the copy must match exactly."""
    discovery_runs = {
        r.expected for r in reports
        if r.expected and (r.kind == "raw-discovery" or r.path.startswith("by-outcome/discovery/"))
    }
    folder_by_run = {
        r.expected: r.path for r in reports
        if r.expected and (r.kind == "raw-discovery" or r.path.startswith("by-outcome/discovery/"))
    }
    out: list[FolderReport] = []
    for path in sorted(capabilities.glob("*.json")):
        report = FolderReport(path=f"capabilities/{path.name}", kind="artifact")
        documents, error = _read_json_values(path)
        if error:
            report.problems.append(error)
        else:
            source = documents[0].get("created_from") if isinstance(documents[0], dict) else None
            report.expected = source
            if source is None:
                report.warnings.append("no created_from: this artifact cannot be traced to a discovery run")
            elif not _UUID_RE.match(str(source)):
                report.problems.append("created_from is not a run id (uuid)")
            elif source not in discovery_runs:
                report.problems.append(f"created_from {source} names a discovery run with no evidence in scope")
            elif source in folder_by_run:
                copy = evidence / folder_by_run[source] / path.name
                if copy.is_file() and copy.read_bytes() != path.read_bytes():
                    report.problems.append(
                        f"{copy.relative_to(evidence)} differs from the real artifact — it is a saved copy, "
                        "never a second source of truth"
                    )
        out.append(report)
    return out


def audit(
    evidence: Path, files: list[Path] | None = None, capabilities: Path | None = None
) -> tuple[list[FolderReport], list[str]]:
    """Returns (one report per run folder or artifact, global warnings)."""
    if files is None:
        files = [p for p in evidence.rglob("*") if p.is_file()]
    grouped = _group_by_folder(evidence, files)

    reports: list[FolderReport] = []
    warnings: list[str] = []
    for rel, folder_files in sorted(grouped.items()):
        if not rel or rel[0] == "agent_demo":
            continue
        kind = _classify(rel)
        has_json = any(f.suffix in (".json", ".jsonl") for f in folder_files)
        if kind is None:
            if has_json:  # a container README is fine; run data in an unrecognized place is not
                warnings.append(f"{'/'.join(rel)}: JSON evidence in an unrecognized location")
            continue
        reports.append(_audit_folder(evidence, rel, kind, folder_files))

    if capabilities is not None and capabilities.is_dir():
        reports += _audit_artifacts(evidence, capabilities, reports)

    known = {r.expected for r in reports if r.expected and r.kind != "artifact"}
    for path in sorted(p for p in files if p.parent.relative_to(evidence).parts[:1] == ("agent_demo",)):
        if path.suffix != ".json":
            continue
        documents, error = _read_json_values(path)
        if error:
            warnings.append(f"agent_demo/{error}")
            continue
        for cited in sorted({i for d in documents for i in _find_run_ids(d)}):
            if cited not in known:
                warnings.append(f"agent_demo/{path.name}: cites run_id {cited}, which no run folder in scope has")
    return reports, warnings


def _tracked_files(evidence: Path) -> list[Path]:
    root = Path(
        subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True,
                       cwd=evidence).stdout.strip()
    )
    listed = subprocess.run(["git", "ls-files", "-z", "--", str(evidence)], capture_output=True, text=True,
                            check=True, cwd=root).stdout
    return [root / name for name in listed.split("\0") if name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit run_id consistency across evidence/.")
    parser.add_argument("--evidence-dir", default="evidence")
    parser.add_argument("--tracked", action="store_true", help="only files git tracks (what a reviewer sees)")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    evidence = Path(args.evidence_dir).resolve()
    if not evidence.is_dir():
        print(f"error: {evidence} is not a directory", file=sys.stderr)
        return 2
    reports, warnings = audit(
        evidence, _tracked_files(evidence) if args.tracked else None, capabilities=evidence.parent / "capabilities"
    )

    failed = 0
    for report in reports:
        label = "FAIL" if report.problems else "PASS"
        failed += bool(report.problems)
        print(f"{label}  {report.kind:<14} {report.path}")
        for line in report.problems:
            print(f"        - {line}")
        for line in report.warnings:
            warnings.append(f"{report.path}: {line}")
    for line in warnings:
        print(f"WARN  {line}")

    scope = "tracked by git" if args.tracked else "on disk"
    print(f"\n{len(reports)} run folder(s)/artifact(s) audited ({scope}): {len(reports) - failed} clean, {failed} with problems, "
          f"{len(warnings)} warning(s).")
    return 1 if failed or (args.strict and warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
