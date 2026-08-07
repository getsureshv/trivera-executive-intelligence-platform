"""
================================================================================
 RELEASE-GATING SECURITY TESTS — AUDIT TAMPER EVIDENCE
================================================================================

 If any test in this file fails, THE BUILD MUST NOT SHIP.

 These tests exist because the Phase 1A report claimed more than the code
 delivered. The original hash covered only a subset of columns, so `occurred_at`,
 `actor_type`, `trace_id`, and `request_id` could be rewritten and the chain
 would still verify. There was no checkpoint, so deleting the final event,
 truncating to an earlier prefix, or deleting the chain entirely left a
 perfectly valid remainder — and an empty chain verified successfully. The
 report nonetheless stated that "deletion by the privileged role remains
 detectable". It was not.

 Every claim in `verify_chain`'s docstring has a test here, and every tamper is
 performed with the **privileged** role — modelling an attacker who has already
 obtained elevated database access. Detection must not depend on the attacker
 being unprivileged.

 The one blind spot is asserted honestly in `TestDocumentedLimits`: a database
 owner can drop the trigger and rewrite the checkpoint. That is stated in the
 docstring, in the report, and here — rather than left for someone to discover.
================================================================================
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eip.governance import audit
from eip.governance.audit import ChainStatus
from eip.platform.context import ActorType, Capability, Principal, RoleCode, TenantContext
from eip.platform.db import tenant_session
from tests.conftest import Fixtures

pytestmark = [pytest.mark.security, pytest.mark.integration]


def _context(tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        tenant_slug="audit-test",
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


async def _write_events(
    app_sessions: async_sessionmaker[AsyncSession],
    context: TenantContext,
    count: int,
) -> None:
    async with tenant_session(app_sessions, context) as session:
        for index in range(count):
            await audit.record(
                session,
                context,
                action=audit.AuditAction.MEMBERSHIP_GRANTED,
                resource_type="membership",
                resource_id=f"resource-{index}",
            )


async def _verify(
    app_sessions: async_sessionmaker[AsyncSession], context: TenantContext
) -> audit.ChainVerification:
    async with tenant_session(app_sessions, context) as session:
        return await audit.verify_chain(session, context.tenant_id)


# =============================================================================
# Field coverage — the columns the original hash omitted
# =============================================================================


class TestEverySecurityRelevantFieldIsHashed:
    """Mutating any immutable column must break verification.

    The four parametrised columns are precisely those the original digest left
    out. Backdating an event or reattributing it from a person to the system is
    exactly what an audit trail exists to detect.
    """

    @pytest.mark.parametrize(
        ("column", "value"),
        [
            ("occurred_at", datetime(2020, 1, 1, tzinfo=UTC)),
            ("actor_type", "system"),
            ("trace_id", "rewritten-trace"),
            ("request_id", "rewritten-request"),
            ("action", "quietly.rewritten"),
            ("resource_type", "something_else"),
            ("resource_id", "different-resource"),
            ("outcome", "failure"),
        ],
    )
    async def test_mutating_a_field_breaks_the_chain(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
        column: str,
        value: object,
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 3)

        assert (await _verify(app_sessions, context)).status is ChainStatus.INTACT

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text(
                    f"UPDATE audit_event SET {column} = :value "
                    "WHERE tenant_id = :tenant_id AND seq = 2"
                ),
                {"value": value, "tenant_id": seeded.tenant_a.id},
            )

        result = await _verify(app_sessions, context)
        assert result.status is ChainStatus.MUTATED, (
            f"mutating {column} was NOT detected — it is outside the digest"
        )
        assert result.at_seq == 2
        assert result.ok is False

    async def test_mutating_detail_breaks_the_chain(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 2)

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text(
                    'UPDATE audit_event SET detail = \'{"slug": "tampered"}\'::jsonb '
                    "WHERE tenant_id = :tenant_id AND seq = 1"
                ),
                {"tenant_id": seeded.tenant_a.id},
            )

        assert (await _verify(app_sessions, context)).status is ChainStatus.MUTATED


# =============================================================================
# Deletion — the cases the original design could not detect at all
# =============================================================================


class TestDeletionIsDetected:
    async def test_interior_deletion_is_detected(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """The case the hash chain alone already caught."""
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 4)

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM audit_event WHERE tenant_id = :t AND seq = 2"),
                {"t": seeded.tenant_a.id},
            )

        result = await _verify(app_sessions, context)
        assert result.status is ChainStatus.GAP
        assert result.at_seq == 3

    async def test_final_event_deletion_is_detected(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """Previously undetectable: the survivors form a valid chain.

        Only the checkpoint reveals that an event is missing from the end — which
        is the deletion an attacker would actually perform, since it removes the
        most recent evidence.
        """
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 4)

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM audit_event WHERE tenant_id = :t AND seq = 4"),
                {"t": seeded.tenant_a.id},
            )

        result = await _verify(app_sessions, context)
        assert result.status is ChainStatus.TRUNCATED, (
            "tail deletion was NOT detected — the checkpoint is not working"
        )
        assert result.at_seq == 3
        assert "1 event(s) were deleted" in result.detail

    async def test_truncation_to_an_earlier_prefix_is_detected(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """Also previously undetectable: a prefix is itself a valid chain."""
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 6)

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM audit_event WHERE tenant_id = :t AND seq > 2"),
                {"t": seeded.tenant_a.id},
            )

        result = await _verify(app_sessions, context)
        assert result.status is ChainStatus.TRUNCATED
        assert "4 event(s) were deleted" in result.detail

    async def test_total_deletion_is_detected(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """The worst case, and the one the original design silently passed.

        With no checkpoint, an empty chain verified as intact — so the most
        complete possible tampering produced the most reassuring possible
        result.
        """
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 5)

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text("DELETE FROM audit_event WHERE tenant_id = :t"),
                {"t": seeded.tenant_a.id},
            )

        result = await _verify(app_sessions, context)
        assert result.status is ChainStatus.ERASED, (
            "total deletion was NOT detected — an empty chain must not verify"
        )
        assert result.ok is False

    async def test_a_tenant_with_no_events_is_empty_not_intact(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """Never audited is a distinct state from erased.

        Reporting both as "intact" is what let total deletion pass.
        """
        context = _context(seeded.tenant_b.id, seeded.user_b.id)
        result = await _verify(app_sessions, context)
        assert result.status is ChainStatus.EMPTY
        assert result.ok is True


# =============================================================================
# The checkpoint itself
# =============================================================================


class TestCheckpointIsProtected:
    async def test_runtime_role_cannot_write_the_checkpoint(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 1)

        for statement in (
            "UPDATE audit_chain_head SET last_seq = 0",
            "DELETE FROM audit_chain_head",
            "INSERT INTO audit_chain_head (tenant_id, last_seq, last_hash) "
            "VALUES (gen_random_uuid(), 1, 'x')",
        ):
            async with app_sessions() as session, session.begin():
                with pytest.raises(DBAPIError) as excinfo:
                    await session.execute(text(statement))
            assert "permission denied" in str(excinfo.value).lower(), statement

    async def test_platform_role_cannot_write_the_checkpoint(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """The privileged role may delete events; it may not retract the proof.

        This is what makes `ERASED` and `TRUNCATED` meaningful. If the platform
        role could rewrite the checkpoint after deleting events, it could restore
        consistency and the detection would be worthless.
        """
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 2)

        async with platform_sessions() as session, session.begin():
            with pytest.raises(DBAPIError) as excinfo:
                await session.execute(text("UPDATE audit_chain_head SET last_seq = 0"))
        assert "permission denied" in str(excinfo.value).lower()

    async def test_checkpoint_advances_monotonically(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 3)

        async with app_sessions() as session, session.begin():
            head = (
                await session.execute(
                    text("SELECT last_seq FROM audit_chain_head WHERE tenant_id = :t"),
                    {"t": seeded.tenant_a.id},
                )
            ).scalar_one()
        assert head == 3

    async def test_checkpoints_are_independent_per_tenant(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        context_a = _context(seeded.tenant_a.id, seeded.user_a.id)
        context_b = _context(seeded.tenant_b.id, seeded.user_b.id)

        await _write_events(app_sessions, context_a, 3)
        await _write_events(app_sessions, context_b, 1)

        assert (await _verify(app_sessions, context_a)).status is ChainStatus.INTACT
        assert (await _verify(app_sessions, context_b)).status is ChainStatus.INTACT

        async with app_sessions() as session, session.begin():
            rows = dict(
                (
                    await session.execute(text("SELECT tenant_id, last_seq FROM audit_chain_head"))
                ).all()
            )
        assert rows[seeded.tenant_a.id] == 3
        assert rows[seeded.tenant_b.id] == 1


# =============================================================================
# Offboarding
# =============================================================================


class TestOffboardingSemantics:
    async def test_offboarding_marks_the_chain_as_legitimately_erased(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        platform_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """Sanctioned erasure must be distinguishable from tampering.

        Without this, GDPR erasure and a malicious wipe would look identical,
        and the alert that fires on every offboarding would train operators to
        ignore it.
        """
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 3)

        async with platform_sessions() as session, session.begin():
            await session.execute(
                text("SELECT eip_audit_chain_offboard(:t)"), {"t": seeded.tenant_a.id}
            )
            await session.execute(
                text("DELETE FROM audit_event WHERE tenant_id = :t"),
                {"t": seeded.tenant_a.id},
            )

        result = await _verify(app_sessions, context)
        assert result.status is ChainStatus.OFFBOARDED
        assert result.ok is True
        assert "3 events erased by the sanctioned path" in result.detail

    async def test_runtime_role_cannot_call_the_offboard_function(
        self, app_sessions: async_sessionmaker[AsyncSession], seeded: Fixtures
    ) -> None:
        """Otherwise the application could launder a deletion as offboarding."""
        async with app_sessions() as session, session.begin():
            with pytest.raises(DBAPIError) as excinfo:
                await session.execute(
                    text("SELECT eip_audit_chain_offboard(:t)"), {"t": seeded.tenant_a.id}
                )
        assert "permission denied" in str(excinfo.value).lower()


# =============================================================================
# What is NOT guaranteed
# =============================================================================


class TestDocumentedLimits:
    """Assert the boundary of the guarantee, so it cannot be overstated again.

    The Phase 1A report was wrong precisely because it described a guarantee
    nobody had tested. Testing the *limit* is how that stops recurring.
    """

    async def test_clock_skew_within_leeway_does_not_break_verification(
        self,
        app_sessions: async_sessionmaker[AsyncSession],
        seeded: Fixtures,
    ) -> None:
        """`occurred_at` is hashed, so its serialisation must be stable.

        A naive implementation that hashed a timezone-local rendering would fail
        verification whenever the reader's session timezone differed from the
        writer's — a false positive that would quickly get the check disabled.
        """
        context = _context(seeded.tenant_a.id, seeded.user_a.id)
        await _write_events(app_sessions, context, 2)

        # A tenant_session, not a bare one: without app.tenant_id the events are
        # invisible to RLS and the chain would read as erased rather than intact.
        async with tenant_session(app_sessions, context) as session:
            await session.execute(text("SET LOCAL TIME ZONE 'Asia/Kolkata'"))
            result = await audit.verify_chain(session, seeded.tenant_a.id)
        assert result.status is ChainStatus.INTACT

    def test_hash_is_sensitive_to_every_hashed_field(self) -> None:
        """A pure check of the digest inputs, independent of the database."""
        base = {
            "prev_hash": audit.GENESIS_HASH,
            "tenant_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "seq": 1,
            "occurred_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            "actor_type": "user",
            "actor_user_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
            "action": "tenant.provisioned",
            "resource_type": "tenant",
            "resource_id": "abc",
            "outcome": "success",
            "trace_id": "t",
            "request_id": "r",
            "detail": {"slug": "acme"},
        }
        reference = audit.compute_hash(**base)  # type: ignore[arg-type]

        mutations: list[tuple[str, object]] = [
            ("occurred_at", datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)),
            ("actor_type", "system"),
            ("trace_id", "t2"),
            ("request_id", "r2"),
            ("seq", 2),
            ("action", "other"),
            ("resource_type", "other"),
            ("resource_id", "other"),
            ("outcome", "failure"),
            ("detail", {"slug": "other"}),
            ("prev_hash", "f" * 64),
        ]
        for field, value in mutations:
            altered = {**base, field: value}
            assert audit.compute_hash(**altered) != reference, (  # type: ignore[arg-type]
                f"{field} is not covered by the digest"
            )

    def test_utc_normalisation_is_applied(self) -> None:
        """The same instant in two timezones must hash identically."""
        from datetime import timezone

        instant = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        shifted = instant.astimezone(timezone(timedelta(hours=5, minutes=30)))
        common: dict[str, object] = {
            "prev_hash": audit.GENESIS_HASH,
            "tenant_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "seq": 1,
            "actor_type": "user",
            "actor_user_id": None,
            "action": "a",
            "resource_type": "t",
            "resource_id": None,
            "outcome": "success",
            "trace_id": "t",
            "request_id": "r",
            "detail": {},
        }
        assert audit.compute_hash(occurred_at=instant, **common) == audit.compute_hash(  # type: ignore[arg-type]
            occurred_at=shifted, **common
        )
