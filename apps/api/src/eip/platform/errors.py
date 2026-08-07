"""Error taxonomy.

Every failure surfaces as RFC 9457 ``application/problem+json`` with a stable
machine-readable ``code`` and a ``correlation_id`` matching the request's trace.

One security rule dominates the design (ADR-010 §4):

    **Unauthorized and non-existent must be indistinguishable.**

Metric names, dashboard names, and data-source names describe a customer's
business strategy. Returning 403 for "exists but you may not see it" and 404
for "does not exist" leaks that inventory to anyone who can enumerate ids.
``NotFoundError`` is therefore the response for both, and ``ForbiddenError`` is
reserved for *capability* failures where the object identity is not the secret.
"""

from __future__ import annotations

from typing import Any


class EipError(Exception):
    """Base class for every deliberate application error."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    title: str = "Internal server error"

    def __init__(self, detail: str | None = None, **context: Any) -> None:
        self.detail = detail or self.title
        self.context = context
        super().__init__(self.detail)

    def to_problem(self, *, correlation_id: str, instance: str) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": f"https://docs.trivera.invalid/problem/{self.code.lower().replace('_', '-')}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "code": self.code,
            "instance": instance,
            "correlation_id": correlation_id,
        }
        if self.context:
            problem["context"] = self.context
        return problem


# --- authentication / authorization -----------------------------------------


class UnauthenticatedError(EipError):
    """No credential, or a credential that failed verification."""

    status_code = 401
    code = "UNAUTHENTICATED"
    title = "Authentication required"


class ForbiddenError(EipError):
    """The principal is known but lacks the required capability."""

    status_code = 403
    code = "FORBIDDEN"
    title = "Insufficient permissions"


class NotFoundError(EipError):
    """The resource does not exist, **or** the principal may not see it.

    Deliberately ambiguous. Do not add a variant that distinguishes the two.
    """

    status_code = 404
    code = "NOT_FOUND"
    title = "Resource not found or not permitted"


class TenantContextRequiredError(EipError):
    """A tenant-scoped operation was attempted without a resolved tenant.

    Reaching this is a programming error, not a user error: the type system is
    supposed to make it impossible. It fails closed and loudly.
    """

    status_code = 403
    code = "TENANT_CONTEXT_REQUIRED"
    title = "Tenant context is required"


class CrossTenantAccessError(EipError):
    """A request referenced a resource belonging to a different tenant.

    Surfaced to the client as a 404 by the handler (see ``NotFoundError``), but
    kept as a distinct type so it can be audited and alerted on separately —
    a burst of these is a probe, not a typo.
    """

    status_code = 404
    code = "NOT_FOUND"
    title = "Resource not found or not permitted"


# --- request validity --------------------------------------------------------


class ValidationError(EipError):
    status_code = 422
    code = "VALIDATION_FAILED"
    title = "Request validation failed"


class ConflictError(EipError):
    status_code = 409
    code = "CONFLICT"
    title = "Conflicting state"


class PreconditionFailedError(EipError):
    """Optimistic-concurrency failure on ``If-Match`` (ADR-013 §2)."""

    status_code = 412
    code = "PRECONDITION_FAILED"
    title = "Precondition failed"


# --- platform / configuration ------------------------------------------------


class ConfigurationError(EipError):
    """A startup invariant is violated. The process must not serve traffic."""

    status_code = 500
    code = "CONFIGURATION_ERROR"
    title = "Platform misconfigured"


class DependencyUnavailableError(EipError):
    status_code = 503
    code = "DEPENDENCY_UNAVAILABLE"
    title = "A required dependency is unavailable"


class NotImplementedModeError(EipError):
    """A declared-but-unbuilt mode was requested (e.g. an isolation mode).

    Fails loudly. Silently falling back to a different isolation mode would be
    the worst possible behaviour.
    """

    status_code = 501
    code = "MODE_NOT_IMPLEMENTED"
    title = "Requested mode is declared but not implemented"
