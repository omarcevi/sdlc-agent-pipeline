from decimal import Decimal

from stockroom import Product, Store, Tier, Unit

D = Decimal


def build_store():
    """Tax 8.25 %. PEN-1: 1.50, tiers 10 -> 5 % and 50 -> 10 %. BOOK-1: 4.99, untaxed.
    WIDGET: 19.99, tiers 5 -> 10 % and 20 -> 30 %. Coupons SAVE10 (10 %) and BIG (25 %).
    """
    store = Store(tax_percent="8.25")
    store.add_product(
        Product(
            "PEN-1",
            "Pen",
            Unit.EACH,
            D("1.50"),
            tiers=(Tier(10, D("5")), Tier(50, D("10"))),
        )
    )
    store.add_product(Product("BOOK-1", "Notebook", Unit.EACH, D("4.99"), False))
    store.add_product(
        Product(
            "WIDGET",
            "Widget",
            Unit.BOX,
            D("19.99"),
            tiers=(Tier(5, D("10")), Tier(20, D("30"))),
        )
    )
    for sku, units in (("PEN-1", 100), ("BOOK-1", 20), ("WIDGET", 30)):
        store.receive(sku, units)
    store.add_coupon("SAVE10", "10")
    store.add_coupon("BIG", "25")
    return store


def amounts(line):
    return (line.gross, line.discount, line.net, line.tax, line.total)


def cancelled_after_shipping(store, items, coupon, shipped):
    order = store.create_order(items, coupon)
    store.reserve_order(order.id)
    store.fulfil_order(order.id, shipped)
    store.cancel_order(order.id)
    return store.billable(order.id)


def test_issue_example_counts_the_coupon_on_the_shipped_units():
    store = build_store()
    bill = cancelled_after_shipping(store, [("PEN-1", 12)], "save10", {"PEN-1": 9})
    (line,) = bill.lines
    assert (line.sku, line.quantity) == ("PEN-1", 9)
    assert (line.tier_percent, line.discount_percent) == (D("0"), D("10"))
    assert amounts(line) == (D("13.50"), D("1.35"), D("12.15"), D("1.00"), D("13.15"))
    assert bill.total == D("13.15")
    assert bill == store.quote([("PEN-1", 9)], "SAVE10")


def test_tier_follows_the_shipped_quantity_and_adds_to_the_coupon():
    store = build_store()
    bill = cancelled_after_shipping(store, [("PEN-1", 12)], "SAVE10", {"PEN-1": 10})
    (line,) = bill.lines
    assert line.discount_percent == D("15")
    assert amounts(line) == (D("15.00"), D("2.25"), D("12.75"), D("1.05"), D("13.80"))


def test_the_combined_discount_is_still_capped():
    store = build_store()
    capped = cancelled_after_shipping(store, [("WIDGET", 25)], "big", {"WIDGET": 20})
    (line,) = capped.lines
    assert (line.tier_percent, line.discount_percent) == (D("30"), D("40"))
    assert amounts(line) == (
        D("399.80"),
        D("159.92"),
        D("239.88"),
        D("19.79"),
        D("259.67"),
    )
    store = build_store()
    small = cancelled_after_shipping(store, [("WIDGET", 25)], "BIG", {"WIDGET": 4})
    (line,) = small.lines
    assert line.discount_percent == D("25")
    assert amounts(line) == (D("79.96"), D("19.99"), D("59.97"), D("4.95"), D("64.92"))


def test_only_shipped_lines_are_billed_with_the_coupon():
    store = build_store()
    bill = cancelled_after_shipping(
        store, [("PEN-1", 12), ("BOOK-1", 3)], "SAVE10", {"BOOK-1": 3}
    )
    (line,) = bill.lines
    assert (line.sku, line.quantity) == ("BOOK-1", 3)
    assert amounts(line) == (D("14.97"), D("1.50"), D("13.47"), D("0.00"), D("13.47"))
    assert (bill.subtotal, bill.discount, bill.tax, bill.total) == (
        D("14.97"),
        D("1.50"),
        D("0.00"),
        D("13.47"),
    )


def test_other_bills_are_unchanged():
    store = build_store()
    done = store.create_order([("PEN-1", 12)], "SAVE10")
    store.reserve_order(done.id)
    store.fulfil_order(done.id)
    assert store.billable(done.id) == done.quote
    assert done.quote.total == D("16.56")
    nothing = store.create_order([("PEN-1", 12)], "SAVE10")
    store.reserve_order(nothing.id)
    store.cancel_order(nothing.id)
    assert store.billable(nothing.id).lines == ()
    assert str(store.billable(nothing.id).total) == "0.00"
    plain = cancelled_after_shipping(store, [("PEN-1", 12)], None, {"PEN-1": 9})
    assert plain.total == D("14.61")
