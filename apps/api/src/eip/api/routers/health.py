"""Health and readiness probes (ADR-014 §8).

The distinction is operationally load-bearing and routinely got wrong:

``GET /health``  **Liveness.** Is this process running and able to serve? Touches
                 no dependency. If it fails, restart the process. It must never
                 check the database — a brief database outage would otherwise
                 make every replica restart, turning a recoverable incident into
                 a cascading one.

``GET /ready``   **Readiness.** Can this process serve *correct* traffic right
                 now? Verifies the dependencies whose absence would produce
                 wrong answers rather than slow ones: the control-plane
                 database, the migration state, and — because this platform's
                 core guarantee is isolation — that RLS is actually in force.

A process that is live but not ready is removed from the load balancer and left
alone. That is the behaviour we want when Postgres is briefly unavailable.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from eip.api.deps import SettingsDep, UnscopedSession
from eip.platform.db import TENANT_SETTING
from eip.platform.logging import get_logger

_log = get_logger("api.health")

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str
    version: str


class CheckResult(BaseModel):
    name: str
    status: Literal["pass", "fail"]
    detail: str = ""
    duration_ms: float = 0.0


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    service: str
    checks: list[CheckResult]


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    """Report that the process is running. Deliberately checks nothing else."""
    from eip import __version__

    return HealthResponse(
        status="ok",
        service=settings.service_name,
        environment=settings.env.value,
        version=__version__,
    )


@router.get("/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def ready(
    response: Response,
    settings: SettingsDep,
    session: UnscopedSession,
) -> ReadinessResponse:
    """Verify the dependencies required to serve correct traffic."""
    from time import perf_counter

    checks: list[CheckResult] = []

    async def run(name: str, coro: Any) -> None:
        started = perf_counter()
        try:
            detail = await coro
            checks.append(
                CheckResult(
                    name=name,
                    status="pass",
                    detail=str(detail or ""),
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    name=name,
                    status="fail",
                    # Type name only. A driver message can carry host and
                    # credential fragments, and /ready is often unauthenticated.
                    detail=type(exc).__name__,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                )
            )

    async def check_database() -> str:
        await session.execute(text("SELECT 1"))
        return "connected"

    async def check_migrations() -> str:
        revision = (
            await session.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        return f"at {revision}"

    async def check_tenant_isolation() -> str:
        """Confirm RLS is enforced and fails closed with no tenant bound.

        This session has no ``app.tenant_id``. If the tenant-scoped tables are
        genuinely protected, the count must be zero. A non-zero count means
        isolation is off — a condition under which this process must not serve
        traffic, however healthy it otherwise looks.
        """
        setting = (
            await session.execute(text(f"SELECT current_setting('{TENANT_SETTING}', true)"))
        ).scalar_one_or_none()
        if setting:  # pragma: no cover - would indicate a pool leak
            msg = "tenant setting leaked into an unscoped session"
            raise RuntimeError(msg)

        visible = (await session.execute(text("SELECT count(*) FROM membership"))).scalar_one()
        if visible != 0:
            msg = f"RLS not enforced: {visible} membership rows visible without a tenant"
            raise RuntimeError(msg)
        return "rls enforced, fails closed"

    await run("database", check_database())
    await run("migrations", check_migrations())
    await run("tenant_isolation", check_tenant_isolation())

    all_passed = all(check.status == "pass" for check in checks)
    if not all_passed:
        response.status_code = 503
        _log.error("health.not_ready", failed=[c.name for c in checks if c.status == "fail"])

    return ReadinessResponse(
        status="ready" if all_passed else "not_ready",
        service=settings.service_name,
        checks=checks,
    )
