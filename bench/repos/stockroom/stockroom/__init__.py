"""stockroom: a small inventory and pricing module.

Start with `Store`. The rules it follows are listed in the README.
"""

from . import csvio
from .catalog import Product, Tier, Unit
from .errors import StockroomError
from .orders import OrderStatus
from .store import Store

__all__ = [
    "OrderStatus",
    "Product",
    "StockroomError",
    "Store",
    "Tier",
    "Unit",
    "csvio",
]
