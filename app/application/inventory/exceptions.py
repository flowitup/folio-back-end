"""Application-layer exceptions for the inventory use-cases."""


class InventoryError(Exception):
    """Base exception for all inventory application errors."""


class CompanyAccessDeniedError(InventoryError):
    """The requester has no membership in the target company. HTTP 403."""


class InsufficientPermissionError(InventoryError):
    """The requester lacks `inventory:manage` in the target company. HTTP 403."""


class WarehouseNotFoundError(InventoryError):
    """No such warehouse, or it belongs to another company. HTTP 404."""


class InventoryItemNotFoundError(InventoryError):
    """No such inventory row. HTTP 404."""


class WarehouseInUseError(InventoryError):
    """The warehouse still holds rows; move or remove them first. HTTP 409."""


class InvalidInventoryInputError(InventoryError):
    """The row would violate an invariant, or points at a place outside the company. HTTP 422."""
