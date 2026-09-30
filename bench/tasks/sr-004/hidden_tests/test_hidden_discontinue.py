from decimal import Decimal

import pytest
from stockroom import OrderStatus, Product, StockroomError, Store, Tier, Unit
from stockroom.errors import DuplicateSku, UnknownSku

D = Decimal


def build_store():
    """Tax 10 %. OLD-1: 20.00, tier 5 -> 10 %, 30 on hand. PEN-1: 1.50, 100 on hand."""
    store = Store(tax_percent="10")
    store.add_product(
        Product("OLD-1", "Old lamp", Unit.EACH, D("20.00"), tiers=(Tier(5, D("10")),))
    )
    store.add_product(Product("PEN-1", "Pen", Unit.EACH, D("1.50")))
    store.receive("OLD-1", 30)
    store.receive("PEN-1", 100)
    return store


def levels(store, sku):
    level = store.stock(sku)
    return level.on_hand, level.reserved, level.available


def test_a_discontinued_product_cannot_be_quoted_or_ordered():
    store = build_store()
    store.discontinue("old-1")
    for items in ([("OLD-1", 1)], [("PEN-1", 2), ("old-1", 1)]):
        with pytest.raises(StockroomError):
            store.quote(items)
        with pytest.raises(StockroomError):
            store.create_order(items)
    assert store.orders.orders() == []
    assert store.quote([("PEN-1", 2)]).total == D("3.30")
    assert store.create_order([("PEN-1", 2)]).id == "ORD-0001"


def test_discontinuing_an_unknown_sku_fails():
    store = build_store()
    with pytest.raises(UnknownSku):
        store.discontinue("GHOST-1")
    assert store.quote([("OLD-1", 1)]).total == D("22.00")


def test_orders_created_before_carry_on():
    store = build_store()
    new = store.create_order([("OLD-1", 6), ("PEN-1", 10)])
    partly = store.create_order([("OLD-1", 4)])
    store.reserve_order(partly.id)
    dropped = store.create_order([("OLD-1", 5)])
    store.reserve_order(dropped.id)
    store.discontinue("OLD-1")

    store.reserve_order(new.id)
    store.fulfil_order(new.id)
    assert new.status is OrderStatus.FULFILLED
    assert store.billable(new.id) == new.quote
    assert new.quote.total == D("135.30")

    store.fulfil_order(partly.id, {"OLD-1": 1})
    store.cancel_order(partly.id)
    bill = store.billable(partly.id)
    assert [(line.sku, line.quantity) for line in bill.lines] == [("OLD-1", 1)]
    assert bill.total == D("22.00")

    store.cancel_order(dropped.id)
    assert dropped.status is OrderStatus.CANCELLED
    assert levels(store, "OLD-1") == (23, 0, 23)


def test_the_stock_of_a_discontinued_product_is_still_tracked(tmp_path):
    store = build_store()
    store.discontinue("OLD-1")
    store.ship("OLD-1", 5, "back to supplier")
    store.adjust("OLD-1", -2, "stocktake")
    assert levels(store, "old-1") == (23, 0, 23)
    path = tmp_path / "stock.csv"
    store.export_stock(path)
    assert path.read_text(encoding="utf-8").splitlines() == [
        "sku,name,unit,on_hand,reserved,available,stock_value",
        "OLD-1,Old lamp,each,23,0,23,460.00",
        "PEN-1,Pen,each,100,0,100,150.00",
    ]


def test_a_discontinued_sku_stays_taken(tmp_path):
    store = build_store()
    store.discontinue("OLD-1")
    with pytest.raises(DuplicateSku):
        store.add_product(Product("OLD-1", "New lamp", Unit.EACH, D("25.00")))
    path = tmp_path / "products.csv"
    path.write_text(
        "sku,name,unit,price,taxable,tiers\nold-1,New lamp,each,25.00,true,\n",
        encoding="utf-8",
    )
    result = store.import_products(path)
    assert result.products == ()
    assert [error.line for error in result.errors] == [2]
    export = tmp_path / "stock.csv"
    store.export_stock(export)
    assert "OLD-1,Old lamp,each,30,0,30,600.00" in export.read_text().splitlines()
    with pytest.raises(StockroomError):
        store.create_order([("OLD-1", 1)])
