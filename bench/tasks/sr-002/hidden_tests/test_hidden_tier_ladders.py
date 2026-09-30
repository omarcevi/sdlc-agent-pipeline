from decimal import Decimal

import pytest
from stockroom import Product, Store, Tier, Unit, csvio
from stockroom.errors import CatalogError

D = Decimal
HEADER = "sku,name,unit,price,taxable,tiers\n"


def write(tmp_path, text):
    path = tmp_path / "products.csv"
    path.write_text(text, encoding="utf-8")
    return path


def glue(tiers):
    return Product("GLUE-1", "Glue stick", Unit.EACH, D("2.00"), tiers=tiers)


def test_import_reports_the_broken_ladder_and_keeps_the_other_rows(tmp_path):
    store = Store(tax_percent="0")
    rows = (
        "cab-1,Cable,each,3.50,true,10:5;50:10\n"  # line 2
        "glue-1,Glue stick,each,2.00,true,5:10;20:5\n"  # line 3
        "lamp,Lamp,each,12.00,false,\n"  # line 4
    )
    result = store.import_products(write(tmp_path, HEADER + rows))
    assert [error.line for error in result.errors] == [3]
    assert [product.sku for product in result.products] == ["CAB-1", "LAMP"]
    assert "GLUE-1" not in store.catalog
    assert [product.sku for product in store.catalog.products()] == ["CAB-1", "LAMP"]


@pytest.mark.parametrize(
    "tiers",
    [
        (Tier(5, D("10")), Tier(20, D("5"))),
        (Tier(20, D("5")), Tier(5, D("10"))),
        (Tier(2, D("3")), Tier(50, D("6")), Tier(10, D("8"))),
    ],
)
def test_a_product_whose_discount_falls_with_quantity_is_refused(tiers):
    with pytest.raises(CatalogError):
        glue(tiers)


def test_rising_ladders_in_any_order_are_kept_in_quantity_order(tmp_path):
    product = Product(
        "CAB-1",
        "Cable",
        Unit.EACH,
        D("3.50"),
        tiers=(Tier(50, D("12.5")), Tier(10, D("5"))),
    )
    assert [(tier.min_qty, tier.percent) for tier in product.tiers] == [
        (10, D("5")),
        (50, D("12.5")),
    ]
    text = HEADER + "cab-1,Cable,each,3.50,true,50:12.5;10:5\n"
    result = csvio.import_products(write(tmp_path, text))
    assert result.errors == ()
    assert result.products[0].tiers == product.tiers


def test_quotes_use_the_largest_step_reached():
    store = Store(tax_percent="0")
    ladder = (Tier(50, D("12.5")), Tier(10, D("5")), Tier(20, D("8")))
    store.add_product(Product("CAB-1", "Cable", Unit.EACH, D("3.50"), tiers=ladder))
    got = [
        store.quote([("CAB-1", quantity)]).lines[0].discount_percent
        for quantity in (9, 10, 19, 20, 49, 50, 500)
    ]
    assert got == [D(0), D(5), D(5), D(8), D(8), D("12.5"), D("12.5")]
