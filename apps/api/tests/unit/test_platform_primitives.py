"""Unit tests for platform primitives.

These run without any infrastructure, so they execute on every machine and in
the fast CI lane. They cover the invariants that are pure functions of the
code: secret handling, redaction, the audit hash, context construction, and
data-plane naming.
"""

from __future__ import annotations

import json
import pickle
import uuid
from datetime import UTC, datetime

import pytest

from eip.dataplane.interfaces import DataPlaneHandle, TenantRef
from eip.governance.audit import GENESIS_HASH, compute_hash
from eip.platform.context import (
    ROLE_CAPABILITIES,
    ActorType,
    Capability,
    PlatformContext,
    Principal,
    RoleCode,
    TenantContext,
)
from eip.platform.errors import ForbiddenError, NotFoundError, NotImplementedModeError
from eip.platform.logging import is_loggable_key, redact
from eip.platform.secrets import SecretRef, SecretValue
from eip.platform.settings import Environment, IsolationMode, Settings
from eip.platform.telemetry import safe_attributes


def _principal() -> Principal:
    return Principal(
        user_id=uuid.uuid4(),
        external_subject="subject",
        email="user@example.invalid",
        actor_type=ActorType.USER,
    )


class TestSecretValue:
    """A secret must be structurally incapable of leaking (ADR-015 §2)."""

    def test_repr_and_str_are_masked(self) -> None:
        secret = SecretValue("hunter2")
        assert "hunter2" not in repr(secret)
        assert "hunter2" not in str(secret)
        assert "hunter2" not in f"{secret}"
        assert "hunter2" not in f"{secret!r}"

    def test_reveal_returns_the_value(self) -> None:
        assert SecretValue("hunter2").reveal() == "hunter2"

    def test_cannot_be_pickled(self) -> None:
        with pytest.raises(TypeError):
            pickle.dumps(SecretValue("hunter2"))

    def test_cannot_be_json_serialised(self) -> None:
        with pytest.raises(TypeError):
            json.dumps({"secret": SecretValue("hunter2")})

    def test_cannot_be_used_as_a_cache_key(self) -> None:
        """Unhashable on purpose: a secret must never become a dict or cache key."""
        with pytest.raises(TypeError):
            {SecretValue("hunter2"): "value"}  # type: ignore[misc]

    def test_secret_ref_is_tenant_namespaced(self) -> None:
        """Paths are prefix-scoped so IAM can bound the blast radius."""
        tenant_id = uuid.uuid4()
        ref = SecretRef(tenant_id=tenant_id, logical_name="pg-password", version="1")
        assert ref.path == f"tenants/{tenant_id}/pg-password"


class TestRedaction:
    """Log and telemetry payloads must drop, not mask (ADR-014 §6)."""

    @pytest.mark.parametrize(
        "key",
        ["password", "secret", "token", "api_key", "connection_string", "dsn", "signing_secret"],
    )
    def test_credential_keys_are_dropped(self, key: str) -> None:
        assert redact({key: "x", "safe": 1}) == {"safe": 1}
        assert is_loggable_key(key) is False

    @pytest.mark.parametrize("key", ["value", "values", "rows", "sample", "prompt", "completion"])
    def test_business_value_keys_are_dropped(self, key: str) -> None:
        """Metric and source values are the product's payload, never telemetry."""
        assert key not in redact({key: 42, "metric_code": "revenue_ytd"})

    def test_keys_containing_secret_are_dropped(self) -> None:
        assert redact({"client_secret_v2": "x"}) == {}

    def test_identifiers_are_preserved(self) -> None:
        payload = {"tenant_id": "t", "metric_code": "revenue_ytd", "rows_returned": 12}
        assert redact(payload) == payload


class TestTelemetryAllowlist:
    """Attributes are permitted by allowlist, dropped by default."""

    def test_unknown_attributes_are_dropped(self) -> None:
        assert safe_attributes({"customer_name": "Acme", "tenant_id": "t"}) == {"tenant_id": "t"}

    def test_value_bearing_attributes_are_dropped_even_if_allowlisted(self) -> None:
        """Belt and braces: the denylist wins over a mistaken allowlist entry."""
        assert "value" not in safe_attributes({"value": 1.0})


class TestTenantContext:
    """Capability checks are explicit and fail closed."""

    def test_require_passes_when_held(self) -> None:
        context = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_slug="acme",
            principal=_principal(),
            role=RoleCode.TENANT_ADMIN,
            capabilities=frozenset({Capability.TENANT_READ}),
            trace_id="t",
            request_id="r",
        )
        context.require(Capability.TENANT_READ)

    def test_require_raises_when_missing(self) -> None:
        context = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_slug="acme",
            principal=_principal(),
            role=RoleCode.VIEWER,
            capabilities=frozenset({Capability.TENANT_READ}),
            trace_id="t",
            request_id="r",
        )
        with pytest.raises(ForbiddenError):
            context.require(Capability.MEMBERSHIP_MANAGE)

    def test_context_is_immutable(self) -> None:
        """Frozen so a downstream layer cannot widen its own scope."""
        context = TenantContext(
            tenant_id=uuid.uuid4(),
            tenant_slug="acme",
            principal=_principal(),
            role=RoleCode.VIEWER,
            capabilities=frozenset(),
            trace_id="t",
            request_id="r",
        )
        with pytest.raises((AttributeError, TypeError)):
            context.tenant_id = uuid.uuid4()  # type: ignore[misc]

    def test_only_platform_admin_holds_the_provisioning_capability(self) -> None:
        """No tenant role may provision. Escalation would otherwise be a grant away."""
        for role, capabilities in ROLE_CAPABILITIES.items():
            has_it = Capability.PLATFORM_TENANT_PROVISION in capabilities
            assert has_it is (role is RoleCode.PLATFORM_ADMIN), role


class TestPlatformContext:
    """Privileged access requires a stated reason (ADR-010 §5)."""

    def test_reason_is_required(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            PlatformContext(principal=_principal(), reason="", trace_id="t", request_id="r")

    def test_whitespace_reason_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="reason"):
            PlatformContext(principal=_principal(), reason="   ", trace_id="t", request_id="r")


class TestAuditHash:
    """The chain hash must be deterministic and sensitive to every field."""

    def _hash(self, **overrides: object) -> str:
        base: dict[str, object] = {
            "prev_hash": GENESIS_HASH,
            "tenant_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
            "seq": 1,
            # Present since the Phase 1A remediation: these four were outside
            # the digest and could be rewritten undetected.
            "occurred_at": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            "actor_type": "user",
            "trace_id": "trace-1",
            "request_id": "request-1",
            "action": "tenant.provisioned",
            "resource_type": "tenant",
            "resource_id": "abc",
            "actor_user_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
            "outcome": "success",
            "detail": {"slug": "acme"},
        }
        base.update(overrides)
        return compute_hash(**base)  # type: ignore[arg-type]

    def test_is_deterministic(self) -> None:
        assert self._hash() == self._hash()

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("seq", 2),
            ("action", "tenant.deprovisioned"),
            ("resource_id", "different"),
            ("outcome", "failure"),
            ("detail", {"slug": "other"}),
            ("prev_hash", "f" * 64),
            ("occurred_at", datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC)),
            ("actor_type", "system"),
            ("trace_id", "trace-2"),
            ("request_id", "request-2"),
        ],
    )
    def test_changing_any_field_changes_the_hash(self, field: str, value: object) -> None:
        assert self._hash(**{field: value}) != self._hash()

    def test_detail_key_order_does_not_matter(self) -> None:
        """Canonical JSON: otherwise verification would fail spuriously."""
        assert self._hash(detail={"a": 1, "b": 2}) == self._hash(detail={"b": 2, "a": 1})


class TestDataPlaneAbstraction:
    """Business code must not depend on schema-per-tenant assumptions."""

    def test_qualify_rejects_unsafe_object_names(self) -> None:
        handle = DataPlaneHandle(
            tenant_id=uuid.uuid4(), mode=IsolationMode.SCHEMA_PER_TENANT, namespace="tenant_x"
        )
        with pytest.raises(ValueError, match="Unsafe"):
            handle.qualify('revenue"; DROP TABLE tenant; --')

    def test_qualify_produces_a_schema_qualified_identifier(self) -> None:
        handle = DataPlaneHandle(
            tenant_id=uuid.uuid4(), mode=IsolationMode.SCHEMA_PER_TENANT, namespace="tenant_x"
        )
        assert handle.qualify("sem_revenue") == '"tenant_x"."sem_revenue"'

    def test_qualify_is_mode_dependent(self) -> None:
        """The same call yields a different identifier under a different mode —
        which is the property that lets the mode change without touching
        callers."""
        tenant_id = uuid.uuid4()
        schema_mode = DataPlaneHandle(
            tenant_id=tenant_id, mode=IsolationMode.SCHEMA_PER_TENANT, namespace="tenant_x"
        )
        database_mode = DataPlaneHandle(
            tenant_id=tenant_id, mode=IsolationMode.DATABASE_PER_TENANT, namespace="db_x"
        )
        assert schema_mode.qualify("t") != database_mode.qualify("t")

    def test_namespace_is_derived_from_the_tenant_id_not_the_slug(self) -> None:
        """A renamed tenant must not orphan its data."""
        from eip.dataplane.schema_per_tenant import SchemaPerTenantDataPlane

        plane = SchemaPerTenantDataPlane(
            platform_engine=None,  # type: ignore[arg-type]
            schema_prefix="tenant_",
        )
        tenant_id = uuid.uuid4()
        first = plane.namespace_for(TenantRef(tenant_id=tenant_id, slug="original"))
        renamed = plane.namespace_for(TenantRef(tenant_id=tenant_id, slug="renamed"))
        assert first == renamed

    def test_unimplemented_modes_fail_loudly(self) -> None:
        """Silently degrading to weaker isolation would be the worst outcome."""
        from eip.dataplane.registry import build_data_plane

        for mode in (
            IsolationMode.SHARED_RLS,
            IsolationMode.DATABASE_PER_TENANT,
            IsolationMode.DEDICATED_DEPLOYMENT,
        ):
            settings = Settings(env=Environment.CI, data_plane_mode=mode)
            with pytest.raises(NotImplementedModeError):
                build_data_plane(settings, None)  # type: ignore[arg-type]


class TestErrorTaxonomy:
    """Errors must not disclose the existence of unauthorized resources."""

    def test_not_found_carries_an_ambiguous_title(self) -> None:
        problem = NotFoundError().to_problem(correlation_id="c", instance="/v1/tenants/x")
        assert problem["status"] == 404
        assert "not permitted" in problem["title"].lower()

    def test_problem_documents_carry_a_correlation_id(self) -> None:
        problem = ForbiddenError().to_problem(correlation_id="trace-123", instance="/v1/me")
        assert problem["correlation_id"] == "trace-123"
        assert problem["code"] == "FORBIDDEN"


class TestSettingsGuards:
    """Configuration must fail closed."""

    def test_dev_auth_is_only_allowed_in_local_and_ci(self) -> None:
        assert Environment.LOCAL.allows_dev_auth is True
        assert Environment.CI.allows_dev_auth is True
        for env in (Environment.DEV, Environment.STAGING, Environment.PRODUCTION):
            assert env.allows_dev_auth is False, f"{env} must not permit the dev token issuer"

    def test_schema_prefix_must_be_a_valid_identifier(self) -> None:
        with pytest.raises(ValueError, match="identifier"):
            Settings(env=Environment.CI, data_plane_schema_prefix="tenant-; DROP")

    def test_dev_token_issuance_is_refused_outside_local_and_ci(self) -> None:
        from eip.identity.auth import issue_dev_token
        from eip.platform.errors import ConfigurationError

        settings = Settings(env=Environment.PRODUCTION)
        with pytest.raises(ConfigurationError):
            issue_dev_token(settings, subject="anyone", tenant_id=None)
