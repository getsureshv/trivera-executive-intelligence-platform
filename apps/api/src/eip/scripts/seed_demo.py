"""Bootstrap a local development environment.

    python -m eip.scripts.seed_demo

Creates a platform administrator and **two** tenants with one member each. Two,
not one, deliberately: tenant isolation is the property Phase 1A exists to
prove, and it cannot be observed with a single tenant.

Guarded to ``local``/``ci``. Seeding identities in a real environment would
manufacture accounts nobody authorised.

This is the one place that legitimately runs on the privileged role outside a
request, because creating the first tenant necessarily precedes any membership
that could authorise it. Every action it takes is audited like any other
platform-admin operation.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text

from eip.dataplane.registry import build_data_plane
from eip.identity.models import AppUser, Membership, Tenant
from eip.identity.service import TenantProvisioningService
from eip.platform.context import ActorType, PlatformContext, Principal, RoleCode
from eip.platform.db import create_engines, create_session_factory, platform_session
from eip.platform.logging import configure_logging, get_logger, new_trace_id
from eip.platform.settings import get_settings

_log = get_logger("scripts.seed_demo")


@dataclass(frozen=True, slots=True)
class SeedUser:
    email: str
    subject: str
    display_name: str


PLATFORM_ADMIN = SeedUser("ops@trivera.invalid", "subject-ops", "Platform Operations")

TENANTS: tuple[tuple[str, str, SeedUser], ...] = (
    (
        "acme-industrial",
        "Acme Industrial",
        SeedUser("ada@acme.invalid", "subject-ada", "Ada Okafor"),
    ),
    (
        "borealis-capital",
        "Borealis Capital",
        SeedUser("ben@borealis.invalid", "subject-ben", "Ben Nakamura"),
    ),
)


async def _upsert_user(session: object, issuer: str, user: SeedUser) -> uuid.UUID:
    """Create the user if absent; return its id either way (idempotent)."""
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)

    existing = (
        await session.execute(
            select(AppUser.id).where(
                AppUser.issuer == issuer, AppUser.external_subject == user.subject
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    record = AppUser(
        id=uuid.uuid4(),
        issuer=issuer,
        external_subject=user.subject,
        email=user.email,
        display_name=user.display_name,
        status="active",
    )
    session.add(record)
    await session.flush()
    return record.id


async def seed() -> None:
    settings = get_settings()
    configure_logging(settings)

    if not settings.env.allows_dev_auth:
        sys.stderr.write(
            f"Refusing to seed in environment {settings.env.value!r}. "
            "Seeding is permitted only in local and ci.\n"
        )
        raise SystemExit(2)

    engines = create_engines(settings)
    platform_factory = create_session_factory(engines.platform)
    data_plane = build_data_plane(settings, engines.platform)
    service = TenantProvisioningService(data_plane)

    trace_id = new_trace_id()
    admin_principal = Principal(
        user_id=uuid.uuid4(),
        external_subject=PLATFORM_ADMIN.subject,
        email=PLATFORM_ADMIN.email,
        actor_type=ActorType.USER,
    )
    context = PlatformContext(
        principal=admin_principal,
        reason="local development environment bootstrap",
        trace_id=trace_id,
        request_id=trace_id,
    )

    created: list[tuple[str, str, str]] = []

    try:
        async with platform_session(platform_factory, context) as session:
            admin_id = await _upsert_user(session, settings.auth_issuer, PLATFORM_ADMIN)
            # Rebuild the context so audit events attribute to the real user row
            # rather than the placeholder id used to construct it.
            context = PlatformContext(
                principal=Principal(
                    user_id=admin_id,
                    external_subject=PLATFORM_ADMIN.subject,
                    email=PLATFORM_ADMIN.email,
                    actor_type=ActorType.USER,
                ),
                reason=context.reason,
                trace_id=trace_id,
                request_id=trace_id,
            )

            for slug, name, member in TENANTS:
                already = (
                    await session.execute(select(Tenant.id).where(Tenant.slug == slug))
                ).scalar_one_or_none()
                if already is not None:
                    _log.info("seed.tenant_exists", slug=slug)
                    tenant_id = already
                else:
                    tenant = await service.create_tenant(session, context, slug=slug, name=name)
                    await service.provision_data_plane(tenant)
                    tenant_id = tenant.id

                member_id = await _upsert_user(session, settings.auth_issuer, member)

                has_membership = (
                    await session.execute(
                        select(Membership.id).where(
                            Membership.tenant_id == tenant_id,
                            Membership.user_id == member_id,
                        )
                    )
                ).scalar_one_or_none()
                if has_membership is None:
                    await service.add_membership(
                        session,
                        context,
                        tenant_id=tenant_id,
                        user_id=member_id,
                        role=RoleCode.TENANT_ADMIN,
                    )

                created.append((slug, str(tenant_id), member.email))

            # The platform administrator needs a membership somewhere to obtain a
            # token, since tenant context is always resolved from membership. It
            # is granted in the first tenant with the platform_admin role, which
            # `add_membership` refuses — so it is inserted directly here, in the
            # one bootstrap path where no prior administrator exists.
            first_tenant_id = uuid.UUID(created[0][1])
            has_admin_membership = (
                await session.execute(
                    select(Membership.id).where(
                        Membership.tenant_id == first_tenant_id,
                        Membership.user_id == admin_id,
                    )
                )
            ).scalar_one_or_none()
            if has_admin_membership is None:
                await session.execute(
                    text(
                        "INSERT INTO membership (id, tenant_id, user_id, role_code, status) "
                        "VALUES (:id, :tenant_id, :user_id, 'platform_admin', 'active')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant_id": first_tenant_id,
                        "user_id": admin_id,
                    },
                )
    finally:
        await engines.app.dispose()
        await engines.platform.dispose()

    _report(created)


def _report(created: list[tuple[str, str, str]]) -> None:
    """Print the sign-in details. The only sanctioned ``print`` in the codebase."""
    lines = [
        "",
        "=" * 78,
        " Local development environment ready",
        "=" * 78,
        "",
        " Two organizations exist so that tenant isolation can be observed:",
        " sign in as one member and try to reach the other organization's data.",
        "",
    ]
    for slug, tenant_id, email in created:
        lines += [
            f"   {slug}",
            f"     organization id : {tenant_id}",
            f"     sign in as      : {email}",
            "",
        ]
    lines += [
        f"   platform staff    : {PLATFORM_ADMIN.email}",
        "     (holds platform_admin; required for POST /v1/admin/tenants)",
        "",
        " No passwords exist. The platform is not an identity provider (ADR-010);",
        " this environment mints short-lived development tokens instead.",
        "",
        " Open http://localhost:3000",
        "=" * 78,
        "",
    ]
    sys.stdout.write("\n".join(lines))


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
