"""Stock levels, derived from the ledger.

There is no stored counter. For one SKU:

    on_hand   = received + adjusted - shipped
    reserved  = reserved - released
    available = on_hand - reserved

Every operation checks the levels first and only then appends to the ledger, so
a refused operation leaves no trace.
"""

from dataclasses import dataclass

from .catalog import Catalog, check_quantity
from .errors import InsufficientStock, InvalidQuantity
from .ledger import Ledger, MovementKind


@dataclass(frozen=True)
class StockLevel:
    """The three stock figures for one SKU."""

    sku: str
    on_hand: int
    reserved: int
    available: int


class Inventory:
    """Stock operations over a ledger, for the products of a catalog."""

    def __init__(self, catalog: Catalog, ledger: Ledger | None = None) -> None:
        self.catalog = catalog
        self.ledger = ledger if ledger is not None else Ledger()

    def _sku(self, sku: str) -> str:
        """Normalise `sku` and make sure the catalog knows it."""
        product = self.catalog.get(sku)
        return product.sku

    def on_hand(self, sku: str) -> int:
        """Units physically in the stockroom."""
        key = self._sku(sku)
        ledger = self.ledger
        return (
            ledger.total(key, MovementKind.RECEIVE)
            + ledger.total(key, MovementKind.ADJUST)
            - ledger.total(key, MovementKind.SHIP)
        )

    def reserved(self, sku: str) -> int:
        """Units promised to open orders."""
        key = self._sku(sku)
        return self.ledger.total(key, MovementKind.RESERVE) - self.ledger.total(
            key, MovementKind.RELEASE
        )

    def available(self, sku: str) -> int:
        """Units that can still be promised or shipped directly."""
        return self.on_hand(sku) - self.reserved(sku)

    def level(self, sku: str) -> StockLevel:
        """All three figures for one SKU."""
        key = self._sku(sku)
        on_hand, reserved = self.on_hand(key), self.reserved(key)
        return StockLevel(key, on_hand, reserved, on_hand - reserved)

    def snapshot(self) -> list[StockLevel]:
        """The levels of every catalog SKU, sorted by SKU."""
        return [self.level(product.sku) for product in self.catalog.products()]

    def receive(
        self, sku: str, quantity: int, reason: str = "received", ref: str = ""
    ) -> None:
        """Add units to the stockroom."""
        self.ledger.append(self._sku(sku), MovementKind.RECEIVE, quantity, reason, ref)

    def ship(
        self, sku: str, quantity: int, reason: str = "shipped", ref: str = ""
    ) -> None:
        """Ship units directly, without an order. Only available units can go."""
        key = self._sku(sku)
        check_quantity(quantity)
        if quantity > self.available(key):
            raise InsufficientStock(
                f"cannot ship {quantity} of {key}: {self.available(key)} available"
            )
        self.ledger.append(key, MovementKind.SHIP, quantity, reason, ref)

    def adjust(self, sku: str, delta: int, reason: str) -> None:
        """Correct the count by a signed amount, e.g. after a stocktake.

        A correction may not leave fewer units on hand than are reserved."""
        key = self._sku(sku)
        if (
            isinstance(delta, int)
            and delta < 0
            and self.on_hand(key) + delta < self.reserved(key)
        ):
            raise InsufficientStock(
                f"cannot adjust {key} by {delta}: {self.reserved(key)} reserved, "
                f"{self.on_hand(key)} on hand"
            )
        self.ledger.append(key, MovementKind.ADJUST, delta, reason)

    def reserve(self, sku: str, quantity: int, ref: str = "") -> None:
        """Promise available units to an order."""
        key = self._sku(sku)
        check_quantity(quantity)
        if quantity > self.available(key):
            raise InsufficientStock(
                f"cannot reserve {quantity} of {key}: {self.available(key)} available"
            )
        self.ledger.append(key, MovementKind.RESERVE, quantity, "reserved", ref)

    def release(self, sku: str, quantity: int, ref: str = "") -> None:
        """Give reserved units back to the available pool."""
        key = self._sku(sku)
        check_quantity(quantity)
        if quantity > self.reserved(key):
            raise InvalidQuantity(
                f"cannot release {quantity} of {key}: only {self.reserved(key)} reserved"
            )
        self.ledger.append(key, MovementKind.RELEASE, quantity, "released", ref)

    def ship_reserved(self, sku: str, quantity: int, ref: str = "") -> None:
        """Ship reserved units: one SHIP movement, then the matching RELEASE."""
        key = self._sku(sku)
        check_quantity(quantity)
        if quantity > self.reserved(key):
            raise InvalidQuantity(
                f"cannot ship {quantity} of {key}: only {self.reserved(key)} reserved"
            )
        self.ledger.append(key, MovementKind.SHIP, quantity, "fulfilled", ref)
        self.ledger.append(key, MovementKind.RELEASE, quantity, "fulfilled", ref)
