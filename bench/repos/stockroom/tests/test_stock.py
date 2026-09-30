import dataclasses

import pytest
from stockroom.errors import InsufficientStock, InvalidQuantity, UnknownSku
from stockroom.ledger import MovementKind


def test_r8_movements_are_numbered_in_order_and_cannot_be_edited(store):
    """R8: sequence numbers count from 1; entries are frozen copies."""
    store.ship("PEN-1", 5)
    entries = store.ledger.entries()
    assert [m.seq for m in entries] == list(range(1, len(entries) + 1))
    assert entries[-1].kind is MovementKind.SHIP
    with pytest.raises(dataclasses.FrozenInstanceError):
        entries[0].quantity = 999  # type: ignore[misc]
    assert isinstance(entries, tuple) and len(store.ledger) == 4


def test_r8_quantities_must_be_positive_whole_numbers(store):
    """R8: receive and ship take positive ints; bools and floats are refused."""
    for bad in (0, -3, 1.5, True):
        with pytest.raises(InvalidQuantity):
            store.receive("PEN-1", bad)
        with pytest.raises(InvalidQuantity):
            store.ship("PEN-1", bad)
    assert store.stock("PEN-1").on_hand == 100


def test_r8_adjustments_are_signed_and_need_a_reason(store):
    """R8: an adjustment is non-zero and explained."""
    store.adjust("PEN-1", -4, "stocktake")
    store.adjust("PEN-1", 2, "found in aisle")
    assert store.stock("PEN-1").on_hand == 98
    for delta, reason in ((0, "nothing"), (-1, "   ")):
        with pytest.raises(InvalidQuantity):
            store.adjust("PEN-1", delta, reason)
    assert store.ledger.entries("PEN-1")[-1].reason == "found in aisle"


def test_r9_levels_are_derived_from_the_ledger(store):
    """R9: on hand, reserved and available follow from the movements."""
    store.ship("PEN-1", 30)
    store.adjust("PEN-1", -5, "damaged")
    store.reserve_order(store.create_order([("PEN-1", 20)]).id)
    level = store.stock("pen-1")
    assert (level.on_hand, level.reserved, level.available) == (65, 20, 45)
    assert store.ledger.total("PEN-1", MovementKind.RECEIVE) == 100
    assert store.ledger.total("PEN-1", MovementKind.SHIP) == 30


def test_r9_reserving_does_not_change_units_on_hand(store):
    """R9: reserve and release only move the reserved figure."""
    store.inventory.reserve("PEN-1", 10)
    store.inventory.release("PEN-1", 4)
    level = store.stock("PEN-1")
    assert (level.on_hand, level.reserved, level.available) == (100, 6, 94)
    with pytest.raises(InvalidQuantity):
        store.inventory.release("PEN-1", 7)


def test_r10_over_shipping_and_over_reserving_are_refused_and_not_recorded(store):
    """R10: only available units can be shipped or reserved."""
    store.inventory.reserve("PEN-1", 90)
    before = len(store.ledger)
    with pytest.raises(InsufficientStock):
        store.ship("PEN-1", 11)
    with pytest.raises(InsufficientStock):
        store.inventory.reserve("BOOK-1", 21)
    assert len(store.ledger) == before
    store.ship("PEN-1", 10)
    assert store.stock("PEN-1").available == 0


def test_r10_an_adjustment_cannot_undercut_reservations(store):
    """R10: on hand may not fall below reserved."""
    store.inventory.reserve("PEN-1", 20)
    with pytest.raises(InsufficientStock):
        store.adjust("PEN-1", -81, "lost")
    store.adjust("PEN-1", -80, "lost")
    assert store.stock("PEN-1").on_hand == 20
    with pytest.raises(UnknownSku):  # R7: stock needs a catalog SKU
        store.stock("GHOST")
