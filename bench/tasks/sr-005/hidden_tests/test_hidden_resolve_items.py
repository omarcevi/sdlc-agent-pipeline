from decimal import Decimal

import pytest
from stockroom import Product, Store, Tier, Unit
from stockroom.errors import InvalidQuantity, InvalidSku, UnknownSku

D = Decimal


def build_store():
    """Tax 8.25 %. PEN-1: 1.50, tier 10 -> 5 %, 100 on hand. BOOK-1: 4.99, untaxed."""
    store = Store(tax_percent="8.25")
    store.add_product(
        Product("PEN-1", "Pen", Unit.EACH, D("1.50"), tiers=(Tier(10, D("5")),))
    )
    store.add_product(Product("BOOK-1", "Notebook", Unit.EACH, D("4.99"), False))
    store.receive("PEN-1", 100)
    store.receive("BOOK-1", 20)
    return store


def test_resolve_items_merges_repeated_skus_in_order_of_first_appearance():
    store = build_store()
    resolved = list(
        store.catalog.resolve_items([("pen-1", 6), ("BOOK-1", 1), ("PEN-1", 6)])
    )
    assert [(product.sku, quantity) for product, quantity in resolved] == [
        ("PEN-1", 12),
        ("BOOK-1", 1),
    ]
    assert resolved[0][0] == store.catalog.get("PEN-1")
    assert resolved[1][0] == store.catalog.get("BOOK-1")
    from_generator = list(store.catalog.resolve_items(iter([("book-1", 2)])))
    assert [(product.sku, quantity) for product, quantity in from_generator] == [
        ("BOOK-1", 2)
    ]


@pytest.mark.parametrize(
    ("items", "error"),
    [
        ([("GHOST-1", 1)], UnknownSku),
        ([("PEN-1", 1), ("GHOST-1", 1)], UnknownSku),
        ([("not a sku", 1)], InvalidSku),
        ([("PEN-1", 0)], InvalidQuantity),
        ([("PEN-1", -2)], InvalidQuantity),
        ([("PEN-1", True)], InvalidQuantity),
    ],
)
def test_resolve_items_rejects_bad_items(items, error):
    store = build_store()
    with pytest.raises(error):
        store.catalog.resolve_items(items)


def test_quoting_and_order_creation_go_through_resolve_items(monkeypatch):
    store = build_store()
    original = store.catalog.resolve_items
    calls = []

    def spy(self, items):
        items = list(items)
        calls.append(items)
        return original(items)

    monkeypatch.setattr(type(store.catalog), "resolve_items", spy)
    quote = store.quote([("pen-1", 6), ("PEN-1", 6)])
    assert calls
    assert [(line.sku, line.quantity) for line in quote.lines] == [("PEN-1", 12)]
    seen = len(calls)
    order = store.create_order([("BOOK-1", 2)])
    assert len(calls) > seen
    assert [(line.product.sku, line.quantity) for line in order.lines] == [
        ("BOOK-1", 2)
    ]


def test_an_empty_quote_is_still_all_zeros_and_an_empty_order_is_refused():
    store = build_store()
    quote = store.quote([])
    assert len(quote.lines) == 0
    amounts = (quote.subtotal, quote.discount, quote.net, quote.tax, quote.total)
    assert [str(amount) for amount in amounts] == ["0.00"] * 5
    with pytest.raises(InvalidQuantity):
        store.create_order([])
    assert store.orders.orders() == []


def test_orders_keep_the_products_they_were_priced_with():
    store = build_store()
    order = store.create_order([("PEN-1", 6), ("pen-1", 6)])
    store.reserve_order(order.id)
    store.fulfil_order(order.id, {"PEN-1": 9})
    store.catalog.set_price("PEN-1", "2.00")
    store.cancel_order(order.id)
    assert order.lines[0].product.list_price == D("1.50")
    bill = store.billable(order.id)
    assert [(line.sku, line.quantity, line.unit_price) for line in bill.lines] == [
        ("PEN-1", 9, D("1.50"))
    ]
    assert bill.total == D("14.61")
    assert store.quote([("PEN-1", 9)]).lines[0].unit_price == D("2.00")


def test_quotes_and_orders_still_agree_and_bad_orders_record_nothing():
    store = build_store()
    items = [("pen-1", 6), ("BOOK-1", 3), ("PEN-1", 6)]
    order = store.create_order(items)
    assert store.quote(items) == order.quote
    assert order.quote.total == D("33.48")
    for bad in ([("PEN-1", 1), ("GHOST-1", 1)], [("PEN-1", 0)]):
        with pytest.raises((UnknownSku, InvalidQuantity)):
            store.create_order(bad)
    assert [recorded.id for recorded in store.orders.orders()] == ["ORD-0001"]
    assert store.create_order([("BOOK-1", 1)]).id == "ORD-0002"
