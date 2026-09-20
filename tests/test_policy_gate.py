from decimal import Decimal

import pytest

from cua.config import Config
from cua.enums import Verdict
from cua.policy_gate import PolicyGate

TRANSFER = "http://localhost:8080/parabank/transfer.htm"
D = Decimal


@pytest.fixture
def gate() -> PolicyGate:
    return PolicyGate(Config(approval_threshold=D(100)))


@pytest.mark.parametrize("amount", [D(0), D(-5)])
def test_non_positive_amount_blocks(gate, amount):
    assert gate.check(TRANSFER, amount, D(1000)).verdict == Verdict.BLOCK


def test_amount_over_balance_blocks(gate):
    assert gate.check(TRANSFER, D(50), D(10)).verdict == Verdict.BLOCK


def test_amount_over_threshold_escalates_not_blocks(gate):
    assert gate.check(TRANSFER, D(200), D(1000)).verdict == Verdict.ESCALATE


def test_rule_order_balance_beats_threshold(gate):
    # Over both balance and threshold: the block rule runs first.
    assert gate.check(TRANSFER, D(900), D(150)).verdict == Verdict.BLOCK


def test_amount_within_policy_allows(gate):
    assert gate.check(TRANSFER, D(5), D(1000)).verdict == Verdict.ALLOW


def test_amount_with_unknown_balance_blocks(gate):
    assert gate.check(TRANSFER, D(5), None).verdict == Verdict.BLOCK


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8080/parabank/services/bank/accounts",
        "http://localhost:8080/parabank/services_proxy/bank",
        "http://localhost:8080/parabank/admin.htm",
        "http://localhost:8080/parabank/transfer.htm/../services/x",
    ],
)
def test_denied_paths_block(gate, url):
    assert gate.check(url).verdict == Verdict.BLOCK


def test_unlisted_path_blocks(gate):
    assert gate.check("http://localhost:8080/parabank/register.htm").verdict == Verdict.BLOCK
