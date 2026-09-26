"""Money precision: validation checks the value, rendering formats the fill, comparison is Decimal.

Background: ParaBank accepts an amount with more than two decimal places, moves the unrounded value,
shows the rounded one, and is then left with a balance it can never format (every page fails with
"Rounding necessary"). So the contract enforces precision before anything is dispatched.
"""

from decimal import Decimal

import pytest

from cua.money import (
    MoneyError,
    canonical,
    canonical_text,
    decimal_places,
    parse_amount,
    parse_money,
    render_text,
)
from cua.verify import render

# -- validation: at most 2 decimal places, counted on the NORMALIZED value -------------------------


@pytest.mark.parametrize(
    "text", ["1.45", "1.5", "3", "5.00", "1.500", "100", "0", "0.05", ".5", "5.", "+2", " 7.25 ", "-5", "999999999999"]
)
def test_an_amount_with_at_most_two_significant_decimals_is_accepted(text):
    assert parse_amount("amount", text) == Decimal(text.strip())


@pytest.mark.parametrize("text", ["1.98484", "1.984", "0.001", "5.005", "-1.984"])
def test_more_than_two_decimal_places_is_rejected_and_the_message_names_the_constraint(text):
    with pytest.raises(MoneyError, match=r"amount supports at most 2 decimal places \(USD\)") as info:
        parse_amount("amount", text)
    assert info.value.is_precision  # a policy block, not a malformed request


@pytest.mark.parametrize("text", ["abc", "", "  ", "NaN", "nan", "Infinity", "-Infinity", "1e2", "1E-9", "1,5", "$5", "5 5"])
def test_anything_that_is_not_a_plain_finite_decimal_is_rejected_as_malformed(text):
    with pytest.raises(MoneyError, match="not a valid decimal") as info:
        parse_amount("amount", text)
    assert not info.value.is_precision  # malformed input, not a rule breach


def test_an_absurdly_large_amount_is_rejected_rather_than_overflowing_a_formatter():
    with pytest.raises(MoneyError, match="too large") as info:
        parse_amount("amount", "9" * 13)
    assert info.value.is_precision


def test_trailing_zeros_never_count_as_decimals():
    # Decimal preserves the literal's representation, so the count must be on the normalized value.
    assert [decimal_places(Decimal(t)) for t in ("1.50", "1.500", "1.5", "5.00", "100", "1E+2", "1.98484")] == [
        1, 1, 1, 0, 0, 0, 5,
    ]


def test_existing_sign_rules_are_not_this_modules_job():
    # -5 and 0 parse fine here; the Policy Gate's `amount <= 0` rule is what rejects them.
    assert parse_amount("amount", "-5") == Decimal(-5)
    assert parse_amount("amount", "0") == Decimal(0)


# -- rendering: exactly two decimals, a bare number ---------------------------------------------------


@pytest.mark.parametrize(
    "given,rendered",
    [("1.5", "1.50"), ("1.45", "1.45"), ("3", "3.00"), ("100", "100.00"), ("5.00", "5.00"), ("1.500", "1.50"),
     ("0.05", "0.05"), ("3.3", "3.30")],
)
def test_an_amount_renders_as_exactly_two_decimals_with_no_currency_symbol(given, rendered):
    assert canonical_text("amount", given) == rendered
    assert canonical(Decimal(given)) == rendered
    assert "$" not in rendered


def test_the_money_placeholder_fills_in_the_canonical_form_everywhere_it_is_used():
    params = {"amount": "1.5"}
    assert render("{{amount:money}}", params) == "1.50"  # what is typed into the amount field
    assert render("{{amount:money}}", {"amount": "1.45"}) == "1.45"  # and into the search box: no "$"
    assert render("${{amount:money}} has been transferred", params) == "$1.50 has been transferred"
    assert render("{{amount:money}}|{{amount}}", {"amount": "1.500"}) == "1.50|1.500"


def test_a_money_placeholder_that_cannot_be_rendered_stays_visibly_unrendered():
    # Never guessed at: it simply fails to match anything on the page.
    assert render("{{amount:money}}", {"amount": "abc"}) == "{{amount:money}}"
    assert render("{{amount:money}}", {"amount": "1.98484"}) == "{{amount:money}}"
    assert render_text("1.98484") is None


# -- comparison: by value, never by string --------------------------------------------------------


def test_displayed_money_is_compared_by_decimal_value():
    assert parse_money("$1.44") == Decimal("1.44")
    assert parse_money("$5.00") == Decimal(5)
    assert parse_money("$5") == Decimal(5)
    assert parse_money("$1,050.00") == Decimal(1050)
    assert parse_money(" $ 0.50 ") == Decimal("0.5")


def test_the_confirmation_text_for_a_non_whole_amount_matches_the_rendered_sentence():
    expected = render(
        "${{amount:money}} has been transferred from account #{{from_account}} to account #{{to_account}}.",
        {"amount": "1.5", "from_account": "A", "to_account": "B"},
    )
    assert expected == "$1.50 has been transferred from account #A to account #B."
