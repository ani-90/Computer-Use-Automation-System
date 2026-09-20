"""Shared enums: outcomes, discovery stop reasons, session ownership, gate verdicts."""

from enum import StrEnum


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    RECOVERABLE = "RECOVERABLE"
    HARD_FAILURE = "HARD_FAILURE"
    POLICY_BLOCK = "POLICY_BLOCK"


class StopReason(StrEnum):
    SUCCESS = "SUCCESS"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    DEAD_END = "DEAD_END"


class SessionOwner(StrEnum):
    AGENT = "agent"
    HUMAN = "human"


class Verdict(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"
