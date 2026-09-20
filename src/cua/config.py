"""Runtime configuration: page allowlist, approval threshold, escalation policy, redaction."""

from decimal import Decimal

from pydantic import BaseModel, Field

from cua.enums import Outcome


class Config(BaseModel):
    # Paths only the flow needs. The denylist always wins over the allowlist.
    allowlist: list[str] = Field(
        default_factory=lambda: [
            "/parabank/index.htm",
            "/parabank/overview.htm",
            "/parabank/transfer.htm",
            "/parabank/findtrans.htm",
            "/parabank/transaction.htm",
        ]
    )
    denylist: list[str] = Field(
        default_factory=lambda: ["/parabank/services*", "/parabank/admin.htm"]
    )
    approval_threshold: Decimal = Decimal(100)
    # Classification is separate from escalation: which outcomes open a ticket.
    escalation_policy: dict[Outcome, bool] = Field(
        default_factory=lambda: {
            Outcome.SUCCESS: False,
            Outcome.BUSINESS_OUTCOME: False,
            Outcome.RECOVERABLE: False,
            Outcome.HARD_FAILURE: True,
            Outcome.POLICY_BLOCK: False,
        }
    )
    # Config-driven redaction: regexes applied to text, plus dict keys whose values are always hidden.
    redaction_patterns: dict[str, str] = Field(
        default_factory=lambda: {"account_number": r"\b\d{5,12}\b"}
    )
    sensitive_keys: list[str] = Field(
        default_factory=lambda: ["password", "api_key", "secret", "access_token", "auth_token", "authorization"]
    )
