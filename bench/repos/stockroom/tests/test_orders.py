from decimal import Decimal

import pytest
from stockroom import OrderStatus
from stockroom.errors import (
    InsufficientStock,
    InvalidOrderState,
    InvalidQuantity,
    OverFulfilment,
    UnknownOrder,
    UnknownSku,
)

D = Decimal


def levels(store, sku):
    level = store.stock(sku)
    return level.on_hand, level.reserved, level.available


def reserved_order(store, items=(("PEN-1", 12), ("BOOK-1", 3))):
    order = store.create_order(list(items))
    store.reserve_order(order.id)
    return order


def test_r11_creating_an_order_prices_it_and_leaves_stock_alone(store):
    """R11: a new order is priced, NEW, and has no stock effect."""
    before = len(store.ledger)
    order = store.create_order([("PEN-1", 12), ("BOOK-1", 3)])
    assert (order.id, order.status) == ("ORD-0001", OrderStatus.NEW)
    assert order.quote.total == D("33.48")
    assert len(store.ledger) == before and levels(store, "PEN-1") == (100, 0, 100)


def test_r11_repeated_skus_merge_before_pricing(store):
    """R11: two lines of 6 pens are one line of 12, which reaches the 10-unit tier."""
    order = store.create_order([("pen-1", 6), ("BOOK-1", 1), ("PEN-1", 6)])
    assert [(line.product.sku, line.quantity) for line in order.lines] == [
        ("PEN-1", 12),
        ("BOOK-1", 1),
    ]
    assert order.quote.lines[0].tier_percent == D("5")


def test_r11_an_invalid_item_creates_no_order(store):
    """R11: nothing is recorded, and the next order still gets the next number."""
    for items in ([], [("PEN-1", 0)], [("PEN-1", 1), ("GHOST", 1)]):
        with pytest.raises((InvalidQuantity, UnknownSku)):
            store.create_order(items)
    assert store.orders.orders() == []
    assert store.create_order([("PEN-1", 1)]).id == "ORD-0001"
    assert store.create_order([("PEN-1", 1)]).id == "ORD-0002"


def test_r11_orders_keep_the_product_as_it_was(store):
    """R11: a later price change does not reprice an order (R15: billed as quoted)."""
    order = reserved_order(store, [("PEN-1", 12)])
    store.catalog.set_price("PEN-1", "2.00")
    store.fulfil_order(order.id)
    assert store.billable(order.id) == order.quote  # R15: billed as quoted
    assert store.billable(order.id).total == D("18.51")
    assert store.create_order([("PEN-1", 12)]).quote.total > D("18.51")


def test_r12_reserving_is_all_or_nothing(store):
    """R12: one short line means no line is reserved."""
    order = store.create_order([("PEN-1", 5), ("BOOK-1", 21)])
    before = len(store.ledger)
    with pytest.raises(InsufficientStock):
        store.reserve_order(order.id)
    assert len(store.ledger) == before and order.status is OrderStatus.NEW


def test_r12_only_a_new_order_can_be_reserved_and_holds_its_units(store):
    """R12: no double reservation, and other orders cannot take reserved units."""
    order = reserved_order(store, [("BOOK-1", 15)])
    with pytest.raises(InvalidOrderState):
        store.reserve_order(order.id)
    assert levels(store, "BOOK-1") == (20, 15, 5)
    with pytest.raises(InsufficientStock):
        store.reserve_order(store.create_order([("BOOK-1", 6)]).id)


def test_r13_fulfilling_ships_everything_outstanding(store):
    """R13: the default fulfils the whole order."""
    order = reserved_order(store)
    store.fulfil_order(order.id)
    assert order.status is OrderStatus.FULFILLED
    assert levels(store, "PEN-1") == (88, 0, 88)
    assert levels(store, "BOOK-1") == (17, 0, 17)
    assert (order.shipped, order.reserved) == ({"PEN-1": 12, "BOOK-1": 3}, {})


def test_r13_partial_fulfilment_leaves_the_rest_reserved(store):
    """R13: a partial shipment leaves the order PARTIALLY_FULFILLED."""
    order = reserved_order(store)
    store.fulfil_order(order.id, {"pen-1": 5})
    assert order.status is OrderStatus.PARTIALLY_FULFILLED
    assert order.reserved == {"PEN-1": 7, "BOOK-1": 3}
    assert levels(store, "PEN-1") == (95, 7, 88)
    store.fulfil_order(order.id, {"PEN-1": 7})
    assert order.status is OrderStatus.PARTIALLY_FULFILLED
    store.fulfil_order(order.id)
    assert order.status is OrderStatus.FULFILLED
    assert order.shipped == {"PEN-1": 12, "BOOK-1": 3}


def test_r13_over_fulfilment_ships_nothing(store):
    """R13: one bad quantity cancels the whole fulfillment request."""
    order = reserved_order(store)
    before = len(store.ledger)
    for request in ({"BOOK-1": 3, "PEN-1": 13}, {"WIDGET": 1}):
        with pytest.raises(OverFulfilment):
            store.fulfil_order(order.id, request)
    assert len(store.ledger) == before
    assert (order.status, order.shipped) == (OrderStatus.RESERVED, {})


def test_r13_fulfilment_needs_a_reserved_order_and_real_quantities(store):
    """R13: a new or finished order cannot be fulfilled; empty requests are refused."""
    order = store.create_order([("PEN-1", 2)])
    with pytest.raises(InvalidOrderState):
        store.fulfil_order(order.id)
    store.reserve_order(order.id)
    for request in ({}, {"PEN-1": 0}):
        with pytest.raises(InvalidQuantity):
            store.fulfil_order(order.id, request)
    store.fulfil_order(order.id)
    with pytest.raises(InvalidOrderState):
        store.fulfil_order(order.id)


def test_r14_cancelling_releases_reserved_units(store):
    """R14: reserved units become available again; a new order has none to release."""
    fresh = store.create_order([("PEN-1", 5)])
    before = len(store.ledger)
    store.cancel_order(fresh.id)
    assert fresh.status is OrderStatus.CANCELLED and len(store.ledger) == before
    order = reserved_order(store)
    store.cancel_order(order.id)
    assert levels(store, "PEN-1") == (100, 0, 100)
    assert levels(store, "BOOK-1") == (20, 0, 20)
    assert order.reserved == {}


def test_r14_cancelling_a_partly_fulfilled_order_keeps_shipped_units_shipped(store):
    """R14: only the outstanding reservation is released."""
    order = reserved_order(store)
    store.fulfil_order(order.id, {"PEN-1": 9})
    store.cancel_order(order.id)
    assert order.status is OrderStatus.CANCELLED
    assert levels(store, "PEN-1") == (91, 0, 91)
    assert levels(store, "BOOK-1") == (20, 0, 20)
    assert order.shipped == {"PEN-1": 9}


def test_r14_finished_orders_cannot_be_cancelled(store):
    """R14: fulfilled and cancelled orders stay as they are."""
    done = reserved_order(store, [("PEN-1", 1)])
    store.fulfil_order(done.id)
    gone = store.create_order([("PEN-1", 1)])
    store.cancel_order(gone.id)
    for order in (done, gone):
        with pytest.raises(InvalidOrderState):
            store.cancel_order(order.id)


def test_r15_a_cancelled_order_is_billed_for_shipped_units_with_their_own_tier(store):
    """R15: 9 of 12 pens shipped is below the 10-unit tier, so no tier discount."""
    order = reserved_order(store)
    store.fulfil_order(order.id, {"PEN-1": 9})
    store.cancel_order(order.id)
    bill = store.billable(order.id)
    assert [(line.sku, line.quantity) for line in bill.lines] == [("PEN-1", 9)]
    assert bill.lines[0].discount == D("0") and bill.total == D("14.61")
    assert bill.total != order.quote.total


def test_r15_nothing_shipped_owes_nothing_and_open_orders_have_no_bill(store):
    """R15: an all-zero bill after cancelling; no bill while the order is open."""
    order = store.create_order([("PEN-1", 12)])
    with pytest.raises(InvalidOrderState):
        store.billable(order.id)
    store.reserve_order(order.id)
    with pytest.raises(InvalidOrderState):
        store.billable(order.id)
    store.cancel_order(order.id)
    assert store.billable(order.id).lines == ()
    assert str(store.billable(order.id).total) == "0.00"


def test_a_run_of_operations_is_repeatable(make_store):
    """The same operations give the same ledger and totals (no clock, no randomness)."""

    def run(s):
        order = reserved_order(s, [("WIDGET", 7), ("PEN-1", 60)])
        s.fulfil_order(order.id, {"WIDGET": 3})
        s.cancel_order(order.id)
        return s.ledger.entries(), s.billable(order.id)

    assert run(make_store()) == run(make_store())


def test_unknown_order_ids_raise_unknown_order(store):
    """Order lookup: every operation on a missing ID raises UnknownOrder."""
    for operation in (store.reserve_order, store.fulfil_order, store.billable):
        with pytest.raises(UnknownOrder):
            operation("ORD-9999")
