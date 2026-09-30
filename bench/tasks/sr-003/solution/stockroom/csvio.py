"""CSV import of products and stock counts, and export of stock levels.

Import file columns, in any order: `sku,name,unit,price,taxable,tiers`.
`taxable` is `true` or `false`. `tiers` is empty or a `;`-separated list of
`min_qty:percent` steps such as `10:5;50:12.5`.

Stocktake file columns, in any order: `sku,counted`. `counted` is the whole
number of units found on the shelf.

Export file columns: `sku,name,unit,on_hand,reserved,available,stock_value`,
one row per product, sorted by SKU, with `\\n` line endings.
"""

import csv
import re
from collections.abc import Container, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from .catalog import Product, Tier, Unit, normalize_sku
from .errors import CatalogError, CsvFormatError, InvalidQuantity, StockroomError
from .money import format_money, parse_percent

if TYPE_CHECKING:
    from .store import Store

IMPORT_COLUMNS = ("sku", "name", "unit", "price", "taxable", "tiers")
STOCKTAKE_COLUMNS = ("sku", "counted")
_COUNT_PATTERN = re.compile(r"[0-9]+")
EXPORT_COLUMNS = (
    "sku",
    "name",
    "unit",
    "on_hand",
    "reserved",
    "available",
    "stock_value",
)


@dataclass(frozen=True)
class RowError:
    """A rejected row. `line` is the row's line number in the file (the header is 1)."""

    line: int
    message: str


@dataclass(frozen=True)
class ImportResult:
    """Products that were accepted and rows that were not, each in file order."""

    products: tuple[Product, ...]
    errors: tuple[RowError, ...]


@dataclass(frozen=True)
class StockCount:
    """One stocktake row: `counted` units of `sku` found, from file line `line`."""

    line: int
    sku: str
    counted: int


@dataclass(frozen=True)
class StocktakeResult:
    """Counts that were read and rows that were not, each in file order."""

    counts: tuple[StockCount, ...]
    errors: tuple[RowError, ...]


def _read_rows(
    path: str | Path, columns: Sequence[str]
) -> Iterator[tuple[int, dict[str, str | None]]]:
    """Yield `(line, row)` for each data row of a CSV file with exactly `columns`.

    A file that is empty, not UTF-8, malformed for the csv module, or lacks
    exactly the expected columns raises `CsvFormatError`."""
    try:
        with open(path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames
            if header is None:
                raise CsvFormatError("the file is empty")
            if sorted(name.strip() for name in header) != sorted(columns):
                raise CsvFormatError(f"expected columns {','.join(columns)}")
            reader.fieldnames = [name.strip() for name in header]
            for row in reader:
                yield reader.line_num, row
    except UnicodeDecodeError:
        raise CsvFormatError("the file is not valid UTF-8") from None
    except csv.Error as exc:
        raise CsvFormatError(f"the file is not valid CSV: {exc}") from None


def _fields(row: dict[str, str | None]) -> dict[str, str]:
    """The row's values, or an error if it has more or fewer fields than the header."""
    if None in row:
        raise CatalogError("row has more fields than the header")
    if any(value is None for value in row.values()):
        raise CatalogError("row has fewer fields than the header")
    return {key: value or "" for key, value in row.items()}


def parse_tiers(text: str) -> tuple[Tier, ...]:
    """Parse the `tiers` column. An empty string means no tiers."""
    text = text.strip()
    if not text:
        return ()
    tiers = []
    for part in text.split(";"):
        min_qty, separator, percent = part.partition(":")
        if not separator:
            raise CatalogError(f"tier must look like min_qty:percent: {part!r}")
        try:
            quantity = int(min_qty.strip())
        except ValueError:
            raise CatalogError(
                f"tier quantity is not a whole number: {part!r}"
            ) from None
        tiers.append(Tier(quantity, parse_percent(percent)))
    return tuple(tiers)


def parse_taxable(text: str) -> bool:
    """`true` or `false`, in any case."""
    value = text.strip().lower()
    if value not in ("true", "false"):
        raise CatalogError(f"taxable must be true or false: {text!r}")
    return value == "true"


def import_products(path: str | Path, existing: Container[str] = ()) -> ImportResult:
    """Read products from a CSV file.

    A bad row is skipped and reported; the good rows are still returned. A row
    is bad if a field is invalid, if fields are missing or extra, or if its SKU
    appeared earlier in the file or is in `existing`. A file that is empty, not
    UTF-8, malformed for the csv module, or lacks exactly the expected columns
    raises `CsvFormatError`."""
    products: list[Product] = []
    errors: list[RowError] = []
    seen: set[str] = set()
    for line, row in _read_rows(path, IMPORT_COLUMNS):
        try:
            product = _parse_row(row, seen, existing)
        except StockroomError as exc:
            errors.append(RowError(line, str(exc)))
        else:
            seen.add(product.sku)
            products.append(product)
    return ImportResult(tuple(products), tuple(errors))


def _parse_row(
    row: dict[str, str | None], seen: set[str], existing: Container[str]
) -> Product:
    fields = _fields(row)
    sku = normalize_sku(fields["sku"])
    if sku in seen or sku in existing:
        raise CatalogError(f"duplicate SKU: {sku}")
    return Product(
        sku=sku,
        name=fields["name"],
        unit=Unit.parse(fields["unit"]),
        list_price=fields["price"],
        taxable=parse_taxable(fields["taxable"]),
        tiers=parse_tiers(fields["tiers"]),
    )


def parse_count(text: str) -> int:
    """A counted number of units: a whole number of 0 or more."""
    value = text.strip()
    if not _COUNT_PATTERN.fullmatch(value):
        raise InvalidQuantity(f"count must be a whole number of 0 or more: {text!r}")
    return int(value)


def read_stocktake(path: str | Path) -> StocktakeResult:
    """Read shelf counts from a stocktake CSV file.

    A bad row is skipped and reported; the good rows are still returned. A row
    is bad if fields are missing or extra, the SKU is malformed or appeared
    earlier in the file, or the count is not a whole number of 0 or more.
    Whether a SKU is in the catalog is for the caller to check. A file that is
    empty, not UTF-8, malformed for the csv module, or lacks exactly the
    expected columns raises `CsvFormatError`."""
    counts: list[StockCount] = []
    errors: list[RowError] = []
    seen: set[str] = set()
    for line, row in _read_rows(path, STOCKTAKE_COLUMNS):
        try:
            fields = _fields(row)
            sku = normalize_sku(fields["sku"])
            if sku in seen:
                raise CatalogError(f"duplicate SKU: {sku}")
            counted = parse_count(fields["counted"])
        except StockroomError as exc:
            errors.append(RowError(line, str(exc)))
        else:
            seen.add(sku)
            counts.append(StockCount(line, sku, counted))
    return StocktakeResult(tuple(counts), tuple(errors))


def export_stock(store: "Store", path: str | Path) -> None:
    """Write one row per product with its stock levels and stock value.

    `stock_value` is on-hand units times the list price, in cents precision."""
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(EXPORT_COLUMNS)
        for product in store.catalog.products():
            level = store.inventory.level(product.sku)
            value: Decimal = product.list_price * level.on_hand
            writer.writerow(
                [
                    product.sku,
                    product.name,
                    product.unit.value,
                    level.on_hand,
                    level.reserved,
                    level.available,
                    format_money(value),
                ]
            )
