"""Pricing: list price, quantity tiers, coupons, the discount cap and tax.

All arithmetic on money goes through `money`. Each order line is priced on its
own and rounded at the line; a quote's totals are plain sums of line amounts.
The order of operations for one line is fixed:

    gross    = list_price x quantity                      (exact)
    percent  = min(tier % + coupon %, MAX_COMBINED_DISCOUNT_PERCENT)
    discount = round(gross x percent)
    net      = gross - discount
    tax      = round(net x tax %)   if the product is taxable, else 0
    total    = net + tax
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .catalog import Product, check_quantity
from .errors import CatalogError
from .money import ZERO, apply_percent, parse_percent

MAX_COMBINED_DISCOUNT_PERCENT = Decimal("40")
_COUPON_PATTERN = re.compile(r"[A-Z0-9_-]+")


@dataclass(frozen=True)
class Coupon:
    """A percentage discount that an order can carry. The code is upper case."""

    code: str
    percent: Decimal

    def __post_init__(self) -> None:
        code = self.code.strip().upper() if isinstance(self.code, str) else ""
        if not _COUPON_PATTERN.fullmatch(code):
            raise CatalogError(f"invalid coupon code: {self.code!r}")
        percent = parse_percent(self.percent)
        if percent <= 0:
            raise CatalogError("a coupon discount must be above 0 %")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "percent", percent)


@dataclass(frozen=True)
class LineQuote:
    """The priced result for one SKU on an order."""

    sku: str
    quantity: int
    unit_price: Decimal
    gross: Decimal
    tier_percent: Decimal
    coupon_percent: Decimal
    discount_percent: Decimal
    discount: Decimal
    net: Decimal
    tax: Decimal
    total: Decimal


@dataclass(frozen=True)
class Quote:
    """A priced order: its lines and the sums of their amounts."""

    lines: tuple[LineQuote, ...]
    subtotal: Decimal
    discount: Decimal
    net: Decimal
    tax: Decimal
    total: Decimal


def tier_percent(product: Product, quantity: int) -> Decimal:
    """The quantity discount for `quantity` units of `product`.

    The tier with the largest `min_qty` that does not exceed the quantity wins,
    and it applies to every unit of the line. Below the first tier it is 0."""
    percent = Decimal(0)
    for tier in product.tiers:
        if quantity >= tier.min_qty:
            percent = tier.percent
    return percent


def combined_percent(tier: Decimal, coupon: Decimal) -> Decimal:
    """Tier and coupon percentages added together, limited by the cap."""
    return min(tier + coupon, MAX_COMBINED_DISCOUNT_PERCENT)


def quote_line(
    product: Product,
    quantity: int,
    coupon_percent: Decimal,
    tax_percent: Decimal,
) -> LineQuote:
    """Price `quantity` units of one product."""
    check_quantity(quantity)
    gross = product.list_price * quantity
    tier = tier_percent(product, quantity)
    percent = combined_percent(tier, coupon_percent)
    discount = apply_percent(gross, percent)
    net = gross - discount
    tax = apply_percent(net, tax_percent) if product.taxable else ZERO
    return LineQuote(
        sku=product.sku,
        quantity=quantity,
        unit_price=product.list_price,
        gross=gross,
        tier_percent=tier,
        coupon_percent=coupon_percent,
        discount_percent=percent,
        discount=discount,
        net=net,
        tax=tax,
        total=net + tax,
    )


def quote_items(
    items: Sequence[tuple[Product, int]],
    coupon: Coupon | None,
    tax_percent: Decimal,
) -> Quote:
    """Price a list of (product, quantity) pairs, one line per pair.

    An empty list gives an all-zero quote."""
    coupon_percent = coupon.percent if coupon is not None else Decimal(0)
    lines = tuple(
        quote_line(product, quantity, coupon_percent, tax_percent)
        for product, quantity in items
    )
    return Quote(
        lines=lines,
        subtotal=sum((line.gross for line in lines), ZERO),
        discount=sum((line.discount for line in lines), ZERO),
        net=sum((line.net for line in lines), ZERO),
        tax=sum((line.tax for line in lines), ZERO),
        total=sum((line.total for line in lines), ZERO),
    )
