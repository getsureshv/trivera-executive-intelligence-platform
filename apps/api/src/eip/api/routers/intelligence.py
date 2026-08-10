"""Authorized governed-metric and derived-lineage read endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from eip.api.deps import TenantSession, require
from eip.governance.audit import AuditAction, record
from eip.intelligence.service import ExecutiveQueryService
from eip.platform.context import Capability, TenantContext
from eip.platform.errors import ValidationError

router = APIRouter(prefix="/v1", tags=["executive-intelligence"])
service = ExecutiveQueryService()


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class ClosedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MetricPeriodRequest(ClosedRequest):
    kind: Literal["calendar_ytd"]
    timezone: Literal["America/Chicago"]
    as_of_at: datetime


class MetricQueryRequest(ClosedRequest):
    period: MetricPeriodRequest
    group_by: str | None = None


class MetricPeriod(ClosedModel):
    kind: Literal["calendar_ytd"]
    timezone: Literal["America/Chicago"]
    start: str
    end: str
    as_of_at: str


class DecimalComparison(ClosedModel):
    absolute: str
    percent: str | None


class QualityCheck(ClosedModel):
    code: str
    status: Literal["pass", "warn", "fail"]
    evaluated_at: str


class SelectedSourceHealth(ClosedModel):
    data_source_id: str
    connection_test_id: str
    source_version: int
    connection_status: Literal["succeeded"]
    relationship: Literal["selected_source_connection_health_only"]


class MetricProvenance(ClosedModel):
    configuration_version: int
    snapshot_id: str
    calculated_at: str
    dataset_id: str
    origin: Literal["seeded_demo"]
    origin_label: Literal["Demo dataset / seeded demonstration data"]
    observation_basis: Literal["seeded_demo_observations_not_live_extraction"]
    selected_source: SelectedSourceHealth


class MetricAuthorization(ClosedModel):
    row_scope_applied: Literal[True]
    redactions: list[str]


class DrillDownValue(ClosedModel):
    dimension_value_id: str
    label: str
    value: str
    target: str
    target_variance: str


class AttentionItem(DrillDownValue):
    pass


class GovernedMetricResponse(ClosedModel):
    metric_id: str
    metric_version: int
    metric_name: str
    period: MetricPeriod
    value: str
    prior_value: str
    comparison: DecimalComparison
    target: str
    target_variance: DecimalComparison
    unit: str
    format: str
    freshness_status: Literal["fresh", "stale"]
    freshness_as_of: str
    quality_status: Literal["pass", "warn", "fail"]
    quality_checks: list[QualityCheck]
    accountable_owner: str
    provenance: MetricProvenance
    authorization: MetricAuthorization
    allowed_drill_down: list[str]
    drill_down: list[DrillDownValue]
    attention: AttentionItem
    lineage_handle: str


class LineageNode(ClosedModel):
    id: str
    kind: Literal[
        "widget",
        "metric_version",
        "semantic_field",
        "field_binding",
        "source_field",
        "source_object",
        "data_source",
    ]
    label: str


class LineageEdge(ClosedModel):
    from_: str = Field(alias="from")
    to: str
    relation: str


class LineageResponse(ClosedModel):
    configuration_version: int
    origin: Literal["seeded_demo"]
    provenance: MetricProvenance
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    authorization: MetricAuthorization


ExecutiveContext = Annotated[TenantContext, Depends(require(Capability.EXECUTIVE_READ))]
MetricContext = Annotated[TenantContext, Depends(require(Capability.METRIC_QUERY))]
LineageContext = Annotated[TenantContext, Depends(require(Capability.LINEAGE_READ))]


async def _audit_result(
    session: TenantSession,
    context: TenantContext,
    result: dict[str, object],
    *,
    action: str,
    row_count: int,
) -> None:
    await record(
        session,
        context,
        action=action,
        resource_type="metric",
        resource_id=str(result["metric_id"]),
        detail={
            "metric_version": result["metric_version"],
            "configuration_version": cast(dict[str, object], result["provenance"])[
                "configuration_version"
            ],
            "row_scope_applied": True,
            "redaction_count": 0,
            "row_count": row_count,
        },
    )


@router.get("/dashboards/executive", response_model=GovernedMetricResponse)
async def executive_dashboard(
    context: ExecutiveContext, session: TenantSession
) -> GovernedMetricResponse:
    result = await service.query(session, metric_code="revenue_ytd")
    await _audit_result(session, context, result, action=AuditAction.DASHBOARD_VIEWED, row_count=1)
    return GovernedMetricResponse.model_validate(result)


@router.post("/metrics/revenue_ytd/query", response_model=GovernedMetricResponse)
async def query_revenue(
    payload: MetricQueryRequest,
    context: MetricContext,
    session: TenantSession,
) -> GovernedMetricResponse:
    if payload.period.as_of_at.tzinfo is None or payload.period.as_of_at.utcoffset() is None:
        raise ValidationError("The as-of timestamp must be timezone-aware.")
    result = await service.query(
        session,
        metric_code="revenue_ytd",
        group_by=payload.group_by,
        requested_as_of=payload.period.as_of_at,
    )
    drill_down = cast(list[object], result["drill_down"])
    await _audit_result(
        session,
        context,
        result,
        action=(
            AuditAction.METRIC_DRILLDOWN_QUERIED
            if payload.group_by is not None
            else AuditAction.METRIC_QUERIED
        ),
        row_count=len(drill_down) if drill_down else 1,
    )
    return GovernedMetricResponse.model_validate(result)


@router.get("/metrics/revenue_ytd/lineage", response_model=LineageResponse)
async def revenue_lineage(
    context: LineageContext,
    session: TenantSession,
    config_version: Annotated[int, Query(gt=0)],
) -> LineageResponse:
    result = await service.lineage(
        session, metric_code="revenue_ytd", config_version=config_version
    )
    await record(
        session,
        context,
        action=AuditAction.LINEAGE_VIEWED,
        resource_type="metric",
        detail={
            "configuration_version": config_version,
            "row_scope_applied": True,
            "redaction_count": 0,
            "node_count": len(cast(list[object], result["nodes"])),
            "edge_count": len(cast(list[object], result["edges"])),
        },
    )
    return LineageResponse.model_validate(result)
