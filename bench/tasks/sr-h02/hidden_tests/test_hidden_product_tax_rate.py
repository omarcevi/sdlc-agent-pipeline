from decimal import Decimal

import pytest
from stockroom import Product, Store, Tier, Unit, csvio
from stockroom.errors import CsvFormatError, MoneyError

D = Decimal
OLD_HEADER = "sku,name,unit,price,taxable,tiers\n"


def build_store():
    """Store rate 8.25 %. PEN-1: 1.50, tier 10 -> 5 %. BOOK-1: 4.99 at its own 5 %."""
    store = Store(tax_percent="8.25")
    store.add_product(
        Product("PEN-1", "Pen", Unit.EACH, D("1.50"), tiers=(Tier(10, D("5")),))
    )
    store.add_product(
        Product("BOOK-1", "Notebook", Unit.EACH, D("4.99"), tax_percent=D("5"))
    )
    store.receive("PEN-1", 100)
    store.receive("BOOK-1", 20)
    return store


def write(tmp_path, text):
    path = tmp_path / "products.csv"
    path.write_text(text, encoding="utf-8")
    return path


def taxes(quote):
    return [(line.sku, line.net, line.tax, line.total) for line in quote.lines]


def test_a_product_with_its_own_rate_is_taxed_at_that_rate():
    store = build_store()
    quote = store.quote([("PEN-1", 12), ("BOOK-1", 3)])
    assert taxes(quote) == [
        ("PEN-1", D("17.10"), D("1.41"), D("18.51")),
        ("BOOK-1", D("14.97"), D("0.75"), D("15.72")),
    ]
    assert (quote.tax, quote.total) == (D("2.16"), D("34.23"))


def test_rates_are_given_like_the_store_rate():
    store = Store(tax_percent="10")
    for sku, rate in (("A-1", "2.5"), ("A-2", 7), ("A-3", D("0")), ("A-4", None)):
        store.add_product(
            Product(sku, "Thing", Unit.EACH, D("10.00"), tax_percent=rate)
        )
    quote = store.quote([("A-1", 1), ("A-2", 1), ("A-3", 1), ("A-4", 1)])
    assert [line.tax for line in quote.lines] == [
        D("0.25"),
        D("0.70"),
        D("0"),
        D("1.00"),
    ]
    for bad in (5.0, "101", "-1", "abc", True):
        with pytest.raises(MoneyError):
            Product("B-1", "Bad", Unit.EACH, D("1.00"), tax_percent=bad)


def test_a_product_that_is_not_taxable_is_never_taxed():
    store = Store(tax_percent="8.25")
    store.add_product(
        Product("GIFT-1", "Gift card", Unit.EACH, D("20.00"), False, tax_percent="5")
    )
    (line,) = store.quote([("GIFT-1", 2)]).lines
    assert (line.net, line.tax, line.total) == (D("40.00"), D("0"), D("40.00"))


def test_orders_and_bills_use_the_product_rate():
    store = build_store()
    order = store.create_order([("BOOK-1", 10), ("PEN-1", 2)])
    assert taxes(order.quote) == [
        ("BOOK-1", D("49.90"), D("2.50"), D("52.40")),
        ("PEN-1", D("3.00"), D("0.25"), D("3.25")),
    ]
    store.reserve_order(order.id)
    store.fulfil_order(order.id, {"BOOK-1": 3})
    store.cancel_order(order.id)
    assert taxes(store.billable(order.id)) == [
        ("BOOK-1", D("14.97"), D("0.75"), D("15.72")),
    ]


def test_changing_the_price_keeps_the_rate():
    store = build_store()
    store.catalog.set_price("BOOK-1", "6.00")
    (line,) = store.quote([("BOOK-1", 1)]).lines
    assert (line.net, line.tax) == (D("6.00"), D("0.30"))


def test_products_files_may_carry_a_rate(tmp_path):
    text = (
        "tax_percent,sku,name,unit,price,taxable,tiers\n"
        "5,book-2,Atlas,each,20.00,true,\n"  # line 2
        ",pen-2,Pencil,each,0.50,true,\n"  # line 3: the store's rate
        "abc,mug-1,Mug,each,4.00,true,\n"  # line 4: bad rate
        "101,cup-1,Cup,each,3.00,true,\n"  # line 5: bad rate
        " 0 ,map-1,Map,each,10.00,true,\n"  # line 6: no tax
    )
    store = Store(tax_percent="10")
    result = store.import_products(write(tmp_path, text))
    assert [error.line for error in result.errors] == [4, 5]
    assert [product.sku for product in result.products] == ["BOOK-2", "PEN-2", "MAP-1"]
    quote = store.quote([("BOOK-2", 1), ("PEN-2", 10), ("MAP-1", 1)])
    assert [line.tax for line in quote.lines] == [D("1.00"), D("0.50"), D("0")]


def test_products_files_without_the_column_work_as_before(tmp_path):
    store = Store(tax_percent="10")
    result = store.import_products(
        write(tmp_path, OLD_HEADER + "pen-2,Pencil,each,0.50,true,\n")
    )
    assert result.errors == ()
    (line,) = store.quote([("PEN-2", 10)]).lines
    assert line.tax == D("0.50")
    for header in (
        "sku,name,unit,price,taxable,tiers,tax_percent,extra\n",
        "sku,name,unit,price,taxable,tax_percent\n",
        "sku,name,unit,price,taxable,tiers,rate\n",
    ):
        with pytest.raises(CsvFormatError):
            csvio.import_products(write(tmp_path, header))
