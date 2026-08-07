"""
================================================================================
 RELEASE-GATING SECURITY TESTS — TENANT ISOLATION
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 A failure here means one customer can see another customer's data. In an
 Executive Intelligence Platform that data is revenue, margin, pipeline, and
 compensation. This is not a functional regression; it is a breach.

 Do not:
   * mark these tests xfail or skip to unblock a release;
   * weaken an assertion to match new behaviour — if behaviour changed, the
     change is wrong until an ADR says otherwise;
   * delete a test because "the code no longer works that way".

 These tests encode ADR-003 (tenant isolation) and ADR-010 (authorization).
 Changing them requires a superseding ADR, not a pull-request comment.
================================================================================

The suite proves isolation at three independent layers, because any one of them
could be bypassed by a future mistake:

  1. **Database (RLS).**  Even a query with no ``WHERE tenant_id`` returns only
     the active tenant's rows. This is the defence-in-depth layer: it holds
     when application filtering is forgotten.
  2. **Context resolution.**  A token naming a tenant the user does not belong
     to is refused before any query is compiled.
  3. **HTTP.**  Identifier manipulation in the path, headers, and token all
     fail, and fail *indistinguishably from a non-existent resource*.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from eip.platform.context import ActorType, Capability, Principal, RoleCode, TenantContext
from eip.platform.db import TENANT_SETTING, tenant_session, unscoped_session
from tests.conftest import Fixtures, auth, token_for

pytestmark = [pytest.mark.security, pytest.mark.integration]


def _context(tenant_id: uuid.UUID, slug: str, user_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug=slug,
        principal=Principal(
            user_id=user_id,
            external_subject="test",
            email="test@example.invalid",
            actor_type=ActorType.USER,
        ),
        role=RoleCode.TENANT_ADMIN,
        capabilities=frozenset(Capability),
        trace_id="trace-test",
        request_id="request-test",
    )


# =============================================================================
# LAYER 1 — Database-enforced isolation (defence in depth)
# =============================================================================


class TestDatabaseLevelIsolation:
    """RLS must isolate tenants even when the query does not filter.

    Every query below deliberately omits ``WHERE tenant_id = ...``. That is the
    point: these tests prove the database is the backstop, so a forgotten
    application filter degrades to *zero rows* rather than to another
    customer's data.
    """

    async def test_tenant_a_sees_only_tenant_a_rows(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.tenant_a.slug, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            rows = (await session.execute(text("SELECT tenant_id FROM membership"))).all()

        assert rows, "tenant A must see its own membership"
        assert {row.tenant_id for row in rows} == {seeded.tenant_a.id}

    async def test_tenant_b_sees_only_tenant_b_rows(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_b.id, seeded.tenant_b.slug, seeded.user_b.id)
        async with tenant_session(app_sessions, context) as session:
            rows = (await session.execute(text("SELECT tenant_id FROM membership"))).all()

        assert rows, "tenant B must see its own membership"
        assert {row.tenant_id for row in rows} == {seeded.tenant_b.id}

    async def test_tenant_a_cannot_read_tenant_b_rows_even_when_naming_them(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """Explicitly asking for tenant B's rows while scoped to A returns none.

        This is the strongest form of the check: the query *names* the other
        tenant's id, so nothing but RLS is preventing the read.
        """
        context = _context(seeded.tenant_a.id, seeded.tenant_a.slug, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM membership WHERE tenant_id = :other"),
                    {"other": seeded.tenant_b.id},
                )
            ).scalar_one()

        assert count == 0, "RLS FAILED: tenant A read tenant B's rows"

    async def test_tenant_b_cannot_read_tenant_a_rows_even_when_naming_them(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_b.id, seeded.tenant_b.slug, seeded.user_b.id)
        async with tenant_session(app_sessions, context) as session:
            count = (
                await session.execute(
                    text("SELECT count(*) FROM membership WHERE tenant_id = :other"),
                    {"other": seeded.tenant_a.id},
                )
            ).scalar_one()

        assert count == 0, "RLS FAILED: tenant B read tenant A's rows"

    async def test_unscoped_session_sees_nothing(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """With no tenant bound, tenant-scoped tables must be empty.

        Fail-closed. The policy compares against NULL, which is never TRUE, so
        an unset tenant yields no rows rather than all rows — the difference
        between a safe default and a catastrophic one.
        """
        async with unscoped_session(app_sessions) as session:
            for table in ("membership", "audit_event", "outbox"):
                count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                assert count == 0, f"RLS FAILED: {table} readable without tenant context"

    async def test_blank_tenant_setting_is_treated_as_unset(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """An empty ``app.tenant_id`` must not error or widen access.

        Guards the ``NULLIF(..., '')`` in the policy. Without it, an empty
        string would raise on the ``::uuid`` cast, and a naive fix might have
        been to drop the cast — which would change the comparison semantics.
        """
        async with app_sessions() as session, session.begin():
            await session.execute(text(f"SELECT set_config('{TENANT_SETTING}', '', true)"))
            count = (await session.execute(text("SELECT count(*) FROM membership"))).scalar_one()
            assert count == 0

    async def test_insert_for_another_tenant_is_rejected(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """The policy's WITH CHECK must block writes across the boundary.

        Reading is the obvious risk; writing is the subtler one. Without
        ``WITH CHECK``, tenant A could *insert* rows into tenant B's data —
        poisoning another customer's audit trail, for instance.
        """
        from sqlalchemy.exc import DBAPIError

        context = _context(seeded.tenant_a.id, seeded.tenant_a.slug, seeded.user_a.id)
        with pytest.raises(DBAPIError):
            async with tenant_session(app_sessions, context) as session:
                await session.execute(
                    text(
                        "INSERT INTO outbox (id, tenant_id, topic, payload, trace_id) "
                        "VALUES (:id, :tenant_id, 'probe', '{}', 'trace')"
                    ),
                    {"id": uuid.uuid4(), "tenant_id": seeded.tenant_b.id},
                )

    async def test_tenant_setting_does_not_leak_across_pool_checkouts(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """``SET LOCAL`` must not survive a connection returning to the pool.

        This is the failure that would be hardest to notice in production: a
        pooled connection retaining the previous request's tenant would serve
        one customer's data to the next request that happened to reuse it.
        """
        context = _context(seeded.tenant_a.id, seeded.tenant_a.slug, seeded.user_a.id)
        async with tenant_session(app_sessions, context) as session:
            bound = (
                await session.execute(text(f"SELECT current_setting('{TENANT_SETTING}', true)"))
            ).scalar_one()
            assert bound == str(seeded.tenant_a.id)

        # A fresh session, very likely reusing the same pooled connection.
        async with unscoped_session(app_sessions) as session:
            leaked = (
                await session.execute(text(f"SELECT current_setting('{TENANT_SETTING}', true)"))
            ).scalar_one_or_none()
            assert not leaked, f"TENANT SETTING LEAKED ACROSS CHECKOUT: {leaked!r}"

    async def test_runtime_role_cannot_bypass_rls(self, app_engine: AsyncEngine) -> None:
        """The runtime role must not be superuser, BYPASSRLS, or table owner.

        Every other test in this file is meaningless if this one fails: a role
        with any of those attributes ignores policies entirely, so isolation
        would appear to work in tests written against a *different* role.
        """
        async with app_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()
            assert row.rolsuper is False, "runtime role is a SUPERUSER; RLS does not apply"
            assert row.rolbypassrls is False, "runtime role has BYPASSRLS; RLS does not apply"

            owned = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tableowner = current_user"
                    )
                )
            ).scalar_one()
            assert owned == 0, "runtime role owns tables; owners can disable RLS"


# =============================================================================
# LAYER 2 — Tenant-context resolution
# =============================================================================


class TestTenantContextResolution:
    """Tenant context must come from membership, never from the caller."""

    async def test_token_naming_a_foreign_tenant_is_refused(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        """THE decisive check.

        User A asks for a token scoped to tenant B. The token issuer will mint
        it — it only asserts identity. Membership resolution must then refuse
        it, before any tenant-scoped query exists.
        """
        token = await token_for(client, seeded.user_a.email, seeded.tenant_b.id)
        response = await client.get("/v1/me", headers=auth(token))

        assert response.status_code == 403, (
            "ISOLATION FAILED: user A obtained a context in tenant B"
        )

    async def test_token_naming_a_foreign_tenant_is_refused_inverse(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_b.email, seeded.tenant_a.id)
        response = await client.get("/v1/me", headers=auth(token))

        assert response.status_code == 403, (
            "ISOLATION FAILED: user B obtained a context in tenant A"
        )

    async def test_token_naming_a_nonexistent_tenant_is_refused(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_a.email, uuid.uuid4())
        assert (await client.get("/v1/me", headers=auth(token))).status_code == 403

    async def test_user_with_no_membership_is_refused(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        """Authenticated is not authorized."""
        token = await token_for(client, seeded.user_orphan.email)
        response = await client.get("/v1/me", headers=auth(token))
        assert response.status_code == 403

    async def test_each_user_resolves_to_their_own_tenant(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token_a = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
        token_b = await token_for(client, seeded.user_b.email, seeded.tenant_b.id)

        body_a = (await client.get("/v1/me", headers=auth(token_a))).json()
        body_b = (await client.get("/v1/me", headers=auth(token_b))).json()

        assert body_a["tenant"]["id"] == str(seeded.tenant_a.id)
        assert body_b["tenant"]["id"] == str(seeded.tenant_b.id)
        assert body_a["tenant"]["id"] != body_b["tenant"]["id"]


# =============================================================================
# LAYER 3 — HTTP surface: identifier and header manipulation
# =============================================================================


class TestIdentifierManipulation:
    """Every way a client can try to name another tenant must fail."""

    async def test_path_identifier_manipulation_returns_not_found(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        """Requesting tenant B by id, as user A, must 404 — not 403.

        404 rather than 403 is deliberate (ADR-010 §4): a 403 would confirm
        that tenant B exists, letting an attacker enumerate the platform's
        customer list by probing identifiers.
        """
        token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)

        own = await client.get(f"/v1/tenants/{seeded.tenant_a.id}", headers=auth(token))
        assert own.status_code == 200

        foreign = await client.get(f"/v1/tenants/{seeded.tenant_b.id}", headers=auth(token))
        assert foreign.status_code == 404, "ISOLATION FAILED: user A fetched tenant B by identifier"

    async def test_path_identifier_manipulation_returns_not_found_inverse(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token = await token_for(client, seeded.user_b.email, seeded.tenant_b.id)

        assert (
            await client.get(f"/v1/tenants/{seeded.tenant_b.id}", headers=auth(token))
        ).status_code == 200
        assert (
            await client.get(f"/v1/tenants/{seeded.tenant_a.id}", headers=auth(token))
        ).status_code == 404

    async def test_nonexistent_and_unauthorized_are_indistinguishable(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        """A tenant that exists-but-is-forbidden must look like one that does not."""
        token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)

        forbidden = await client.get(f"/v1/tenants/{seeded.tenant_b.id}", headers=auth(token))
        absent = await client.get(f"/v1/tenants/{uuid.uuid4()}", headers=auth(token))

        assert forbidden.status_code == absent.status_code == 404
        assert forbidden.json()["code"] == absent.json()["code"]
        assert forbidden.json()["title"] == absent.json()["title"]
        assert forbidden.json()["detail"] == absent.json()["detail"]

    @pytest.mark.parametrize(
        "header",
        ["X-Tenant-Id", "X-Tenant", "X-Org-Id", "X-Organization-Id"],
    )
    async def test_tenant_headers_are_ignored(
        self, client: AsyncClient, seeded: Fixtures, header: str
    ) -> None:
        """A browser-supplied tenant header must have no effect whatsoever."""
        token = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)

        response = await client.get(
            "/v1/me",
            headers={**auth(token), header: str(seeded.tenant_b.id)},
        )

        assert response.status_code == 200
        assert response.json()["tenant"]["id"] == str(seeded.tenant_a.id), (
            f"ISOLATION FAILED: {header} influenced tenant resolution"
        )

    async def test_membership_list_never_includes_another_tenant(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        token_a = await token_for(client, seeded.user_a.email, seeded.tenant_a.id)
        emails = {
            item["email"]
            for item in (await client.get("/v1/memberships", headers=auth(token_a))).json()
        }

        assert seeded.user_a.email in emails
        assert seeded.user_b.email not in emails, "ISOLATION FAILED: tenant A saw tenant B's member"


# =============================================================================
# Authentication boundary
# =============================================================================


class TestAuthenticationBoundary:
    """No credential, no data."""

    @pytest.mark.parametrize("path", ["/v1/me", "/v1/memberships", "/v1/audit-events"])
    async def test_requests_without_a_token_are_rejected(
        self, client: AsyncClient, seeded: Fixtures, path: str
    ) -> None:
        assert (await client.get(path)).status_code == 401

    async def test_malformed_authorization_header_is_rejected(
        self, client: AsyncClient, seeded: Fixtures
    ) -> None:
        for value in ("", "Bearer", "Bearer ", "Basic abc123", "not-a-scheme token"):
            response = await client.get("/v1/me", headers={"Authorization": value})
            assert response.status_code == 401, f"accepted malformed header {value!r}"

    async def test_forged_token_is_rejected(self, client: AsyncClient, seeded: Fixtures) -> None:
        """A token signed with the wrong key must not authenticate.

        Also implicitly covers algorithm confusion: verification pins a single
        algorithm, so an ``alg: none`` token cannot be accepted either.
        """
        import jwt

        forged = jwt.encode(
            {
                "sub": seeded.user_a.subject,
                "iss": "https://local.eip.invalid/",
                "aud": "eip-api",
                "iat": 1_700_000_000,
                "exp": 4_102_444_800,
                "tid": str(seeded.tenant_b.id),
            },
            "an-attacker-controlled-key-of-sufficient-length-32b",
            algorithm="HS256",
        )
        assert (await client.get("/v1/me", headers=auth(forged))).status_code == 401

    async def test_unsigned_token_is_rejected(self, client: AsyncClient, seeded: Fixtures) -> None:
        import jwt

        unsigned = jwt.encode(
            {
                "sub": seeded.user_a.subject,
                "iss": "https://local.eip.invalid/",
                "aud": "eip-api",
                "iat": 1_700_000_000,
                "exp": 4_102_444_800,
            },
            key="",
            algorithm="none",
        )
        assert (await client.get("/v1/me", headers=auth(unsigned))).status_code == 401
