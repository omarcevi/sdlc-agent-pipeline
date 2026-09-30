from decimal import Decimal

import pytest
from stockroom import Product, Tier, Unit
from stockroom.catalog import Catalog, normalize_sku
from stockroom.errors import (
    CatalogError,
    DuplicateSku,
    InvalidSku,
    MoneyError,
    UnknownSku,
)

D = Decimal


def make(sku="A-1", **overrides):
    values = {"name": "Thing", "unit": Unit.EACH, "list_price": D("2.00")}
    return Product(sku, **{**values, **overrides})


def test_r7_skus_are_normalised_to_upper_case():
    """R7: SKUs are case-insensitive and stored upper case."""
    assert normalize_sku("  pen-1 ") == "PEN-1"
    assert make("pen-1").sku == "PEN-1"


@pytest.mark.parametrize("bad", ["", "a--b", "-a", "a-", "a b", "a_b", "x" * 21])
def test_r7_bad_skus_are_rejected(bad):
    """R7: hyphen groups, letters and digits only, at most 20 characters."""
    with pytest.raises(InvalidSku):
        normalize_sku(bad)


def test_r7_a_sku_can_be_added_once_and_is_found_in_any_case():
    """R7: duplicates are refused; lookups normalise the SKU."""
    catalog = Catalog()
    catalog.add(make("pen-1"))
    with pytest.raises(DuplicateSku):
        catalog.add(make("PEN-1"))
    assert catalog.get("Pen-1").sku == "PEN-1" and "pen-1" in catalog
    assert "nope" not in catalog and "not a sku!" not in catalog
    with pytest.raises(UnknownSku):
        catalog.get("NOPE")


def test_r1_product_prices_are_decimal_only():
    """R1: a float price is refused, a string price is exact."""
    with pytest.raises(MoneyError):
        make(list_price=1.5)
    assert make(list_price="1.50").list_price == D("1.50")


def test_a_product_needs_a_name_and_a_known_unit():
    """Catalog validation, and unit codes (R16)."""
    for bad in ({"name": "  "}, {"unit": "each"}):
        with pytest.raises(CatalogError):
            make(**bad)
    assert Unit.parse(" KG ") is Unit.KILOGRAM
    with pytest.raises(CatalogError):
        Unit.parse("litre")


def test_r4_tiers_are_sorted_and_validated():
    """R4: min_qty of 2 or more, strictly rising percentages, 0 < percent <= 100."""
    product = make(tiers=(Tier(20, D("10")), Tier(5, D("5"))))
    assert [t.min_qty for t in product.tiers] == [5, 20]
    for args in ((1, D("5")), (5, D("0"))):
        with pytest.raises(CatalogError):
            Tier(*args)
    with pytest.raises(MoneyError):
        Tier(5, D("101"))
    for tiers in (
        (Tier(5, D("5")), Tier(5, D("6"))),
        (Tier(5, D("5")), Tier(10, D("5"))),
    ):
        with pytest.raises(CatalogError):
            make(tiers=tiers)


def test_r11_set_price_replaces_the_product():
    """R11: a price change makes a new product; old copies are unchanged."""
    catalog = Catalog()
    original = make("b-1")
    catalog.add(original)
    catalog.add(make("a-1"))
    assert catalog.set_price("B-1", "3.25").list_price == D("3.25")
    assert catalog.get("b-1").list_price == D("3.25")
    assert original.list_price == D("2.00")
    assert [p.sku for p in catalog.products()] == ["A-1", "B-1"]  # sorted (R18)
