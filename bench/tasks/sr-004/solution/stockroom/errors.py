"""Exceptions raised by stockroom.

Every error derives from `StockroomError`, so callers can catch one type. Errors
that come from bad input values also derive from `ValueError`.
"""


class StockroomError(Exception):
    """Base class for every error stockroom raises on purpose."""


class MoneyError(StockroomError, ValueError):
    """A money amount or percentage is malformed or out of range."""


class CatalogError(StockroomError, ValueError):
    """A product, tier, unit or coupon definition is invalid."""


class InvalidSku(CatalogError):
    """A SKU does not match the SKU format."""


class DuplicateSku(CatalogError):
    """A product with this SKU is already in the catalog."""


class UnknownSku(CatalogError):
    """No product with this SKU is in the catalog."""


class DiscontinuedSku(CatalogError):
    """The product is discontinued: it can no longer be quoted or ordered."""


class InvalidQuantity(StockroomError, ValueError):
    """A quantity is not a positive whole number (or a non-zero adjustment)."""


class InsufficientStock(StockroomError):
    """The requested stock movement would use units that are not available."""


class OverFulfilment(StockroomError):
    """An order fulfillment asked for more than is still reserved for the order."""


class UnknownOrder(StockroomError):
    """No order with this ID exists."""


class InvalidOrderState(StockroomError):
    """The order's status does not allow the requested operation."""


class UnknownCoupon(StockroomError):
    """No coupon with this code is registered."""


class CsvFormatError(StockroomError):
    """A CSV file as a whole is unusable (empty, bad header, not UTF-8)."""
