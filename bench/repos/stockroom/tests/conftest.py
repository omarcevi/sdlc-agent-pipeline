from decimal import Decimal

import pytest
from stockroom import Product, Store, Tier, Unit


def build_store() -> Store:
    """Tax 8.25 %. PEN-1: 1.50, taxable, tiers 10 -> 5 % and 50 -> 10 %, 100 on hand.
    BOOK-1: 4.99, not taxable, 20 on hand. WIDGET: 19.99, tiers 5 -> 10 % and
    20 -> 30 %, 30 on hand."""
    store = Store(tax_percent="8.25")
    tiers = {"PEN-1": ((10, "5"), (50, "10")), "WIDGET": ((5, "10"), (20, "30"))}
    for sku, name, unit, price, taxable in (
        ("PEN-1", "Pen", Unit.EACH, "1.50", True),
        ("BOOK-1", "Notebook", Unit.EACH, "4.99", False),
        ("WIDGET", "Widget", Unit.BOX, "19.99", True),
    ):
        steps = tuple(Tier(q, Decimal(p)) for q, p in tiers.get(sku, ()))
        store.add_product(Product(sku, name, unit, Decimal(price), taxable, steps))
    store.receive("PEN-1", 100)
    store.receive("BOOK-1", 20)
    store.receive("WIDGET", 30)
    return store


@pytest.fixture
def store() -> Store:
    return build_store()


@pytest.fixture
def make_store():
    """The factory behind `store`, for tests that need two identical stores."""
    return build_store
