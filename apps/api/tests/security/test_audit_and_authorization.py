"""
================================================================================
 RELEASE-GATING SECURITY TESTS — AUDIT INTEGRITY & AUTHORIZATION
================================================================================

 Covers two guarantees that are cheap to break and expensive to discover:

   * the audit trail is append-only and tamper-evident (ADR-014 §5) — an audit
     trail that can be rewritten is not evidence;
   * capabilities are enforced at the boundary, and authorization failures do
     not disclose what exists (ADR-010).
================================================================================
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.governance import audit
from eip.platform.context import ActorType, Capability, Principal, RoleCode, TenantContext
from eip.platform.db import tenant_session
from tests.conftest import Fixtures, auth, token_for

pytestmark = [pytest.mark.security, pytest.mark.integration]


def _context(
    tenant_id: uuid.UUID, user_id: uuid.UUID, capabilities: frozenset[Capability] | None = None
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="test",
        principal=Principal(
            user_id=user_id,
            external_subject="test",
            email="test@example.invalid",
            actor_type=ActorType.USER,
        ),
        role=RoleCode.TENANT_ADMIN,
        capabilities=capabilities if capabilities is not None else frozenset(Capability),
        trace_id="trace-test",
        request_id="request-test",
    )


class TestAuditTrailIsAppendOnly:
    """The runtime role must be unable to rewrite history."""

    async def test_update_is_denied_at_the_grant_level(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            await audit.record(
                session,
                context,
                action=audit.AuditAction.TENANT_CONTEXT_ESTABLISHED,
                resource_type="tenant",
                resource_id=str(seeded.tenant_a.id),
            )

        with pytest.raises(DBAPIError) as excinfo:
            async with tenant_session(app_sessions, context) as session:
                await session.execute(text("UPDATE audit_event SET action = 'tampered'"))

        assert "permission denied" in str(excinfo.value).lower()

    async def test_delete_is_denied_at_the_grant_level(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            await audit.record(
                session,
                context,
                action=audit.AuditAction.TENANT_CONTEXT_ESTABLISHED,
                resource_type="tenant",
            )

        with pytest.raises(DBAPIError) as excinfo:
            async with tenant_session(app_sessions, context) as session:
                await session.execute(text("DELETE FROM audit_event"))

        assert "permission denied" in str(excinfo.value).lower()


class TestAuditChainIsTamperEvident:
    """Hash chaining must detect modification even by a privileged writer."""

    async def test_a_valid_chain_verifies(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            for index in range(5):
                await audit.record(
                    session,
                    context,
                    action=audit.AuditAction.TENANT_CONTEXT_ESTABLISHED,
                    resource_type="tenant",
                    resource_id=f"resource-{index}",
                )

        async with tenant_session(app_sessions, context) as session:
            result = await audit.verify_chain(session, seeded.tenant_a.id)

        assert result.status is audit.ChainStatus.INTACT
        assert result.ok is True

    async def test_modification_breaks_the_chain(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """Tamper via the privileged role, then prove verification catches it.

        The runtime role cannot perform this UPDATE at all (previous test), so
        the tamper is simulated with the privileged role — modelling an
        attacker who has obtained elevated database access. Detection must not
        depend on the attacker's privilege level.
        """
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            for index in range(3):
                await audit.record(
                    session,
                    context,
                    action=audit.AuditAction.MEMBERSHIP_GRANTED,
                    resource_type="membership",
                    resource_id=f"resource-{index}",
                )

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text(
                    "UPDATE audit_event SET action = 'quietly.rewritten' "
                    "WHERE tenant_id = :tenant_id AND seq = 2"
                ),
                {"tenant_id": seeded.tenant_a.id},
            )

        async with tenant_session(app_sessions, context) as session:
            result = await audit.verify_chain(session, seeded.tenant_a.id)

        assert result.ok is False, "TAMPERING UNDETECTED: the audit chain still verified"
        assert result.status is audit.ChainStatus.MUTATED
        assert result.at_seq == 2

    async def test_chains_are_independent_per_tenant(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """Each tenant's sequence starts at 1 and is unaffected by the other."""
        context_a = _context(seeded.tenant_a.id, seeded.user_a.id)
        context_b = _context(seeded.tenant_b.id, seeded.user_b.id)

        async with tenant_session(app_sessions, context_a) as session:
            first_a = await audit.record(
                session, context_a, action="test.a", resource_type="tenant"
            )
            assert first_a.seq == 1
            assert first_a.prev_hash == audit.GENESIS_HASH

        async with tenant_session(app_sessions, context_b) as session:
            first_b = await audit.record(
                session, context_b, action="test.b", resource_type="tenant"
            )
            assert first_b.seq == 1, "tenant B's sequence was influenced by tenant A"
            assert first_b.prev_hash == audit.GENESIS_HASH


class TestAuditNeverStoresSecrets:
    """``detail`` must not carry credentials or business values."""

    async def test_forbidden_keys_are_dropped(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            event = await audit.record(
                session,
                context,
                action=audit.AuditAction.TENANT_PROVISIONED,
                resource_type="data_source",
                detail={
                    "slug": "acme",
                    "password": "hunter2",
                    "api_key": "sk-live-abc",
                    "connection_string": "postgresql://u:p@host/db",
                    "signing_secret": "s3cret",
                    "value": 1_234_567.89,
                },
            )

        assert event.detail == {"slug": "acme"}
        for forbidden in ("password", "api_key", "connection_string", "signing_secret", "value"):
            assert forbidden not in event.detail


class TestAuditVisibilityRespectsTenancy:
    """One tenant must never read another's audit trail."""

    async def test_audit_endpoint_is_tenant_scoped(
        self,
        client: AsyncClient,
        app_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        context_b = _context(seeded.tenant_b.id, seeded.user_b.id)
        async with tenant_session(app_sessions, context_b) as session:
            await audit.record(
                session,
                context_b,
                action=audit.AuditAction.MEMBERSHIP_GRANTED,
                resource_type="membership",
                resource_id="tenant-b-only-resource",
            )

        token_a = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
        events = (await client.get("/v1/audit-events", headers=auth(token_a))).json()

        assert all(event["resource_id"] != "tenant-b-only-resource" for event in events), (
            "ISOLATION FAILED: tenant A read tenant B's audit trail"
        )


class TestCapabilityEnforcement:
    """Authorization is declared at the boundary, not scattered."""

    async def test_missing_capability_is_refused(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        from eip.identity.service import TenantReadService
        from eip.platform.errors import ForbiddenError

        context = _context(seeded.tenant_a.id, seeded.user_a.id, capabilities=frozenset())
        async with tenant_session(app_sessions, context) as session:
            with pytest.raises(ForbiddenError):
                await TenantReadService().get_tenant(session, context)

    async def test_viewer_cannot_read_the_audit_trail(
        self,
        client: AsyncClient,
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """A ``viewer`` holds ``tenant.read`` but not ``audit.read``."""
        async with platform_sessions() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO membership (id, tenant_id, user_id, role_code, status) "
                    "VALUES (:id, :tenant_id, :user_id, 'viewer', 'active')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": seeded.tenant_b.id,
                    "user_id": seeded.user_orphan.id,
                },
            )

        token = await token_for(client, seeded.user_orphan.email, seeded.tenant_b.id)

        assert (await client.get("/v1/me", headers=auth(token))).status_code == 200
        assert (await client.get("/v1/audit-events", headers=auth(token))).status_code == 403
