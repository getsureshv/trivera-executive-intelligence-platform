"""Authorized-read implementation over frozen, tenant-scoped demo metadata."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from eip.intelligence.ast import SemanticFieldPolicy, validate_metric_expression
from eip.intelligence.models import ConfigurationBundle, DemoDataset, DemoMetadata, GovernedFact
from eip.platform.errors import NotFoundError, ValidationError


def _decimal(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." not in rendered:
        return rendered
    return rendered.rstrip("0").rstrip(".") or "0"


def _percent(numerator: Decimal, denominator: Decimal) -> str | None:
    if denominator == 0:
        return None
    return _decimal((numerator / denominator) * Decimal(100))


def _metadata(rows: list[DemoMetadata], kind: str, *, code: str | None = None) -> DemoMetadata:
    matches = [row for row in rows if row.kind == kind and (code is None or row.code == code)]
    if len(matches) != 1:
        raise NotFoundError()
    return matches[0]


def _attributes(row: DemoMetadata) -> Mapping[str, object]:
    return cast(Mapping[str, object], row.attributes)


class ExecutiveQueryService:
    async def query(
        self,
        session: AsyncSession,
        *,
        metric_code: str,
        config_version: int | None = None,
        group_by: str | None = None,
        requested_as_of: datetime | None = None,
    ) -> dict[str, object]:
        bundles = list(
            await session.scalars(
                select(ConfigurationBundle).where(ConfigurationBundle.status == "published")
            )
        )
        if len(bundles) != 1:
            raise NotFoundError()
        bundle = bundles[0]
        if config_version is not None and bundle.version != config_version:
            raise NotFoundError()
        datasets = list(
            await session.scalars(select(DemoDataset).where(DemoDataset.bundle_id == bundle.id))
        )
        if len(datasets) != 1:
            raise NotFoundError()
        dataset = datasets[0]
        if requested_as_of is not None:
            if requested_as_of.tzinfo is None or requested_as_of.utcoffset() is None:
                raise ValidationError("The as-of timestamp must be timezone-aware.")
            if requested_as_of != dataset.as_of_at:
                raise NotFoundError()

        rows = list(
            await session.scalars(
                select(DemoMetadata).where(
                    DemoMetadata.bundle_id == bundle.id,
                    DemoMetadata.dataset_id == dataset.id,
                )
            )
        )
        metric = _metadata(rows, "metric", code=metric_code)
        metric_version = _metadata(rows, "metric_version", code=metric_code)
        dimension = _metadata(rows, "dimension")
        dashboard = _metadata(rows, "dashboard")
        widget = _metadata(rows, "widget")
        if (
            metric_version.parent_id != metric.id
            or metric_version.allowed_dimension_id != dimension.id
            or widget.parent_id != dashboard.id
            or widget.related_id != metric_version.id
        ):
            raise NotFoundError()
        semantic_field = next(
            (
                row
                for row in rows
                if row.id == metric_version.related_id and row.kind == "semantic_field"
            ),
            None,
        )
        if semantic_field is None:
            raise NotFoundError()
        expression = _attributes(metric_version).get("expression")
        if not isinstance(expression, Mapping):
            raise NotFoundError()
        classification = _attributes(semantic_field).get("classification")
        if classification not in ("measure", "dimension"):
            raise NotFoundError()
        policy = SemanticFieldPolicy(
            reference=semantic_field.name,
            classification=classification,
            additive=_attributes(semantic_field).get("additive") is True,
            published=semantic_field.status == "published",
            reachable_dimensions=frozenset({dimension.code}),
        )
        try:
            validate_metric_expression(
                expression,
                {semantic_field.name: policy},
                group_by=group_by,
                configuration_published=bundle.status == "published",
                as_of_at=dataset.as_of_at,
            )
        except ValueError as exc:
            raise NotFoundError() from exc

        facts = list(
            await session.scalars(
                select(GovernedFact).where(
                    GovernedFact.dataset_id == dataset.id,
                    GovernedFact.metric_version_id == metric_version.id,
                )
            )
        )
        observation = next(
            (row for row in facts if row.kind == "observation" and row.dimension_value_id is None),
            None,
        )
        target = next(
            (row for row in facts if row.kind == "target" and row.dimension_value_id is None), None
        )
        if (
            observation is None
            or target is None
            or observation.prior_value is None
            or observation.quality_status is None
            or observation.quality_code is None
            or observation.quality_evaluated_at is None
            or observation.freshness_status is None
            or observation.freshness_code is None
            or observation.freshness_evaluated_at is None
        ):
            raise NotFoundError()
        value = observation.value
        prior_delta = value - observation.prior_value
        target_delta = value - target.value

        def dimension_order(row: DemoMetadata) -> int:
            order = _attributes(row).get("order")
            if not isinstance(order, int) or isinstance(order, bool):
                raise NotFoundError()
            return order

        dimension_values = sorted(
            (
                row
                for row in rows
                if row.kind == "dimension_value" and row.parent_id == dimension.id
            ),
            key=dimension_order,
        )
        drill_down: list[dict[str, object]] = []
        for dimension_value in dimension_values:
            slice_observation = next(
                (
                    row
                    for row in facts
                    if row.kind == "observation" and row.dimension_value_id == dimension_value.id
                ),
                None,
            )
            slice_target = next(
                (
                    row
                    for row in facts
                    if row.kind == "target" and row.dimension_value_id == dimension_value.id
                ),
                None,
            )
            if slice_observation is None or slice_target is None:
                raise NotFoundError()
            drill_down.append(
                {
                    "dimension_value_id": str(dimension_value.id),
                    "label": dimension_value.name,
                    "value": _decimal(slice_observation.value),
                    "target": _decimal(slice_target.value),
                    "target_variance": _decimal(slice_observation.value - slice_target.value),
                }
            )
        if sum(Decimal(str(item["value"])) for item in drill_down) != value:
            raise NotFoundError()
        rule = _metadata(rows, "attention_rule")
        if (
            rule.parent_id != widget.id
            or rule.related_id != dimension.id
            or _attributes(rule).get("comparator") != "largest_negative_target_variance"
        ):
            raise NotFoundError()
        attention = min(drill_down, key=lambda item: Decimal(str(item["target_variance"])))

        source_object = _metadata(rows, "source_object")
        if source_object.data_source_id is None:
            raise NotFoundError()
        source = (
            (
                await session.execute(
                    text(
                        "SELECT id,version,name FROM data_source "
                        "WHERE id=:source_id AND status='active'"
                    ),
                    {"source_id": source_object.data_source_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if source is None:
            raise NotFoundError()
        connection_test = (
            (
                await session.execute(
                    text(
                        "SELECT id FROM connection_test WHERE data_source_id=:source_id "
                        "AND source_version=:source_version AND status='succeeded' "
                        "ORDER BY completed_at DESC LIMIT 1"
                    ),
                    {"source_id": source["id"], "source_version": source["version"]},
                )
            )
            .mappings()
            .one_or_none()
        )
        if connection_test is None:
            raise NotFoundError()
        metric_attrs = _attributes(metric_version)
        return {
            "metric_id": str(metric.id),
            "metric_version": metric_version.version,
            "metric_name": metric.name,
            "period": {
                "kind": "calendar_ytd",
                "timezone": metric_attrs.get("timezone"),
                "start": observation.period_start.isoformat(),
                "end": observation.period_end.isoformat(),
                "as_of_at": dataset.as_of_at.isoformat(),
            },
            "value": _decimal(value),
            "prior_value": _decimal(observation.prior_value),
            "comparison": {
                "absolute": _decimal(prior_delta),
                "percent": _percent(prior_delta, observation.prior_value),
            },
            "target": _decimal(target.value),
            "target_variance": {
                "absolute": _decimal(target_delta),
                "percent": _percent(target_delta, target.value),
            },
            "unit": metric_attrs.get("unit"),
            "format": metric_attrs.get("format"),
            "freshness_status": observation.freshness_status,
            "freshness_as_of": observation.freshness_evaluated_at.isoformat(),
            "quality_status": observation.quality_status,
            "quality_checks": [
                {
                    "code": observation.quality_code,
                    "status": observation.quality_status,
                    "evaluated_at": observation.quality_evaluated_at.isoformat(),
                }
            ],
            "accountable_owner": observation.owner_label,
            "provenance": {
                "configuration_version": bundle.version,
                "snapshot_id": str(observation.snapshot_id),
                "calculated_at": observation.computed_at.isoformat(),
                "dataset_id": str(dataset.id),
                "origin": dataset.origin,
                "origin_label": dataset.label,
                "observation_basis": "seeded_demo_observations_not_live_extraction",
                "selected_source": {
                    "data_source_id": str(source["id"]),
                    "connection_test_id": str(connection_test["id"]),
                    "source_version": source["version"],
                    "connection_status": "succeeded",
                    "relationship": "selected_source_connection_health_only",
                },
            },
            "authorization": {"row_scope_applied": True, "redactions": []},
            "allowed_drill_down": [dimension.code],
            "drill_down": drill_down if group_by is not None else [],
            "attention": attention,
            "lineage_handle": f"{metric_version.id}:{bundle.version}",
            "_rows": rows,
            "_source_label": source["name"],
        }

    async def lineage(
        self, session: AsyncSession, *, metric_code: str, config_version: int
    ) -> dict[str, object]:
        result = await self.query(session, metric_code=metric_code, config_version=config_version)
        rows = cast(list[DemoMetadata], result.pop("_rows"))
        metric_version = _metadata(rows, "metric_version", code=metric_code)
        widget = _metadata(rows, "widget")
        semantic_field = next((row for row in rows if row.id == metric_version.related_id), None)
        if semantic_field is None:
            raise NotFoundError()
        field_binding = next(
            (
                row
                for row in rows
                if row.kind == "field_binding" and row.parent_id == semantic_field.id
            ),
            None,
        )
        source_field = next(
            (
                row
                for row in rows
                if field_binding is not None and row.id == field_binding.related_id
            ),
            None,
        )
        source_object = next(
            (row for row in rows if source_field is not None and row.id == source_field.parent_id),
            None,
        )
        if field_binding is None or source_field is None or source_object is None:
            raise NotFoundError()
        source = cast(
            dict[str, object], cast(dict[str, object], result["provenance"])["selected_source"]
        )
        nodes = [
            {"id": str(widget.id), "kind": "widget", "label": widget.name},
            {"id": str(metric_version.id), "kind": "metric_version", "label": metric_version.name},
            {"id": str(semantic_field.id), "kind": "semantic_field", "label": semantic_field.name},
            {"id": str(field_binding.id), "kind": "field_binding", "label": field_binding.name},
            {"id": str(source_field.id), "kind": "source_field", "label": source_field.name},
            {"id": str(source_object.id), "kind": "source_object", "label": source_object.name},
            {
                "id": str(source["data_source_id"]),
                "kind": "data_source",
                "label": str(result["_source_label"]),
            },
        ]
        edges = [
            {"from": nodes[index]["id"], "to": nodes[index + 1]["id"], "relation": "derives_from"}
            for index in range(len(nodes) - 1)
        ]
        return {
            "configuration_version": config_version,
            "origin": "seeded_demo",
            "provenance": result["provenance"],
            "nodes": nodes,
            "edges": edges,
            "authorization": {"row_scope_applied": True, "redactions": []},
        }
