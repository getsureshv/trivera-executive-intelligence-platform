"""Model registry — importing this module registers every ORM mapper.

SQLAlchemy resolves a relationship or foreign key by name against
``Base.metadata``, and it does so lazily, at first mapper configuration. A
process that imports only ``eip.governance.models`` therefore fails with
``NoReferencedTableError`` the first time it touches ``AuditEvent``, because
``audit_event.tenant_id`` references a ``tenant`` table whose model has not been
imported.

That is not hypothetical: it is exactly what happened when the outbox relay
began writing audit events. The worker imported ``eip.governance`` but never
``eip.identity``, so the relay would have crashed on its first dispatch in
production.

Fixing it inside ``eip.governance.models`` is not an option — a context may not
import another context's internals (ADR-001), and doing so would create a cycle.
This module lives at the package root rather than inside a context, so importing
it composes the registry without breaching any boundary. It is the same role
``eip.api`` plays for HTTP: composition, not domain logic.

**Import this from every process entrypoint and from test configuration.**
Anything that touches an ORM model needs the whole registry, not part of it.
"""

from __future__ import annotations

from eip.connectivity.models import ConnectionTest, DataSource, DataSourceAcl
from eip.governance.models import AuditChainHead, AuditEvent, OutboxMessage
from eip.identity.models import AppUser, Membership, Role, RoleCapability, Tenant

__all__ = [
    "AppUser",
    "AuditChainHead",
    "AuditEvent",
    "ConnectionTest",
    "DataSource",
    "DataSourceAcl",
    "Membership",
    "OutboxMessage",
    "Role",
    "RoleCapability",
    "Tenant",
]
