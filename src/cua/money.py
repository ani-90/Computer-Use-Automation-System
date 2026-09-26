"""Money in one place: what a valid amount is, and how it is written down.

Found by hand against the target: ParaBank accepts an amount with more than two decimal places
(1.98484), moves the unrounded value, displays the rounded one ($1.98), and is left with a balance it
cannot format — every page that shows it then fails with "Rounding necessary", permanently. A target's
own validation cannot be assumed, so the capability contract enforces money precision before anything
is dispatched. Three separate concerns, each with one rule:

  VALIDATION  checks the value: a plain, finite decimal with AT MOST 2 decimal places, counted on the
              normalized value (1.5, 1.50 and 1.500 are all one-and-a-half; 1.984 is not allowed).
  RENDERING   writes the value the way a money field expects: exactly 2 decimals, no "$" (1.5 -> "1.50").
  COMPARISON  is Decimal equality, never string equality (Decimal("5") == Decimal("5.00")).

Floats are never used anywhere: every amount is a Decimal built from its text.
"""

import re
from decimal import Decimal, InvalidOperation

MAX_DECIMALS = 2
_MAX_INTEGER_DIGITS = 12
# A plain decimal literal only: no exponent ("1e2"), no "NaN"/"Infinity", no thousands separators.
_PLAIN = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")
_CENT = Decimal("0.01")


class MoneyError(ValueError):
    """The text is not a usable amount. `is_precision` separates "well-formed but breaks the
    contract's precision or size rule" (a policy block) from "not a number at all" (malformed)."""

    def __init__(self, message: str, *, is_precision: bool = False):
        super().__init__(message)
        self.is_precision = is_precision


def parse_amount(name: str, text: str) -> Decimal:
    """The Decimal an amount's text denotes, or MoneyError. Never returns NaN or Infinity — a
    non-finite value would otherwise crash the first `amount <= 0` comparison."""
    stripped = text.strip()
    if not _PLAIN.fullmatch(stripped):
        raise MoneyError(f"{name} is not a valid decimal")
    try:
        value = Decimal(stripped)
    except InvalidOperation:  # unreachable after the pattern, kept so a bad value can never slip by
        raise MoneyError(f"{name} is not a valid decimal") from None
    if decimal_places(value) > MAX_DECIMALS:
        raise MoneyError(f"{name} supports at most {MAX_DECIMALS} decimal places (USD)", is_precision=True)
    if abs(value).adjusted() + 1 > _MAX_INTEGER_DIGITS:
        raise MoneyError(f"{name} is too large", is_precision=True)
    return value


def decimal_places(value: Decimal) -> int:
    """Significant decimal places of the NORMALIZED value: trailing zeros never count.
    Decimal("100").normalize() is 1E+2 (a positive exponent), hence the clamp at zero."""
    return max(0, -value.normalize().as_tuple().exponent)


def canonical(value: Decimal) -> str:
    """The value as a money field expects it: exactly 2 decimals, no currency symbol."""
    return format(value.quantize(_CENT), "f")


def canonical_text(name: str, text: str) -> str:
    return canonical(parse_amount(name, text))


def render_text(text: str) -> str | None:
    """The canonical form of an amount given as text, or None when it is not a usable amount.
    For rendering a placeholder: an unrenderable value must stay visibly unrendered and simply
    fail to match, never be guessed at."""
    try:
        return canonical(parse_amount("amount", text))
    except MoneyError:
        return None


def parse_money(text: str) -> Decimal:
    """A displayed money value ("$1,050.00", "$5") as a Decimal, for comparison by value."""
    return Decimal(text.replace("$", "").replace(",", "").strip())
