"""
================================================================================
 RELEASE-GATING SECURITY TESTS — PRIVILEGED PLATFORM ACCESS
================================================================================

 Privileged, cross-tenant access **exists** in this system. ADR-003 §3 requires
 a path that can create tenants and operate across them.

 This file exists to prove that path is:
   * genuinely privileged (it really can cross tenants — otherwise the
     isolation tests next door would be proving nothing about a path that
     silently did not work);
   * reachable ONLY by platform staff;
   * impossible to reach without a recorded justification;
   * always audited, into the affected tenant's own trail.

 Kept in a separate file from the isolation tests on purpose. Privileged access
 is the exception, and exceptions should be small, obvious, and individually
 defended.
================================================================================
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.platform.context import ActorType, PlatformContext, Principal
from eip.platform.db import platform_session
from tests.conftest import Fixtures, auth, token_for

pytestmark = [pytest.mark.security, pytest.mark.integration]

ELEVATION = {"X-Elevation-Reason": "phase-1a automated test"}


class TestPrivilegedPathIsGenuinelyPrivileged:
    """The BYPASSRLS role must actually cross tenants."""

    async def test_platform_session_sees_every_tenant(
        self, platform_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """A negative control for the whole isolation suite.

        If this failed, the isolation tests could be passing because the
        privileged role is broken rather than because RLS works. Proving the
        privileged path *can* see across tenants is what makes the constrained
        role's inability to do so meaningful.
        """
        context = PlatformContext(
            principal=Principal(
                user_id=uuid.uuid4(),
                external_subject="test",
                email="ops@trivera.invalid",
                actor_type=ActorType.SERVICE,
            ),
            reason="verify the privileged path is genuinely privileged",
            trace_id="trace-test",
            request_id="request-test",
        )
        async with platform_session(platform_sessions, context) as session:
            tenants = {
                row.tenant_id
                for row in (await session.execute(text("SELECT tenant_id FROM membership"))).all()
            }

        assert seeded.tenant_a.id in tenants
        assert seeded.tenant_b.id in tenants


class TestPrivilegedPathRequiresPlatformRole:
    """A tenant principal must never reach the privileged endpoints."""

    async def test_tenant_admin_cannot_create_a_tenant(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "escalation-attempt", "name": "Escalation Attempt"},
            headers={**auth(token), **ELEVATION},
        )
        assert response.status_code == 403, "PRIVILEGE ESCALATION: a tenant admin created a tenant"

    async def test_tenant_admin_cannot_grant_membership(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        """A tenant admin must not be able to add themselves to another tenant."""
        token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/memberships",
            json={
                "tenant_id": str(seeded.tenant_b.id),
                "user_id": str(seeded.user_a.id),
                "role_code": "tenant_admin",
            },
            headers={**auth(token), **ELEVATION},
        )
        assert response.status_code == 403, (
            "PRIVILEGE ESCALATION: user A granted themselves access to tenant B"
        )

    async def test_unauthenticated_cannot_reach_admin(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "anon", "name": "Anonymous"},
            headers=ELEVATION,
        )
        assert response.status_code == 401


class TestElevationIsJustifiedAndAudited:
    """Privileged access requires a reason and leaves a trail."""

    async def test_missing_elevation_reason_is_refused(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_platform.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "no-reason-given", "name": "No Reason"},
            headers=auth(token),  # deliberately no X-Elevation-Reason
        )
        assert response.status_code == 403

    async def test_blank_elevation_reason_is_refused(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_platform.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "blank-reason", "name": "Blank Reason"},
            headers={**auth(token), "X-Elevation-Reason": "   "},
        )
        assert response.status_code == 403

    async def test_platform_admin_can_provision_and_it_is_audited(
        self,
        client: AsyncClient,
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """The happy path — and the audit record it must leave behind."""
        token = await token_for(client, seeded.user_platform.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/tenants",
            json={"slug": "cygnus-logistics", "name": "Cygnus Logistics"},
            headers={**auth(token), "X-Elevation-Reason": "onboarding a new customer"},
        )
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["isolation_mode"] == "schema_per_tenant"

        new_tenant_id = uuid.UUID(created["id"])

        context = PlatformContext(
            principal=Principal(
                user_id=seeded.user_platform.id,
                external_subject="subject-ops",
                email=seeded.user_platform.email,
                actor_type=ActorType.USER,
            ),
            reason="verify the audit trail",
            trace_id="trace-test",
            request_id="request-test",
        )
        async with platform_session(platform_sessions, context) as session:
            events = (
                await session.execute(
                    text(
                        "SELECT action, resource_id, detail FROM audit_event "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": new_tenant_id},
                )
            ).all()

            # The audit event lands in the NEW tenant's own chain, so the
            # customer can see that platform staff acted on their data.
            assert len(events) == 1
            assert events[0].action == "tenant.provisioned"
            assert events[0].resource_id == str(new_tenant_id)
            assert events[0].detail["elevation_reason"] == "onboarding a new customer"

            # And the tenant's analytical namespace really was provisioned.
            schema_exists = (
                await session.execute(
                    text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
                    {"name": f"tenant_{str(new_tenant_id).replace('-', '_')}"},
                )
            ).scalar_one_or_none()
            assert schema_exists == 1

            # Clean up the schema this test created.
            await session.execute(
                text(
                    f'DROP SCHEMA IF EXISTS "tenant_{str(new_tenant_id).replace("-", "_")}" CASCADE'
                )
            )

    async def test_platform_admin_cannot_grant_the_platform_role(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        """``platform_admin`` must not be assignable as a tenant membership.

        Otherwise a tenant admin who could somehow reach this endpoint could
        mint cross-tenant capability inside their own tenant.
        """
        token = await token_for(client, seeded.user_platform.email, seeded.tenant_a.id)
        response = await client.post(
            "/v1/admin/memberships",
            json={
                "tenant_id": str(seeded.tenant_b.id),
                "user_id": str(seeded.user_orphan.id),
                "role_code": "platform_admin",
            },
            headers={**auth(token), **ELEVATION},
        )
        assert response.status_code == 409
