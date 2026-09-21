from decimal import Decimal

import pytest

from cua.adapter import Candidate
from cua.config import Config
from cua.enums import Verdict
from cua.guard import ActionGuard
from cua.models import Locator, LocatorCandidate
from cua.policy_gate import PolicyGate
from cua.tools import AgentAction

OVERVIEW = "http://h/parabank/overview.htm"
TRANSFER = "http://h/parabank/transfer.htm"


def guard(amount: str = "5") -> ActionGuard:
    gate = PolicyGate(Config(approval_threshold=Decimal(100)))
    return ActionGuard(gate, {"amount": amount}, "amount")


def element(role: str, name: str, href: str | None = None) -> Candidate:
    loc = Locator(description=name, chain=[LocatorCandidate(strategy="text", value=name)])
    return Candidate(1, role, name, None, None, loc, href=href)


def click(role: str = "button", name: str = "Go", href: str | None = None) -> AgentAction:
    return AgentAction("click", ref=1, element=element(role, name, href))


def type_amount() -> AgentAction:
    return AgentAction("type", ref=2, text="5", element=element("textbox", "Amount"))


def test_navigation_and_link_targets_are_checked_before_the_action():
    g = guard()
    assert g.check(AgentAction("navigate", text="transfer.htm"), OVERVIEW, "other", None).verdict == (
        Verdict.ALLOW
    )
    admin = AgentAction("navigate", text="/parabank/admin.htm")
    assert g.check(admin, OVERVIEW, "other", None).verdict == Verdict.BLOCK
    bad_link = click("link", "Admin", "http://h/parabank/admin.htm")
    assert g.check(bad_link, OVERVIEW, "other", None).verdict == Verdict.BLOCK
    good_link = click("link", "Transfer Funds", "http://h/parabank/transfer.htm")
    assert g.check(good_link, OVERVIEW, "other", None).verdict == Verdict.ALLOW


def test_typing_the_amount_needs_a_known_balance_that_covers_it():
    g = guard("5")
    unknown = g.check(type_amount(), TRANSFER, "parameter", "amount")
    assert unknown.verdict == Verdict.BLOCK and "balance unknown" in unknown.reason
    g.set_balance("$100.00")
    assert g.check(type_amount(), TRANSFER, "parameter", "amount").verdict == Verdict.ALLOW
    g.set_balance("$2.00")
    too_low = g.check(type_amount(), TRANSFER, "parameter", "amount")
    assert too_low.verdict == Verdict.BLOCK and "exceeds balance" in too_low.reason


def test_over_threshold_is_blocked_in_discovery():
    g = guard("150")
    g.set_balance("$1000.00")
    result = g.check(type_amount(), TRANSFER, "parameter", "amount")
    assert result.verdict == Verdict.BLOCK and "approval" in result.reason


def test_other_typing_is_not_subject_to_the_amount_rules():
    g = guard()  # no balance known
    other = AgentAction("type", ref=2, text="hello", element=element("textbox", "Note"))
    assert g.check(other, TRANSFER, "other", None).verdict == Verdict.ALLOW


def test_clicks_after_the_amount_is_typed_re_check_the_amount_rules():
    g = guard("5")
    g.set_balance("$100.00")
    g.after(type_amount(), TRANSFER, TRANSFER, True, "parameter", "amount")
    assert g.armed
    assert g.check(click(), TRANSFER, "other", None).verdict == Verdict.ALLOW
    g.set_balance("$1.00")
    assert g.check(click(), TRANSFER, "other", None).verdict == Verdict.BLOCK


def test_a_submitted_button_cannot_be_pressed_twice():
    g = guard("5")
    g.set_balance("$100.00")
    g.after(type_amount(), TRANSFER, TRANSFER, True, "parameter", "amount")
    submit = click("button", "Transfer")
    assert g.check(submit, TRANSFER, "other", None).verdict == Verdict.ALLOW
    g.after(submit, TRANSFER, TRANSFER, True, "other", None)
    again = g.check(click("button", "Transfer"), TRANSFER, "other", None)
    assert again.verdict == Verdict.BLOCK and "already submitted" in again.reason


def test_links_never_count_as_a_submission_and_navigation_disarms():
    g = guard("5")
    g.set_balance("$100.00")
    g.after(type_amount(), TRANSFER, TRANSFER, True, "parameter", "amount")
    link = click("link", "Accounts Overview", "http://h/parabank/overview.htm")
    g.after(link, TRANSFER, OVERVIEW, True, "other", None)
    assert not g.armed and not g.dispatched
    assert g.check(link, OVERVIEW, "other", None).verdict == Verdict.ALLOW


def test_a_failed_action_changes_nothing():
    g = guard("5")
    g.after(type_amount(), TRANSFER, TRANSFER, False, "parameter", "amount")
    assert not g.armed


@pytest.mark.parametrize(
    ("text", "expected"),
    [("$1,114.00", Decimal("1114.00")), ("-$598.50", Decimal("-598.50")), (" $5 ", Decimal(5))],
)
def test_balance_parsing(text, expected):
    g = guard()
    assert g.set_balance(text) is True and g.balance == expected


@pytest.mark.parametrize("text", ["", "n/a", "NaN", "$"])
def test_unparseable_balance_is_rejected(text):
    g = guard()
    assert g.set_balance(text) is False and g.balance is None


def test_landing_on_a_denied_page_is_blocked():
    g = guard()
    assert g.landing("http://h/parabank/services/x").verdict == Verdict.BLOCK
    assert g.landing(OVERVIEW).verdict == Verdict.ALLOW
