"""Config-driven redaction of text and JSON-like structures."""

import re
from typing import Any

from cua.config import Config

REDACTED = "[REDACTED]"


class Redactor:
    def __init__(self, config: Config, secrets: list[str] | None = None):
        self._patterns = [re.compile(p) for p in config.redaction_patterns.values()]
        self._keys = [k.lower() for k in config.sensitive_keys]
        # Literal secret values (e.g. the loaded password), longest first.
        self._secrets = sorted((s for s in (secrets or []) if s), key=len, reverse=True)

    def redact(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: REDACTED if self._is_sensitive(k) else self.redact(v) for k, v in obj.items()
            }
        if isinstance(obj, list | tuple):
            return [self.redact(v) for v in obj]
        if isinstance(obj, str):
            return self._redact_text(obj)
        return obj

    def _is_sensitive(self, key: Any) -> bool:
        k = str(key).lower()
        return any(s in k for s in self._keys)

    def _redact_text(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        for pattern in self._patterns:
            text = pattern.sub(REDACTED, text)
        return text
