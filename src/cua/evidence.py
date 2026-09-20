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
        safe_data = self._redactor.redact(json.loads(json.dumps(data, default=str)))
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "event": event,
            "data": safe_data,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
