from decimal import Decimal

import pytest
from stockroom.errors import MoneyError
from stockroom.money import (
    apply_percent,
    parse_money,
    parse_percent,
    round_money,
)

D = Decimal


def test_r1_floats_and_bools_are_rejected():
    """R1: floats and bools never become money."""
    for bad in (1.5, True, None, [1]):
        with pytest.raises(MoneyError):
            parse_money(bad)


def test_r1_prices_have_two_decimals_and_no_silent_rounding():
    """R1: strings and ints parse exactly; extra decimals are refused."""
    assert str(parse_money("12.5")) == "12.50"
    assert str(parse_money(3)) == "3.00"
    assert parse_money("1.500") == D("1.50")
    for bad in ("1.005", "-0.01", "abc", "", "NaN", "Infinity"):
        with pytest.raises(MoneyError):
            parse_money(bad)


def test_r2_rounding_is_half_up():
    """R2: halves round up, unlike float or banker's rounding."""
    assert round_money(D("2.675")) == D("2.68")
    assert round_money(D("0.125")) == D("0.13")
    assert round_money(D("0.005")) == D("0.01")
    assert round_money(D("0.004")) == D("0.00")


def test_r2_apply_percent_rounds_once_to_cents():
    """R2: a percentage of an amount is rounded half up to cents."""
    assert apply_percent(D("0.10"), D("5")) == D("0.01")
    assert apply_percent(D("17.10"), D("8.25")) == D("1.41")
    assert str(apply_percent(D("10.00"), D("0"))) == "0.00"


def test_r1_percentages_use_percent_units_between_0_and_100():
    """R1: percent values are Decimals from 0 to 100."""
    assert parse_percent("8.25") == D("8.25") and parse_percent(100) == D(100)
    for bad in ("-1", "100.01", 2.5):
        with pytest.raises(MoneyError):
            parse_percent(bad)
