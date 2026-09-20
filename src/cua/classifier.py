"""Outcome classifier. Unmapped conditions default to HARD_FAILURE."""

from cua.enums import Outcome

DEFAULT_MAPPING: dict[str, Outcome] = {
    "checkpoint_passed": Outcome.SUCCESS,
    "invalid_account": Outcome.BUSINESS_OUTCOME,
    "login_rejected": Outcome.BUSINESS_OUTCOME,
    "session_expired": Outcome.RECOVERABLE,
    "transient_error": Outcome.RECOVERABLE,
    "policy_block": Outcome.POLICY_BLOCK,
}


def classify(condition: str | None, mapping: dict[str, Outcome] | None = None) -> Outcome:
    table = DEFAULT_MAPPING if mapping is None else mapping
    if condition is None:
        return Outcome.HARD_FAILURE
    return table.get(condition, Outcome.HARD_FAILURE)
