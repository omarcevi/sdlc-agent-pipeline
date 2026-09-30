from datetime import date

import pytest
from taskcli.dates import parse_due

TODAY = date(2026, 9, 30)


def test_keywords():
    assert parse_due("today", TODAY) == TODAY
    assert parse_due("Tomorrow", TODAY) == date(2026, 10, 1)


def test_relative_days():
    assert parse_due("+3d", TODAY) == date(2026, 10, 3)


def test_iso_date():
    assert parse_due("2026-12-24", TODAY) == date(2026, 12, 24)


def test_rejects_garbage():
    with pytest.raises(ValueError):
        parse_due("someday", TODAY)
