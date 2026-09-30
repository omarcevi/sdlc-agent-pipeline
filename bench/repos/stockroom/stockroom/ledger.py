"""The stock ledger: an append-only list of movements.

Nothing in the ledger is ever changed or removed. Stock levels are not stored
anywhere; `inventory` derives them by summing movements.
"""

from dataclasses import dataclass
from enum import Enum

from .catalog import check_quantity
from .errors import InvalidQuantity


class MovementKind(Enum):
    """What a movement does.

    RECEIVE, SHIP and ADJUST change the units on hand. RESERVE and RELEASE
    change the units promised to orders and leave the units on hand alone."""

    RECEIVE = "receive"
    SHIP = "ship"
    ADJUST = "adjust"
    RESERVE = "reserve"
    RELEASE = "release"


@dataclass(frozen=True)
class Movement:
    """One ledger entry. `seq` counts from 1 in append order.

    `quantity` is positive, except for ADJUST where it is a signed, non-zero
    change. `ref` is free text, normally an order ID."""

    seq: int
    sku: str
    kind: MovementKind
    quantity: int
    reason: str
    ref: str


class Ledger:
    """Append-only movement history."""

    def __init__(self) -> None:
        self._entries: list[Movement] = []

    def append(
        self,
        sku: str,
        kind: MovementKind,
        quantity: int,
        reason: str = "",
        ref: str = "",
    ) -> Movement:
        """Record a movement and return it. `sku` must already be normalised."""
        if kind is MovementKind.ADJUST:
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or quantity == 0
            ):
                raise InvalidQuantity(
                    f"an adjustment must be a non-zero whole number: {quantity!r}"
                )
            if not reason.strip():
                raise InvalidQuantity("an adjustment needs a reason")
        else:
            check_quantity(quantity)
        movement = Movement(
            len(self._entries) + 1, sku, kind, quantity, reason.strip(), ref
        )
        self._entries.append(movement)
        return movement

    def entries(self, sku: str | None = None) -> tuple[Movement, ...]:
        """All movements in order, optionally only those for one (normalised) SKU."""
        return tuple(m for m in self._entries if sku is None or m.sku == sku)

    def total(self, sku: str, kind: MovementKind) -> int:
        """Sum of the quantities of one kind of movement for one SKU."""
        return sum(m.quantity for m in self._entries if m.sku == sku and m.kind is kind)

    def __len__(self) -> int:
        return len(self._entries)
