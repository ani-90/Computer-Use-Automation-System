"""Money through the whole Replay Engine: every amount the contract allows is typed and confirmed in its
canonical two-decimal form, and every amount it does not allow never reaches the browser.

Why this exists: ParaBank accepts an amount like 1.98484, moves the unrounded value, shows "$1.98", and is
left with a balance it can never format again. An artifact that hardcoded the app's ".00" also failed to
confirm any non-whole amount AFTER moving the money. Both are pre-dispatch problems, so both are tested
without a browser.
"""

from decimal import Decimal

import pytest

from cua.config import Config
from cua.enums import Outcome
from cua.models import Capability
from cua.money import canonical
from cua.policy_gate import PolicyGate
from cua.replay import ReplayEngine
from tests.test_replay import BASE, CAP_PATH, FROM, PARAMS, SECRETS, TO, FakeBank, capability


def money_capability() -> Capability:
    """The committed artifact, which the compiler writes with money parameters as {{amount:money}}."""
    cap = capability()
    assert "{{amount:money}}" in cap.model_dump_json(), "the committed artifact predates money-typed placeholders"
    return cap


def legacy_capability() -> Capability:
    """An artifact in the pre-money form (version 1): the app's own ".00" hardcoded after a bare
    {{amount}}. Rebuilt from the committed one so the engine's guard for such artifacts stays tested
    even though none is committed any more."""
    text = CAP_PATH.read_text(encoding="utf-8")
    assert "${{amount:money}}" in text
    text = text.replace("${{amount:money}}", "${{amount}}.00").replace("{{amount:money}}", "{{amount}}")
    return Capability.model_validate_json(text)


def engine(fake):
    return ReplayEngine(fake, PolicyGate(Config(approval_threshold=Decimal(1000))))


def run(fake, cap, amount):
    return engine(fake).replay(cap, {**PARAMS, "amount": amount}, SECRETS, BASE + "/index.htm")


@pytest.mark.parametrize("amount", ["1.5", "3.3", "1.44", "5", "100", "1.500", "5.00", "0.05"])
def test_every_allowed_amount_is_typed_canonically_and_confirmed(amount):
    fake = FakeBank()
    result = run(fake, money_capability(), amount)

    shown = canonical(Decimal(amount))
    assert result.status == Outcome.SUCCESS, result.failure_detail
    assert fake.fields["Amount: $"] == shown  # never the raw "1.500" the caller wrote
    assert result.outputs.confirmation_text == f"${shown} has been transferred from account #{FROM} to account #{TO}."
    assert fake.fields["Find by Amount:"] == shown  # the ledger search uses the same canonical value


@pytest.mark.parametrize("amount", ["1.98484", "1.984", "0.001", "5.005"])
def test_more_than_two_decimals_is_a_policy_block_and_the_browser_is_never_touched(amount):
    fake = FakeBank()
    result = run(fake, money_capability(), amount)

    assert result.status == Outcome.POLICY_BLOCK
    assert "at most 2 decimal places" in result.failure_detail.observed
    assert fake.page == "login"  # never navigated: nothing was dispatched, nothing reached the app
    assert not fake.transferred


@pytest.mark.parametrize("amount", ["abc", "NaN", "nan", "Infinity", "1e2", "1,5", "", "$5"])
def test_a_malformed_amount_is_a_hard_failure_never_a_crash(amount):
    # "NaN" used to be accepted as a decimal and then crash the first `amount <= 0` comparison.
    fake = FakeBank()
    result = run(fake, money_capability(), amount)

    assert result.status == Outcome.HARD_FAILURE
    assert "not a valid decimal" in result.failure_detail.observed
    assert fake.page == "login"


@pytest.mark.parametrize("amount", ["0", "-5", "0.00", "-1.50"])
def test_the_existing_sign_rule_still_blocks_before_the_browser(amount):
    fake = FakeBank()
    result = run(fake, money_capability(), amount)
    assert result.status == Outcome.POLICY_BLOCK
    assert fake.page == "login"


def test_the_precision_rule_is_judged_before_the_amount_rules():
    # 1.98484 is over the balance-irrelevant precision limit whatever the balance is.
    fake = FakeBank(start_balance="1.00")
    result = run(fake, money_capability(), "1.98484")
    assert result.status == Outcome.POLICY_BLOCK
    assert "decimal places" in result.failure_detail.observed


# -- the committed artifact predates money-typed placeholders until a discovery run regenerates it ------


def test_the_pre_money_artifact_refuses_a_fractional_amount_before_moving_any_money():
    fake = FakeBank()
    result = run(fake, legacy_capability(), "1.5")

    assert result.status == Outcome.HARD_FAILURE
    assert "whole-number amounts only" in result.failure_detail.observed
    assert fake.page == "login" and not fake.transferred


@pytest.mark.parametrize("amount,typed", [("12", "12"), ("5.00", "5"), ("7.000", "7")])
def test_the_pre_money_artifact_still_handles_whole_amounts_in_its_own_plain_form(amount, typed):
    fake = FakeBank()
    result = run(fake, legacy_capability(), amount)

    assert result.status == Outcome.SUCCESS, result.failure_detail
    assert fake.fields["Amount: $"] == typed  # "5.00" would have been confirmed as "$5.00.00"
