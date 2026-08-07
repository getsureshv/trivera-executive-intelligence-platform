"""Secret handling primitives (ADR-015).

Phase 1A ships the *types* and the *port*, not a cloud adapter. That ordering
is deliberate: the property worth protecting from the first commit is that a
secret value cannot accidentally be logged, serialised, or persisted, and that
property is enforced by the type — not by an adapter.

Design goal, restated from ADR-015: **a full compromise of the metadata
database yields zero customer credentials.** Records hold a ``SecretRef``, a
pointer plus a version; values live in the secret manager and are fetched at
point of use for the minimum duration.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol, final, runtime_checkable


@final
class SecretValue:
    """A secret that cannot be printed, logged, or serialised.

    The overwhelming majority of real credential leaks are not exfiltration —
    they are a ``logger.debug(config)`` or an exception whose message contains a
    connection string. Making that a *type* error is far more reliable than
    making it a review comment.

    ``reveal()`` is the single, greppable way to obtain the underlying value.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the raw value. Call as late and as briefly as possible."""
        return self._value

    def __repr__(self) -> str:
        return "SecretValue(***)"

    def __str__(self) -> str:
        return "***"

    def __format__(self, _spec: str) -> str:
        return "***"

    def __eq__(self, other: object) -> bool:
        # Constant-time comparison is not required here (these are not
        # verified against attacker-supplied input), but structural equality
        # must not be usable as an oracle in logs.
        return isinstance(other, SecretValue) and self._value == other._value

    def __hash__(self) -> int:
        raise TypeError("SecretValue is unhashable: it must not become a dict key or cache key")

    def __getstate__(self) -> Any:
        raise TypeError("SecretValue cannot be pickled or serialised (ADR-015)")

    def __json__(self) -> Any:
        raise TypeError("SecretValue cannot be JSON-serialised (ADR-015)")


@final
@dataclass(frozen=True, slots=True)
class SecretRef:
    """A pointer to a secret. This — never a value — is what a row stores."""

    tenant_id: uuid.UUID
    logical_name: str
    version: str

    @property
    def path(self) -> str:
        """Tenant-namespaced path, so IAM policies can be prefix-scoped."""
        return f"tenants/{self.tenant_id}/{self.logical_name}"


@runtime_checkable
class SecretStore(Protocol):
    """The port. Business logic depends on this, never on a vendor SDK.

    Phase 1A defines the interface only; connectors (Phase 2) are the first
    real consumer. ``get`` takes a ``purpose`` because every retrieval is
    audited, and an unexplained retrieval is a finding.
    """

    async def put(
        self, tenant_id: uuid.UUID, logical_name: str, value: SecretValue
    ) -> SecretRef: ...

    async def get(self, ref: SecretRef, *, purpose: str) -> SecretValue: ...

    async def rotate(self, ref: SecretRef, value: SecretValue) -> SecretRef: ...

    async def delete(self, ref: SecretRef) -> None: ...
