"""The Escalation Manager's ticket lifecycle: the primary trigger only (supervisor approval on
an over-threshold amount). The secondary trigger (an ambiguous post-submit state) is Phase 9, a
stretch goal, not here.

A ticket is a real JSON file on disk, written the instant the trigger fires (status "open") and
rewritten the moment a decision is given (status "resolved") — never just held in a Python
variable, so it can be inspected mid-run from outside the process.
"""

import uuid

from cua.evidence import EvidenceLogger
from cua.models import Escalation


def new_ticket_id() -> str:
    return str(uuid.uuid4())


def open_ticket(logger: EvidenceLogger | None, run_id: str, step_index: int, reason: str) -> Escalation:
    ticket = Escalation(ticket_id=new_ticket_id(), run_id=run_id, step_index=step_index, reason=reason)
    _write(logger, ticket)
    return ticket


def resolve_ticket(
    logger: EvidenceLogger | None, ticket: Escalation, decision: str, captured: list[dict[str, str]]
) -> Escalation:
    resolved = ticket.model_copy(
        update={"status": "resolved", "decision": decision, "captured_human_actions": captured}
    )
    _write(logger, resolved)
    return resolved


def _write(logger: EvidenceLogger | None, ticket: Escalation) -> None:
    if logger is not None:
        logger.write_json(f"ticket-{ticket.ticket_id}.json", ticket.model_dump(mode="json"))
