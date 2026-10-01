"""Orders: create, reserve, fulfil and cancel.

An order moves through these statuses:

    NEW -> RESERVED -> PARTIALLY_FULFILLED -> FULFILLED
    NEW, RESERVED or PARTIALLY_FULFILLED -> CANCELLED

Creating an order prices it but does not touch stock. Reserving, fulfilling and
cancelling write to the inventory's ledger, always with the order ID as `ref`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .catalog import (
    Catalog,
    Product,
    check_quantity,
    merge_items,
    normalize_sku,
)
from .errors import (
    InsufficientStock,
    InvalidOrderState,
    InvalidQuantity,
    OverFulfilment,
    UnknownOrder,
)
from .inventory import Inventory
from .pricing import Coupon, Quote, quote_items


class OrderStatus(Enum):
    """Where an order is in its life."""

    NEW = "new"
    RESERVED = "reserved"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class OrderLine:
    """One SKU on an order. `product` is the catalog entry as it was at creation."""

    product: Product
    quantity: int


@dataclass
class Order:
    """An order and its progress.

    `reserved` holds the units still reserved for the order per SKU, and
    `shipped` the units already sent. Both only ever list SKUs with units."""

    id: str
    lines: tuple[OrderLine, ...]
    coupon: Coupon | None
    tax_percent: Decimal
    quote: Quote
    status: OrderStatus = OrderStatus.NEW
    reserved: dict[str, int] = field(default_factory=dict)
    shipped: dict[str, int] = field(default_factory=dict)


class OrderBook:
    """All orders, plus the operations that move them along."""

    def __init__(self, catalog: Catalog, inventory: Inventory) -> None:
        self.catalog = catalog
        self.inventory = inventory
        self._orders: dict[str, Order] = {}

    def get(self, order_id: str) -> Order:
        """Return an order, or raise `UnknownOrder`."""
        try:
            return self._orders[order_id]
        except KeyError:
            raise UnknownOrder(f"no such order: {order_id}") from None

    def orders(self) -> list[Order]:
        """All orders in creation order."""
        return list(self._orders.values())

    def create(
        self,
        items: Iterable[tuple[str, int]],
        coupon: Coupon | None,
        tax_percent: Decimal,
    ) -> Order:
        """Price an order from (sku, quantity) pairs and record it as NEW.

        Repeated SKUs are merged into one line, in order of first appearance,
        before pricing. Nothing is recorded if any item is invalid."""
        merged = merge_items(items)
        if not merged:
            raise InvalidQuantity("an order needs at least one line")
        lines = tuple(
            OrderLine(self.catalog.get(sku), quantity)
            for sku, quantity in merged.items()
        )
        quote = quote_items(
            [(line.product, line.quantity) for line in lines], coupon, tax_percent
        )
        order = Order(
            id=f"ORD-{len(self._orders) + 1:04d}",
            lines=lines,
            coupon=coupon,
            tax_percent=tax_percent,
            quote=quote,
        )
        self._orders[order.id] = order
        return order

    def reserve(self, order_id: str) -> Order:
        """Reserve every line in full, or nothing at all."""
        order = self.get(order_id)
        if order.status is not OrderStatus.NEW:
            raise InvalidOrderState(f"{order.id} is {order.status.value}, not new")
        for line in order.lines:
            available = self.inventory.available(line.product.sku)
            if line.quantity > available:
                raise InsufficientStock(
                    f"{order.id}: {line.quantity} of {line.product.sku} wanted, "
                    f"{available} available"
                )
        for line in order.lines:
            self.inventory.reserve(line.product.sku, line.quantity, order.id)
            order.reserved[line.product.sku] = line.quantity
        order.status = OrderStatus.RESERVED
        return order

    def fulfil(
        self, order_id: str, quantities: Mapping[str, int] | None = None
    ) -> Order:
        """Ship reserved units. With no `quantities`, ship everything outstanding.

        Asking for more of a SKU than is still reserved for the order, or for a
        SKU that has nothing outstanding, raises `OverFulfilment` and ships
        nothing."""
        order = self.get(order_id)
        if order.status not in (OrderStatus.RESERVED, OrderStatus.PARTIALLY_FULFILLED):
            raise InvalidOrderState(
                f"{order.id} is {order.status.value}, cannot fulfil"
            )
        if quantities is None:
            to_ship = dict(order.reserved)
        else:
            to_ship = {}
            for sku, quantity in quantities.items():
                key = normalize_sku(sku)
                to_ship[key] = to_ship.get(key, 0) + check_quantity(quantity)
            if not to_ship:
                raise InvalidQuantity("nothing to fulfil")
        for sku, quantity in to_ship.items():
            outstanding = order.reserved.get(sku, 0)
            if quantity > outstanding:
                raise OverFulfilment(
                    f"{order.id}: {quantity} of {sku} requested, {outstanding} outstanding"
                )
        for line in order.lines:
            sku = line.product.sku
            quantity = to_ship.get(sku, 0)
            if quantity == 0:
                continue
            self.inventory.ship_reserved(sku, quantity, order.id)
            order.shipped[sku] = order.shipped.get(sku, 0) + quantity
            remaining = order.reserved[sku] - quantity
            if remaining:
                order.reserved[sku] = remaining
            else:
                del order.reserved[sku]
        if order.reserved:
            order.status = OrderStatus.PARTIALLY_FULFILLED
        else:
            order.status = OrderStatus.FULFILLED
        return order

    def cancel(self, order_id: str) -> Order:
        """Cancel an order that is not finished.

        Units still reserved go back to the available pool. Units already
        shipped stay shipped."""
        order = self.get(order_id)
        if order.status in (OrderStatus.FULFILLED, OrderStatus.CANCELLED):
            raise InvalidOrderState(
                f"{order.id} is {order.status.value}, cannot cancel"
            )
        for line in order.lines:
            sku = line.product.sku
            if sku in order.reserved:
                self.inventory.release(sku, order.reserved[sku], order.id)
        order.reserved = {}
        order.status = OrderStatus.CANCELLED
        return order

    def billable_quote(self, order_id: str) -> Quote:
        """What the customer owes for a finished order.

        A fulfilled order is billed as quoted. A cancelled order is billed for
        the units that shipped, priced again as if that were the order (so
        quantity tiers follow the shipped quantity). An order that is still
        open has no bill yet."""
        order = self.get(order_id)
        if order.status is OrderStatus.FULFILLED:
            return order.quote
        if order.status is OrderStatus.CANCELLED:
            items = [
                (line.product, order.shipped[line.product.sku])
                for line in order.lines
                if line.product.sku in order.shipped
            ]
            return quote_items(items, None, order.tax_percent)
        raise InvalidOrderState(f"{order.id} is {order.status.value}, not finished")
