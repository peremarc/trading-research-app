class DuplicateResourceError(ValueError):
    """Raised when a unique business key already exists."""


class IntegrityConstraintError(ValueError):
    """Raised when the database rejects a write due to invalid relations or constraints."""
