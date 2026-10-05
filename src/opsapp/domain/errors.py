"""Domain errors. The web layer maps each class to an HTTP response."""


class DomainError(Exception):
    """Base class for rule violations a user can understand and act on."""


class ValidationError(DomainError):
    """Input is malformed or violates a business rule."""


class PricingError(ValidationError):
    """A quote cannot be calculated from the approved catalog and rules."""


class InvalidTransition(DomainError):
    """A workflow was asked to move to a state that is not allowed from its current state."""


class PermissionDenied(DomainError):
    """The acting user's role does not allow this action."""


class NotFound(DomainError):
    """The record does not exist in the acting user's tenant.

    Records in other tenants raise this too, so their existence is not revealed.
    """


class Conflict(DomainError):
    """The record changed since the user loaded it, or the action was already taken."""


class StaleApproval(Conflict):
    """An approval refers to a quote or action version that is no longer current."""
