"""The product catalog: SKUs, units, quantity tiers and products.

A product says what is sold and at which list price. It knows nothing about
stock levels (see `inventory`) or how a quantity is priced (see `pricing`).
"""

import re
from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import Enum
from itertools import pairwise

from .errors import (
    CatalogError,
    DuplicateSku,
    InvalidQuantity,
    InvalidSku,
    UnknownSku,
)
from .money import parse_money, parse_percent

MAX_SKU_LENGTH = 20
_SKU_PATTERN = re.compile(r"[A-Z0-9]+(-[A-Z0-9]+)*")


class Unit(Enum):
    """How a product is counted. Quantities are always whole numbers of a unit."""

    EACH = "each"
    PAIR = "pair"
    BOX = "box"
    CASE = "case"
    KILOGRAM = "kg"

    @classmethod
    def parse(cls, text: str) -> "Unit":
        """Look a unit up by its code, ignoring case and surrounding spaces."""
        code = text.strip().lower() if isinstance(text, str) else text
        for unit in cls:
            if unit.value == code:
                return unit
        raise CatalogError(f"unknown unit: {text!r}")


def normalize_sku(text: str) -> str:
    """Return the canonical form of a SKU: stripped and upper case.

    A SKU is letters and digits in groups separated by single hyphens, at most
    `MAX_SKU_LENGTH` characters."""
    if not isinstance(text, str):
        raise InvalidSku(f"SKU must be a string: {text!r}")
    sku = text.strip().upper()
    if len(sku) > MAX_SKU_LENGTH or not _SKU_PATTERN.fullmatch(sku):
        raise InvalidSku(f"invalid SKU: {text!r}")
    return sku


def check_quantity(quantity: object) -> int:
    """Return `quantity` if it is a positive whole number, else raise."""
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise InvalidQuantity(f"quantity must be a positive whole number: {quantity!r}")
    return quantity


@dataclass(frozen=True)
class Tier:
    """A quantity discount step: from `min_qty` units, `percent` % off the list price."""

    min_qty: int
    percent: Decimal

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_qty, bool)
            or not isinstance(self.min_qty, int)
            or self.min_qty < 2
        ):
            raise CatalogError(f"a tier starts at 2 units or more: {self.min_qty!r}")
        percent = parse_percent(self.percent)
        if percent <= 0:
            raise CatalogError("a tier discount must be above 0 %")
        object.__setattr__(self, "percent", percent)


@dataclass(frozen=True)
class Product:
    """A sellable item.

    `sku`, `name`, `list_price` and `tiers` are validated and normalised on
    creation: the SKU is upper case, the price has two decimals, and the tiers
    are sorted by `min_qty` with strictly rising percentages."""

    sku: str
    name: str
    unit: Unit
    list_price: Decimal
    taxable: bool = True
    tiers: tuple[Tier, ...] = field(default=())

    def __post_init__(self) -> None:
        object.__setattr__(self, "sku", normalize_sku(self.sku))
        name = self.name.strip() if isinstance(self.name, str) else ""
        if not name:
            raise CatalogError("a product needs a name")
        object.__setattr__(self, "name", name)
        if not isinstance(self.unit, Unit):
            raise CatalogError(f"unit must be a Unit: {self.unit!r}")
        object.__setattr__(self, "list_price", parse_money(self.list_price))
        if not isinstance(self.taxable, bool):
            raise CatalogError(f"taxable must be a bool: {self.taxable!r}")
        tiers = tuple(sorted(self.tiers, key=lambda tier: tier.min_qty))
        for lower, higher in pairwise(tiers):
            if lower.min_qty == higher.min_qty:
                raise CatalogError(f"two tiers start at {lower.min_qty}")
            if higher.percent <= lower.percent:
                raise CatalogError("tier percentages must rise with quantity")
        object.__setattr__(self, "tiers", tiers)


class Catalog:
    """All products, keyed by SKU."""

    def __init__(self) -> None:
        self._products: dict[str, Product] = {}

    def add(self, product: Product) -> None:
        """Add a product. A SKU can only be added once."""
        if product.sku in self._products:
            raise DuplicateSku(f"SKU already in the catalog: {product.sku}")
        self._products[product.sku] = product

    def get(self, sku: str) -> Product:
        """Return the product for `sku` (any case), or raise `UnknownSku`."""
        key = normalize_sku(sku)
        try:
            return self._products[key]
        except KeyError:
            raise UnknownSku(f"no such SKU: {key}") from None

    def set_price(self, sku: str, price: object) -> Product:
        """Change a product's list price. Existing orders keep their own copy."""
        product = replace(self.get(sku), list_price=parse_money(price))
        self._products[product.sku] = product
        return product

    def products(self) -> list[Product]:
        """All products, sorted by SKU."""
        return [self._products[sku] for sku in sorted(self._products)]

    def __contains__(self, sku: object) -> bool:
        if not isinstance(sku, str):
            return False
        try:
            return normalize_sku(sku) in self._products
        except InvalidSku:
            return False

    def __len__(self) -> int:
        return len(self._products)
