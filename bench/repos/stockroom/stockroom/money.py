"""Money helpers: the only place that parses, rounds or formats money.

Money is `decimal.Decimal`, always with two decimal places. Floats are rejected
everywhere. Percentages are `Decimal` numbers in percent units (`Decimal("8.25")`
means 8.25 %), and `round_money` is the one rounding function: half up, to whole
cents. Pricing, orders and CSV all go through these helpers.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .errors import MoneyError

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)


def to_decimal(value: object) -> Decimal:
    """Convert a Decimal, int or str to a finite Decimal.

    Floats and bools are rejected, because a float has already lost the exact
    value the caller meant."""
    if isinstance(value, bool | float):
        raise MoneyError(f"{type(value).__name__} is not allowed for money: {value!r}")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except InvalidOperation:
            raise MoneyError(f"not a number: {value!r}") from None
    else:
        raise MoneyError(f"not a number: {value!r}")
    if not result.is_finite():
        raise MoneyError(f"not a finite number: {value!r}")
    return result


def round_money(amount: Decimal) -> Decimal:
    """Round to whole cents, halves away from zero. The only rounding in stockroom."""
    if not isinstance(amount, Decimal):
        raise MoneyError(f"expected Decimal, got {type(amount).__name__}")
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_money(value: object) -> Decimal:
    """Parse a price: non-negative, with at most two decimal places.

    Extra decimals are an error rather than being rounded away silently."""
    amount = to_decimal(value)
    if amount < 0:
        raise MoneyError(f"money cannot be negative: {value!r}")
    cents = round_money(amount)
    if cents != amount:
        raise MoneyError(f"more than two decimal places: {value!r}")
    return cents


def parse_percent(value: object) -> Decimal:
    """Parse a percentage in percent units, from 0 to 100 inclusive."""
    percent = to_decimal(value)
    if percent < 0 or percent > HUNDRED:
        raise MoneyError(f"percentage must be between 0 and 100: {value!r}")
    return percent


def apply_percent(amount: Decimal, percent: Decimal) -> Decimal:
    """`percent` % of `amount`, rounded to cents."""
    return round_money(amount * percent / HUNDRED)


def format_money(amount: Decimal) -> str:
    """Render an amount with exactly two decimals, e.g. `12.50`."""
    return f"{round_money(amount):.2f}"
