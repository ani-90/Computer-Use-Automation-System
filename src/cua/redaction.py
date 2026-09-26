"""Config-driven redaction of text and JSON-like structures."""

import re
from typing import Any

from cua.config import Config

REDACTED = "[REDACTED]"

# Our own generated correlation IDs (see CLAUDE.md: run_id is "our own correlation ID, not the
# bank's") — random UUIDs, never customer data. The generic account-number pattern (any 5-12
# digit run) can't tell a UUID segment from a real account number when that segment happens to
# be all digits, so these keys are exempt from pattern-based text redaction entirely, rather than
# risk silently corrupting the one ID meant to tie a result to its own logged trace.
_EXEMPT_KEYS = {"run_id", "ticket_id"}

# The same IDs also travel as free text — a model quoting "ticket 12345678-371f-..." to a user, a log
# sentence naming a run — where there is no key to exempt. A UUID is never an account number, so any
# well-formed UUID in text is protected from the number pattern (found live: the model's reply showed
# a ticket reference whose all-digit first segment had been masked, so it matched no ticket file).
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class Redactor:
    def __init__(self, config: Config, secrets: list[str] | None = None):
        self._patterns = [re.compile(p) for p in config.redaction_patterns.values()]
        self._keys = [k.lower() for k in config.sensitive_keys]
        # Literal secret values (e.g. the loaded password), longest first.
        self._secrets = sorted((s for s in (secrets or []) if s), key=len, reverse=True)

    def redact(self, obj: Any, *, _key: str | None = None) -> Any:
        if isinstance(obj, dict):
            return {
                k: REDACTED if self._is_sensitive(k) else self.redact(v, _key=k)
                for k, v in obj.items()
            }
        if isinstance(obj, list | tuple):
            return [self.redact(v) for v in obj]
        if isinstance(obj, str):
            if _key is not None and _key.lower() in _EXEMPT_KEYS:
                return obj
            return self._redact_text(obj)
        return obj

    def _is_sensitive(self, key: Any) -> bool:
        k = str(key).lower()
        return any(s in k for s in self._keys)

    def _redact_text(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        pieces: list[str] = []
        last = 0
        for uuid in _UUID.finditer(text):
            pieces.append(self._mask_numbers(text[last:uuid.start()]))
            pieces.append(uuid.group())  # a correlation ID: kept whole
            last = uuid.end()
        pieces.append(self._mask_numbers(text[last:]))
        return "".join(pieces)

    def _mask_numbers(self, text: str) -> str:
        for pattern in self._patterns:
            text = pattern.sub(REDACTED, text)
        return text
