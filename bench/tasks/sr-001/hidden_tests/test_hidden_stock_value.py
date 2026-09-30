from decimal import Decimal

from stockroom import Product, Store, Unit

HEADER = "sku,name,unit,on_hand,reserved,available,stock_value"


def pen_store():
    store = Store()
    store.add_product(Product("PEN-1", "Pen", Unit.EACH, Decimal("1.50")))
    store.receive("PEN-1", 100)
    return store


def export_rows(store, tmp_path):
    path = tmp_path / "stock.csv"
    store.export_stock(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    return lines[1:]


def test_reserved_units_still_count_towards_stock_value(tmp_path):
    store = pen_store()
    order = store.create_order([("PEN-1", 40)])
    store.reserve_order(order.id)
    assert export_rows(store, tmp_path) == ["PEN-1,Pen,each,100,40,60,150.00"]


def test_stock_value_goes_down_only_when_units_leave(tmp_path):
    store = pen_store()
    order = store.create_order([("PEN-1", 40)])
    store.reserve_order(order.id)
    store.fulfil_order(order.id, {"PEN-1": 15})
    assert export_rows(store, tmp_path) == ["PEN-1,Pen,each,85,25,60,127.50"]
    store.ship("PEN-1", 5)
    assert export_rows(store, tmp_path) == ["PEN-1,Pen,each,80,25,55,120.00"]
    store.cancel_order(order.id)
    assert export_rows(store, tmp_path) == ["PEN-1,Pen,each,80,0,80,120.00"]


def test_every_row_is_valued_at_its_own_units_on_hand(tmp_path):
    store = pen_store()
    store.add_product(Product("BOOK-1", "Notebook", Unit.EACH, Decimal("4.99")))
    store.add_product(Product("WIDGET", "Widget", Unit.BOX, Decimal("19.99")))
    store.receive("BOOK-1", 20)
    store.adjust("BOOK-1", -2, "damaged")
    store.receive("WIDGET", 30)
    order = store.create_order([("PEN-1", 40), ("WIDGET", 30)])
    store.reserve_order(order.id)
    path = tmp_path / "stock.csv"
    store.export_stock(path)
    assert path.read_bytes() == (
        b"sku,name,unit,on_hand,reserved,available,stock_value\n"
        b"BOOK-1,Notebook,each,18,0,18,89.82\n"
        b"PEN-1,Pen,each,100,40,60,150.00\n"
        b"WIDGET,Widget,box,30,30,0,599.70\n"
    )
