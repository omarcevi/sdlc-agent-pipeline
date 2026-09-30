"""`Store`: one object that wires catalog, inventory, pricing and orders together."""

from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path

from . import csvio
from .catalog import Catalog, Product
from .errors import UnknownCoupon
from .inventory import Inventory, StockLevel
from .ledger import Ledger
from .money import parse_percent
from .orders import Order, OrderBook
from .pricing import Coupon, Quote, quote_items

DEFAULT_TAX_PERCENT = Decimal("8.25")


class Store:
    """The public entry point.

    `tax_percent` is the sales tax rate in percent units, applied to taxable
    products. Everything else is reached through the methods below or through
    the `catalog`, `inventory` and `orders` attributes."""

    def __init__(self, tax_percent: object = DEFAULT_TAX_PERCENT) -> None:
        self.tax_percent = parse_percent(tax_percent)
        self.catalog = Catalog()
        self.ledger = Ledger()
        self.inventory = Inventory(self.catalog, self.ledger)
        self.orders = OrderBook(self.catalog, self.inventory)
        self._coupons: dict[str, Coupon] = {}

    # Catalog
    def add_product(self, product: Product) -> None:
        self.catalog.add(product)

    def import_products(self, path: str | Path) -> csvio.ImportResult:
        """Import a products CSV. Valid rows are added; bad rows are reported."""
        result = csvio.import_products(path, existing=self.catalog)
        for product in result.products:
            self.catalog.add(product)
        return result

    def export_stock(self, path: str | Path) -> None:
        csvio.export_stock(self, path)

    # Stock
    def receive(self, sku: str, quantity: int, reason: str = "received") -> None:
        self.inventory.receive(sku, quantity, reason)

    def ship(self, sku: str, quantity: int, reason: str = "shipped") -> None:
        self.inventory.ship(sku, quantity, reason)

    def adjust(self, sku: str, delta: int, reason: str) -> None:
        self.inventory.adjust(sku, delta, reason)

    def stock(self, sku: str) -> StockLevel:
        return self.inventory.level(sku)

    # Coupons and quotes
    def add_coupon(self, code: str, percent: object) -> Coupon:
        coupon = Coupon(code, parse_percent(percent))
        self._coupons[coupon.code] = coupon
        return coupon

    def _coupon(self, code: str | None) -> Coupon | None:
        if code is None:
            return None
        try:
            return self._coupons[code.strip().upper()]
        except KeyError:
            raise UnknownCoupon(f"no such coupon: {code}") from None

    def quote(
        self, items: Iterable[tuple[str, int]], coupon_code: str | None = None
    ) -> Quote:
        """Price (sku, quantity) pairs without creating an order."""
        pairs = [(self.catalog.get(sku), quantity) for sku, quantity in items]
        return quote_items(pairs, self._coupon(coupon_code), self.tax_percent)

    # Orders
    def create_order(
        self, items: Iterable[tuple[str, int]], coupon_code: str | None = None
    ) -> Order:
        return self.orders.create(items, self._coupon(coupon_code), self.tax_percent)

    def reserve_order(self, order_id: str) -> Order:
        return self.orders.reserve(order_id)

    def fulfil_order(
        self, order_id: str, quantities: Mapping[str, int] | None = None
    ) -> Order:
        return self.orders.fulfil(order_id, quantities)

    def cancel_order(self, order_id: str) -> Order:
        return self.orders.cancel(order_id)

    def billable(self, order_id: str) -> Quote:
        return self.orders.billable_quote(order_id)
