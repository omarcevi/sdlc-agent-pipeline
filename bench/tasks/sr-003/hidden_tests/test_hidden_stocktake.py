from decimal import Decimal

import pytest
from stockroom import Product, Store, Unit
from stockroom.errors import CsvFormatError
from stockroom.ledger import MovementKind

HEADER = "sku,counted\n"
ADJUST = MovementKind.ADJUST


def build_store():
    """On hand: PEN-1 100, BOOK-1 20, CUP-1 7, MUG-1 12, LAMP-1 5."""
    store = Store(tax_percent="0")
    for sku, name, price, units in (
        ("PEN-1", "Pen", "1.50", 100),
        ("BOOK-1", "Notebook", "4.99", 20),
        ("CUP-1", "Cup", "3.00", 7),
        ("MUG-1", "Mug", "6.00", 12),
        ("LAMP-1", "Lamp", "12.00", 5),
    ):
        store.add_product(Product(sku, name, Unit.EACH, Decimal(price)))
        store.receive(sku, units)
    return store


def write(tmp_path, content):
    path = tmp_path / "count.csv"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


def on_hand(store):
    return {level.sku: level.on_hand for level in store.inventory.snapshot()}


def new_entries(store, before):
    return store.ledger.entries()[before:]


def test_counts_become_on_hand_and_each_change_is_one_adjustment(tmp_path):
    store = build_store()
    before = len(store.ledger)
    rows = "pen-1,97\nBOOK-1,24\nCUP-1,7\nMUG-1,0\n"
    errors = store.import_stocktake(write(tmp_path, HEADER + rows))
    assert list(errors) == []
    assert on_hand(store) == {
        "BOOK-1": 24,
        "CUP-1": 7,
        "LAMP-1": 5,
        "MUG-1": 0,
        "PEN-1": 97,
    }
    entries = new_entries(store, before)
    assert len(entries) == 3
    assert {(m.sku, m.kind, m.quantity, m.reason) for m in entries} == {
        ("PEN-1", ADJUST, -3, "stocktake"),
        ("BOOK-1", ADJUST, 4, "stocktake"),
        ("MUG-1", ADJUST, -12, "stocktake"),
    }


def test_the_columns_may_come_in_any_order(tmp_path):
    store = build_store()
    errors = store.import_stocktake(write(tmp_path, "counted,sku\n98,PEN-1\n"))
    assert list(errors) == []
    assert store.stock("PEN-1").on_hand == 98


def test_bad_rows_are_reported_and_the_good_rows_still_apply(tmp_path):
    store = build_store()
    before = len(store.ledger)
    rows = (
        "PEN-1,97\n"  # line 2: good
        "GHOST-1,5\n"  # line 3: not in the catalog
        "BOOK-1,-1\n"  # line 4: negative
        "CUP-1,2.5\n"  # line 5: not a whole number
        "MUG-1,\n"  # line 6: no count
        "not a sku,3\n"  # line 7: not in the catalog (not even a SKU)
        "PEN-1,90\n"  # line 8: PEN-1 was counted on line 2
        "LAMP-1,3\n"  # line 9: good
    )
    errors = store.import_stocktake(write(tmp_path, HEADER + rows))
    assert [error.line for error in errors] == [3, 4, 5, 6, 7, 8]
    assert on_hand(store) == {
        "BOOK-1": 20,
        "CUP-1": 7,
        "LAMP-1": 3,
        "MUG-1": 12,
        "PEN-1": 97,
    }
    assert len(new_entries(store, before)) == 2


def test_a_count_below_the_reserved_units_is_refused(tmp_path):
    store = build_store()
    store.reserve_order(store.create_order([("PEN-1", 10)]).id)
    before = len(store.ledger)
    errors = store.import_stocktake(write(tmp_path, HEADER + "PEN-1,8\nBOOK-1,19\n"))
    assert [error.line for error in errors] == [2]
    assert store.stock("PEN-1").on_hand == 100
    assert store.stock("BOOK-1").on_hand == 19
    assert len(new_entries(store, before)) == 1
    assert list(store.import_stocktake(write(tmp_path, HEADER + "PEN-1,10\n"))) == []
    level = store.stock("PEN-1")
    assert (level.on_hand, level.reserved, level.available) == (10, 10, 0)


@pytest.mark.parametrize(
    "content",
    [
        "",
        "sku,count\nPEN-1,97\n",
        "sku,counted,note\nPEN-1,97,x\n",
        b"sku,counted\nPEN-1,97\nBOOK-1,2\xe9\n",
    ],
)
def test_an_unusable_file_is_refused_as_a_whole(tmp_path, content):
    store = build_store()
    before = len(store.ledger)
    with pytest.raises(CsvFormatError):
        store.import_stocktake(write(tmp_path, content))
    assert len(store.ledger) == before
    assert store.stock("PEN-1").on_hand == 100
