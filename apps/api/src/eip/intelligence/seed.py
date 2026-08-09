"""Deterministic, privileged seed/reset for the explicitly labeled demo dataset."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from importlib.resources import files
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from eip.intelligence.ast import SemanticFieldPolicy, validate_metric_expression
from eip.intelligence.models import ConfigurationBundle, DemoDataset, DemoMetadata, GovernedFact

_NAMESPACE = uuid.UUID("406b777d-68ee-4f02-a173-35fb89a74c98")


def _id(tenant_id: uuid.UUID, identity: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{tenant_id}:{identity}")


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_seed() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(files("eip.intelligence").joinpath("demo_seed.json").read_text("utf-8")),
    )


async def reset_demo(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Delete only this tenant's demo bundle; cascades remove its graph and facts."""
    await session.execute(
        text("SELECT eip_reset_seeded_demo(:tenant_id,:bundle_id)"),
        {"tenant_id": tenant_id, "bundle_id": _id(tenant_id, "bundle:1")},
    )


async def seed_demo(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    author_id: uuid.UUID,
    source_id: uuid.UUID,
) -> None:
    payload = load_seed()
    source_version = await session.scalar(
        text(
            "SELECT version FROM data_source WHERE tenant_id=:tenant_id AND id=:source_id "
            "AND connector_type='postgresql' AND status='active'"
        ),
        {"tenant_id": tenant_id, "source_id": source_id},
    )
    if source_version is None:
        raise ValueError("selected PostgreSQL source is unavailable in this tenant")
    successful = await session.scalar(
        text(
            "SELECT id FROM connection_test WHERE tenant_id=:tenant_id "
            "AND data_source_id=:source_id AND source_version=:source_version "
            "AND status='succeeded' ORDER BY completed_at DESC LIMIT 1"
        ),
        {
            "tenant_id": tenant_id,
            "source_id": source_id,
            "source_version": source_version,
        },
    )
    if successful is None:
        raise ValueError("selected source requires a successful current-version connection test")
    await reset_demo(session, tenant_id)
    dataset_payload = payload["dataset"]
    as_of = datetime.fromisoformat(dataset_payload["as_of_at"])
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("demo as-of timestamp must be timezone-aware")
    bundle_id = _id(tenant_id, "bundle:1")
    dataset_id = _id(tenant_id, "dataset:ceo_demo")
    session.add(
        ConfigurationBundle(
            id=bundle_id,
            tenant_id=tenant_id,
            version=1,
            status="published",
            content_hash=_hash(payload),
            author_id=author_id,
            approver_id=author_id,
            published_at=as_of,
            change_reason=dataset_payload["change_reason"],
        )
    )
    session.add(
        DemoDataset(
            id=dataset_id,
            tenant_id=tenant_id,
            bundle_id=bundle_id,
            code=dataset_payload["code"],
            label=dataset_payload["label"],
            origin=dataset_payload["origin"],
            description=dataset_payload["description"],
            as_of_at=as_of,
            reset_version=1,
        )
    )
    await session.flush()
    identities: dict[str, uuid.UUID] = {}
    pending = list(payload["metadata"])
    while pending:
        progressed = False
        for item in pending[:]:
            dependencies = [item.get("parent"), item.get("related")]
            if any(dependency and dependency not in identities for dependency in dependencies):
                continue
            identity = f"{item['kind']}:{item['code']}"
            record_id = _id(tenant_id, identity)
            identities[identity] = record_id
            allowed_dimension = item["attributes"].get("allowed_dimension")
            allowed_dimension_id = (
                identities.get(f"dimension:{allowed_dimension}") if allowed_dimension else None
            )
            if item["kind"] == "metric_version":
                semantic_fields = {
                    candidate["name"]: SemanticFieldPolicy(
                        reference=candidate["name"],
                        classification=candidate["attributes"]["classification"],
                        additive=bool(candidate["attributes"]["additive"]),
                        published=True,
                        reachable_dimensions=frozenset({str(allowed_dimension)}),
                    )
                    for candidate in payload["metadata"]
                    if candidate["kind"] == "semantic_field"
                }
                validate_metric_expression(
                    item["attributes"]["expression"],
                    semantic_fields,
                    group_by=str(allowed_dimension),
                    as_of_at=as_of,
                )
            session.add(
                DemoMetadata(
                    id=record_id,
                    tenant_id=tenant_id,
                    bundle_id=bundle_id,
                    dataset_id=dataset_id,
                    kind=item["kind"],
                    code=item["code"],
                    name=item["name"],
                    version=1,
                    status=item.get("status", "published"),
                    origin=dataset_payload["origin"],
                    parent_id=identities.get(item.get("parent")),
                    related_id=identities.get(item.get("related")),
                    data_source_id=source_id if item["kind"] == "source_object" else None,
                    allowed_dimension_id=allowed_dimension_id,
                    attributes=item["attributes"],
                    content_hash=_hash(item),
                )
            )
            await session.flush()
            pending.remove(item)
            progressed = True
        if not progressed:
            raise ValueError("demo metadata contains an unresolved relationship")
    metric_versions = [
        value for key, value in identities.items() if key.startswith("metric_version:")
    ]
    if len(metric_versions) != 1:
        raise ValueError("demo seed must contain exactly one metric version")
    metric_id = metric_versions[0]
    segment_values = {
        item["code"]: identities[f"dimension_value:{item['code']}"]
        for item in payload["metadata"]
        if item["kind"] == "dimension_value"
    }
    observations = [
        Decimal(item["value"]) for item in payload["facts"] if item["kind"] == "observation"
    ]
    targets = [Decimal(item["value"]) for item in payload["facts"] if item["kind"] == "target"]
    if sum(observations[1:]) != observations[0] or sum(targets[1:]) != targets[0]:
        raise ValueError("demo facts do not reconcile exactly")
    for index, item in enumerate(payload["facts"]):
        session.add(
            GovernedFact(
                id=_id(tenant_id, f"fact:{index}"),
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                metric_version_id=metric_id,
                dimension_value_id=segment_values.get(item["dimension"]),
                kind=item["kind"],
                period_start=date(as_of.year, 1, 1),
                period_end=as_of.date(),
                value=Decimal(item["value"]),
                prior_value=Decimal(item["prior_value"]) if item.get("prior_value") else None,
                computed_at=as_of,
                snapshot_id=_id(tenant_id, "snapshot:1"),
                config_version=1,
                origin=dataset_payload["origin"],
                owner_label=dataset_payload["owner_label"],
                quality_status=dataset_payload["quality_status"]
                if item["kind"] == "observation"
                else None,
                quality_code=dataset_payload["quality_code"]
                if item["kind"] == "observation"
                else None,
                quality_evaluated_at=as_of if item["kind"] == "observation" else None,
                freshness_status=dataset_payload["freshness_status"]
                if item["kind"] == "observation"
                else None,
                freshness_code=dataset_payload["freshness_code"]
                if item["kind"] == "observation"
                else None,
                freshness_evaluated_at=as_of if item["kind"] == "observation" else None,
            )
        )
    await session.flush()
