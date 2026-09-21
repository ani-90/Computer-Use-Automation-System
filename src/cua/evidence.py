"""Structured JSON evidence logger, keyed by a system-generated run_id."""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cua.redaction import Redactor


def new_run_id() -> str:
    return str(uuid.uuid4())


class EvidenceLogger:
    def __init__(self, run_id: str, redactor: Redactor, base_dir: Path = Path("evidence")):
        self.run_id = run_id
        self._redactor = redactor
        self.dir = base_dir / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "log.jsonl"

    def log(self, event: str, **data: Any) -> None:
        # Redact only `data`: run_id must stay intact so it still matches the folder and result.
        # default=str covers Decimal, enums and datetimes before the redactor scans them.
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "event": event,
            "data": self._clean(data),
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def write_json(self, name: str, data: Any) -> None:
        (self.dir / name).write_text(json.dumps(self._clean(data), indent=2), encoding="utf-8")

    def append_jsonl(self, name: str, data: Any) -> None:
        with (self.dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(self._clean(data)) + "\n")

    def write_bytes(self, name: str, data: bytes) -> None:
        """Only for images that are already masked: bytes are written as they are."""
        (self.dir / name).write_bytes(data)

    def _clean(self, data: Any) -> Any:
        # default=str covers Decimal, enums and datetimes before the redactor scans them.
        return self._redactor.redact(json.loads(json.dumps(data, default=str)))
