"""Policy Gate: pure decision logic, run before every action in Discovery and Replay."""

import fnmatch
import posixpath
from decimal import Decimal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel

from cua.config import Config
from cua.enums import Verdict


class Decision(BaseModel):
    verdict: Verdict
    reason: str


class PolicyGate:
    def __init__(self, config: Config):
        self._config = config

    def check(
        self,
        url: str,
        amount: Decimal | None = None,
        balance: Decimal | None = None,
    ) -> Decision:
        path = self._normalize(url)
        if any(fnmatch.fnmatchcase(path, p) for p in self._config.denylist):
            return Decision(verdict=Verdict.BLOCK, reason=f"path denied: {path}")
        if not any(fnmatch.fnmatchcase(path, p) for p in self._config.allowlist):
            return Decision(verdict=Verdict.BLOCK, reason=f"path not allowlisted: {path}")

        if amount is None:
            return Decision(verdict=Verdict.ALLOW, reason="no amount involved")
        if amount <= 0:
            return Decision(verdict=Verdict.BLOCK, reason="amount must be greater than zero")
        if balance is None:
            return Decision(verdict=Verdict.BLOCK, reason="balance unknown; failing closed")
        if amount > balance:
            return Decision(verdict=Verdict.BLOCK, reason="amount exceeds balance")
        if amount > self._config.approval_threshold:
            return Decision(verdict=Verdict.ESCALATE, reason="amount exceeds approval threshold")
        return Decision(verdict=Verdict.ALLOW, reason="within policy")

    @staticmethod
    def _normalize(url: str) -> str:
        # Decode and collapse "..", "//" so a crafted path cannot dodge the denylist.
        path = posixpath.normpath(unquote(urlparse(url).path)).lower()
        return path if path.startswith("/") else "/" + path
