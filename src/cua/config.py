"""Runtime configuration: page allowlist, approval threshold, confirmation wait, redaction."""

from decimal import Decimal

from pydantic import BaseModel, Field


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
    # Exactly two named triggers open a ticket, and these two settings are their whole config:
    # an amount over approval_threshold (a supervisor approves it), and a dispatched
    # money-moving step whose confirmation never verifies (a human checks the ledger). Every
    # other failure returns a structured result and pages nobody.
    approval_threshold: Decimal = Decimal(100)
    # How long to wait for the confirmation after the money-moving click before treating the
    # dispatch as unverified. Overrides the artifact's own (shorter) wait for that one step.
    submit_confirmation_wait_ms: int = 60_000
    # Config-driven redaction: regexes applied to text, plus dict keys whose values are always hidden.
    redaction_patterns: dict[str, str] = Field(
        default_factory=lambda: {"account_number": r"\b\d{5,12}\b"}
    )
    sensitive_keys: list[str] = Field(
        default_factory=lambda: ["password", "api_key", "secret", "access_token", "auth_token", "authorization"]
    )
