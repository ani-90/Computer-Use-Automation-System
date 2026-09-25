"""Once the money-moving click is dispatched, the engine never clicks it again.

If the confirmation never appears the engine cannot know whether the transfer posted, so it stops,
records a durable ticket for a human, and reports HARD_FAILURE with business_outcome
"dispatch_unverified". No session probe, no re-login, no retry — those recovery paths would
re-execute an irreversible step.
"""

import json
from decimal import Decimal

from cua.adapter import ActionFailed, LocatorNotFound
from cua.config import Config
from cua.enums import Outcome
from cua.evidence import EvidenceLogger, new_run_id
from cua.policy_gate import PolicyGate
from cua.redaction import Redactor
from cua.replay import ReplayEngine
from cua.tool_interface import _for_caller
from tests.test_replay import BASE, FROM, PARAMS, SECRETS, FakeBank, capability


class DroppedConfirmationBank(FakeBank):
    """The Transfer click reaches the server (money_moved) but the page never shows the
    confirmation — a lost response. Optionally the session also expires at that moment."""

    def __init__(self, expire_on_click: bool = False):
        super().__init__()
        self.transfer_clicks = 0
        self.logins = 0
        self.money_moved = False
        self.expire_on_click = expire_on_click

    def act(self, handle, action):
        kind, key = handle.key
        if kind == "field" and action.kind == "click":
            if key == "Log In":
                self.logins += 1
            elif key == "Transfer":
                self.transfer_clicks += 1
                self.money_moved = True
                if self.expire_on_click:
                    self.session_expired = True
                return None  # the response is lost: the page never changes
        return super().act(handle, action)


class ClickRaisesBank(FakeBank):
    """The Transfer click reaches the server, then the browser raises (e.g. a timeout after the
    request was already sent) — the click's own error, not a missing confirmation."""

    def __init__(self):
        super().__init__()
        self.transfer_clicks = 0

    def act(self, handle, action):
        kind, key = handle.key
        if kind == "field" and action.kind == "click" and key == "Transfer":
            self.transfer_clicks += 1
            raise ActionFailed("Timeout 30000ms exceeded while waiting for navigation")
        return super().act(handle, action)


class NoTransferButtonBank(FakeBank):
    """The Transfer button cannot be found at all: provably nothing was clicked."""

    def resolve(self, locator):
        if locator.description == 'button "Transfer"':
            raise LocatorNotFound(locator.description)
        return super().resolve(locator)


def engine(fake, logger=None, wait_ms=50):
    config = Config(approval_threshold=Decimal(1000), submit_confirmation_wait_ms=wait_ms)
    return ReplayEngine(fake, PolicyGate(config), logger)


def test_a_missing_confirmation_is_an_unverified_dispatch_with_a_ticket_and_one_click(tmp_path):
    redactor = Redactor(Config(), secrets=list(SECRETS.values()))
    logger = EvidenceLogger(new_run_id(), redactor, base_dir=tmp_path)
    fake = DroppedConfirmationBank()

    result = engine(fake, logger).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")

    assert result.status == Outcome.HARD_FAILURE
    assert result.business_outcome == "dispatch_unverified"
    assert "verify before any retry" in result.failure_detail.observed
    assert fake.transfer_clicks == 1  # the rule: never clicked a second time

    assert len(result.escalations) == 1
    ticket = result.escalations[0]
    assert ticket.status == "open" and ticket.decision is None
    assert ticket.run_id == result.run_id == logger.run_id == logger.dir.name
    assert "Balance before the step: 1040.00" in ticket.procedure
    assert "Funds Transfer Sent" in ticket.procedure  # built from the artifact's own lookup steps

    on_disk_text = next(logger.dir.glob("ticket-*.json")).read_text(encoding="utf-8")
    assert json.loads(on_disk_text)["status"] == "open"
    assert FROM not in on_disk_text  # the saved ticket is redacted like every other evidence file


def test_a_session_that_expires_at_the_click_is_never_recovered_by_reclicking():
    # Before this rule, an expired session at the submit step would re-login and re-run the step.
    fake = DroppedConfirmationBank(expire_on_click=True)

    result = engine(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")

    assert result.status == Outcome.HARD_FAILURE  # not RECOVERABLE
    assert result.business_outcome == "dispatch_unverified"
    assert fake.transfer_clicks == 1
    assert fake.logins == 1  # only the initial login: no recovery re-login


def test_a_click_that_raises_after_dispatch_is_unverified_not_a_plain_failure():
    # Playwright can raise after the request has already gone out; the transfer may have posted.
    fake = ClickRaisesBank()

    result = engine(fake).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")

    assert result.status == Outcome.HARD_FAILURE
    assert result.business_outcome == "dispatch_unverified"
    assert len(result.escalations) == 1 and result.escalations[0].status == "open"
    assert fake.transfer_clicks == 1  # and never a second attempt


def test_a_transfer_button_that_cannot_be_found_stays_a_plain_failure_with_no_ticket():
    # Nothing was clicked, so nothing could have posted: no false alarm for a human.
    result = engine(NoTransferButtonBank()).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")

    assert result.status == Outcome.HARD_FAILURE
    assert result.business_outcome is None
    assert result.escalations == []


def test_click_then_reject_waits_the_configured_time_not_the_artifacts_shorter_one():
    # The artifact's own wait for the Transfer step is 10s; the configured one is what applies.
    # If this path used the raw artifact wait, an absent confirmation would take ~10s to give up.
    import time

    fake = FakeBank()

    def on_escalate(ticket):
        fake.pending_captures.append({"tag": "BUTTON", "text": "Transfer", "url": fake.url})  # click, no effect
        return "reject"

    over_threshold = Config(approval_threshold=Decimal(1), submit_confirmation_wait_ms=300)
    started = time.monotonic()
    result = ReplayEngine(fake, PolicyGate(over_threshold)).replay(
        capability(), PARAMS, SECRETS, BASE + "/index.htm", on_escalate
    )
    elapsed = time.monotonic() - started

    assert result.business_outcome == "dispatch_unverified"
    assert elapsed < 5


def test_only_the_named_triggers_open_a_ticket_other_hard_failures_page_nobody():
    result = engine(FakeBank()).replay(capability(), {**PARAMS, "amount": "nope"}, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.HARD_FAILURE
    assert result.escalations == []


def test_a_confirmed_transfer_is_untouched_by_the_new_rule():
    result = engine(FakeBank()).replay(capability(), PARAMS, SECRETS, BASE + "/index.htm")
    assert result.status == Outcome.SUCCESS
    assert result.business_outcome is None
    assert result.escalations == []


def test_the_procedure_never_reaches_an_http_caller_but_the_correlation_ids_do():
    summary = {
        "run_id": "r-1",
        "escalations": [{"ticket_id": "t-1", "run_id": "r-1", "status": "open", "procedure": "secret steps"}],
    }
    caller_view = _for_caller(summary)
    assert "procedure" not in caller_view["escalations"][0]
    assert caller_view["escalations"][0]["ticket_id"] == "t-1"
    assert caller_view["run_id"] == "r-1"
