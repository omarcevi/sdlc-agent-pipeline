from decimal import Decimal

import pytest
from stockroom import Store, Tier, csvio
from stockroom.csvio import parse_tiers
from stockroom.errors import CsvFormatError

D = Decimal

HEADER = "sku,name,unit,price,taxable,tiers\n"

MIXED = (
    HEADER
    + "cab-1,Cable,each,3.50,true,10:5;50:10\n"  # line 2: good
    + "BAD SKU,Broken,each,1.00,true,\n"  # line 3: bad SKU
    + "pen-2,Pencil,each,1.005,true,\n"  # line 4: three decimals
    + "mug-1,Mug,box,4.00,maybe,\n"  # line 5: taxable is not a boolean
    + "CAB-1,Cable again,each,3.00,true,\n"  # line 6: duplicate of line 2
    + "\n"  # line 7: blank, skipped
    + "lamp,Lamp,each,12.00,false,\n"  # line 8: good
    + "short,Short,each\n"  # line 9: too few fields
    + "long,Long,each,1.00,true,,extra\n"  # line 10: too many fields
)


def write(tmp_path, text):
    path = tmp_path / "products.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_r16_a_good_file_imports_every_row(tmp_path):
    """R16: columns in the documented form, tiers parsed."""
    rows = "cab-1,Cable,kg,3.50,TRUE,10:5;50:12.5\nlamp,Lamp,each,12,false,\n"
    result = csvio.import_products(write(tmp_path, HEADER + rows))
    assert result.errors == ()
    cable, lamp = result.products
    assert (cable.sku, cable.unit.value, cable.taxable) == ("CAB-1", "kg", True)
    assert cable.list_price == Decimal("3.50")
    assert cable.tiers == (Tier(10, Decimal("5")), Tier(50, Decimal("12.5")))
    assert (lamp.taxable, lamp.tiers, str(lamp.list_price)) == (False, (), "12.00")
    text = "name,price,sku,tiers,unit,taxable\nCable,3.50,cab-1,,each,true\n"
    shuffled = csvio.import_products(write(tmp_path, text))  # any column order
    assert [p.sku for p in shuffled.products] == ["CAB-1"]


@pytest.mark.parametrize(
    "text", ["", "sku,name,unit,price,taxable\n", HEADER.strip() + ",extra\n"]
)
def test_r16_an_unusable_file_is_a_format_error(tmp_path, text):
    """R16: empty files and other columns raise CsvFormatError."""
    with pytest.raises(CsvFormatError):
        csvio.import_products(write(tmp_path, text))


def test_r16_a_file_that_is_not_utf8_is_a_format_error(tmp_path):
    """R16: undecodable bytes raise CsvFormatError."""
    path = tmp_path / "bad.csv"
    path.write_bytes(HEADER.encode() + b"x-1,Bad\xe9,each,1.00,true,\n")
    with pytest.raises(CsvFormatError):
        csvio.import_products(path)


def test_r17_good_rows_are_kept_and_bad_rows_reported_with_line_numbers(tmp_path):
    """R17: file line numbers, header is line 1, blank lines still count."""
    result = csvio.import_products(write(tmp_path, MIXED))
    assert [p.sku for p in result.products] == ["CAB-1", "LAMP"]
    assert [e.line for e in result.errors] == [3, 4, 5, 6, 9, 10]
    by_line = {e.line: e.message for e in result.errors}
    assert "duplicate" in by_line[6] and "decimal" in by_line[4]
    assert "fewer" in by_line[9] and "more" in by_line[10]


def test_r17_import_never_overwrites_an_existing_product(store, tmp_path):
    """R17: a SKU already in the catalog is a bad row; the rest is imported."""
    rows = "pen-1,Other pen,each,9.99,true,\nnew-1,New,each,2.00,true,\n"
    before = len(store.ledger)
    result = store.import_products(write(tmp_path, HEADER + rows))
    assert [(e.line, "duplicate" in e.message) for e in result.errors] == [(2, True)]
    assert store.catalog.get("PEN-1").list_price == Decimal("1.50")
    assert store.catalog.get("NEW-1").name == "New"
    assert len(store.ledger) == before and store.stock("NEW-1").on_hand == 0


def test_r17_bad_tiers_are_bad_rows(tmp_path):
    """R17 and R4: tier syntax and tier rules are checked per row."""
    bad = ("10", "x:5", "1:5", "10:5;10:6", "10:5;20:5", "10:0", "10:150")
    rows = "".join(f"p-{i},P,each,1.00,true,{t}\n" for i, t in enumerate(bad))
    result = csvio.import_products(write(tmp_path, HEADER + rows))
    assert result.products == ()
    assert [e.line for e in result.errors] == list(range(2, 9))
    assert parse_tiers(" 10 : 5 ; 20:7.5 ") == (Tier(10, D("5")), Tier(20, D("7.5")))
    assert parse_tiers("") == ()


def test_r18_export_writes_the_documented_file(store, tmp_path):
    """R18: columns, sorted rows, two-decimal values, LF line endings."""
    path = tmp_path / "stock.csv"
    store.export_stock(path)
    assert path.read_bytes() == (
        b"sku,name,unit,on_hand,reserved,available,stock_value\n"
        b"BOOK-1,Notebook,each,20,0,20,99.80\n"
        b"PEN-1,Pen,each,100,0,100,150.00\n"
        b"WIDGET,Widget,box,30,0,30,599.70\n"
    )


def test_r18_import_then_export_round_trips_the_catalog(tmp_path):
    """R16 and R18: imported products appear in the export, quoted and empty ones too."""
    fresh = Store(tax_percent="0")
    fresh.import_products(write(tmp_path, MIXED))
    fresh.import_products(
        write(tmp_path, HEADER + 'mix-1,"Pen, blue",each,0.99,true,\n')
    )
    fresh.receive("lamp", 4)
    path = tmp_path / "out.csv"
    fresh.export_stock(path)
    assert path.read_text().splitlines()[1:] == [
        "CAB-1,Cable,each,0,0,0,0.00",
        "LAMP,Lamp,each,4,0,4,48.00",
        'MIX-1,"Pen, blue",each,0,0,0,0.00',
    ]


def test_r16_a_csv_parser_error_is_a_format_error(tmp_path):
    """R16: a field over the csv module's size limit makes it fail; unusable file."""
    path = tmp_path / "huge.csv"
    path.write_text(HEADER + "x-1," + "n" * 200_000 + ",each,1.00,true,\n")
    with pytest.raises(CsvFormatError):
        csvio.import_products(path)
