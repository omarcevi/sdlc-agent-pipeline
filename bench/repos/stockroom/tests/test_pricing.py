from decimal import Decimal

import pytest
from stockroom import Product, Store, Unit
from stockroom.errors import CatalogError, InvalidQuantity, UnknownCoupon, UnknownSku
from stockroom.pricing import Coupon, combined_percent, tier_percent

D = Decimal


def amounts(line):
    return (line.gross, line.discount, line.net, line.tax, line.total)


def test_r3_order_of_operations_on_a_line(store):
    """R3: gross, then discount, then tax on the discounted amount."""
    (line,) = store.quote([("PEN-1", 12)]).lines
    assert amounts(line) == (D("18.00"), D("0.90"), D("17.10"), D("1.41"), D("18.51"))


def test_r3_below_the_first_tier_a_line_is_gross_plus_tax(store):
    """R3: no tier, no discount."""
    (line,) = store.quote([("PEN-1", 9)]).lines
    assert amounts(line) == (D("13.50"), D("0"), D("13.50"), D("1.11"), D("14.61"))


def test_r2_tax_is_rounded_half_up_per_line():
    """R2: each line's tax is rounded on its own."""
    store = Store(tax_percent="5")
    for sku in ("A-1", "A-2"):
        store.add_product(Product(sku, "Dime", Unit.EACH, D("0.10")))
    quote = store.quote([("A-1", 1), ("A-2", 1)])
    assert [line.tax for line in quote.lines] == [D("0.01"), D("0.01")]
    assert quote.tax == D("0.02")


def test_r2_discounts_are_rounded_half_up():
    """R2: a half-cent discount rounds up."""
    store = Store(tax_percent="0")
    store.add_product(Product("A-1", "Dime", Unit.EACH, D("0.10")))
    store.add_coupon("HALF", "5")
    (line,) = store.quote([("A-1", 1)], "half").lines
    assert (line.discount, line.net) == (D("0.01"), D("0.09"))


def test_r4_tier_selection_uses_the_largest_reached_tier(store):
    """R4: boundaries of the PEN-1 tiers (10 -> 5 %, 50 -> 10 %)."""
    pen = store.catalog.get("PEN-1")
    got = [tier_percent(pen, q) for q in (1, 9, 10, 49, 50, 500)]
    assert got == [D(0), D(0), D(5), D(5), D(10), D(10)]
    assert tier_percent(store.catalog.get("BOOK-1"), 1000) == D(0)


def test_r5_coupon_adds_to_the_tier(store):
    """R5: 5 % tier plus a 10 % coupon is a 15 % discount."""
    store.add_coupon("save10", "10")
    (line,) = store.quote([("PEN-1", 12)], "SAVE10").lines
    assert line.discount_percent == D("15")
    assert amounts(line) == (D("18.00"), D("2.70"), D("15.30"), D("1.26"), D("16.56"))


def test_r5_the_combined_discount_is_capped_at_40_percent(store):
    """R5: tier 30 % plus coupon 25 % is limited to 40 %."""
    store.add_coupon("BIG", "25")
    (line,) = store.quote([("WIDGET", 20)], "BIG").lines
    assert (line.tier_percent, line.discount_percent) == (D("30"), D("40"))
    assert amounts(line) == (
        D("399.80"),
        D("159.92"),
        D("239.88"),
        D("19.79"),
        D("259.67"),
    )
    assert combined_percent(D("39"), D("1")) == D("40")
    assert combined_percent(D("10"), D("5")) == D("15")


def test_r5_coupon_codes_ignore_case_and_bad_coupons_fail(store):
    """R5: lookup is case-insensitive; unknown or invalid coupons are errors."""
    store.add_coupon("Spring", "10")
    assert store.quote([("PEN-1", 1)], " spring ").discount == D("0.15")
    with pytest.raises(UnknownCoupon):
        store.quote([("PEN-1", 1)], "WINTER")
    for code, percent in (("", "10"), ("bad code", "10"), ("OK", "0")):
        with pytest.raises(CatalogError):
            store.add_coupon(code, percent)
    assert Coupon(" vip_1 ", D("5")).code == "VIP_1"


def test_r6_totals_are_sums_of_the_lines(store):
    """R6: no re-rounding of totals."""
    quote = store.quote([("PEN-1", 12), ("BOOK-1", 3)])
    assert (quote.subtotal, quote.discount, quote.net, quote.tax, quote.total) == (
        D("32.97"),
        D("0.90"),
        D("32.07"),
        D("1.41"),
        D("33.48"),
    )
    assert quote.total == sum((line.total for line in quote.lines), D("0.00"))


def test_quote_rejects_bad_items_and_bad_tax_rates(store):
    """R8 and R1: positive whole quantities, catalog SKUs, percent-unit tax rate."""
    with pytest.raises(InvalidQuantity):
        store.quote([("PEN-1", 0)])
    with pytest.raises(UnknownSku):
        store.quote([("GHOST", 1)])
    assert Store(tax_percent=0).tax_percent == D(0)
    for bad in (8.25, "101"):
        with pytest.raises(ValueError):
            Store(tax_percent=bad)


def test_r11_quote_merges_repeated_skus_like_an_order(store):
    """R11: a split quantity is priced as the merged quantity, in quote and order."""
    split = store.quote([("pen-1", 6), ("PEN-1", 6)])
    assert len(split.lines) == 1 and split.lines[0].tier_percent == D("5")
    assert split == store.quote([("PEN-1", 12)])
    assert split.total == store.create_order([("PEN-1", 6), ("PEN-1", 6)]).quote.total
