"""Closed metric expression parser; it never accepts executable text."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class MetricExpression:
    kind: Literal["aggregation"]
    function: Literal["sum"]
    field: str


@dataclass(frozen=True, slots=True)
class SemanticFieldPolicy:
    reference: str
    classification: Literal["measure", "dimension"]
    additive: bool
    published: bool
    reachable_dimensions: frozenset[str]


def validate_metric_expression(
    raw: Mapping[str, object],
    fields: Mapping[str, SemanticFieldPolicy],
    *,
    group_by: str | None = None,
    configuration_published: bool = True,
    as_of_at: datetime | None = None,
) -> MetricExpression:
    """Parse the sole accepted AST and resolve it against published metadata."""
    if any(isinstance(value, float) for value in raw.values()):
        raise ValueError("binary floating-point values are forbidden")
    if as_of_at is not None and (as_of_at.tzinfo is None or as_of_at.utcoffset() is None):
        raise ValueError("metric timestamps must be timezone-aware")
    if not configuration_published:
        raise ValueError("configuration bundle is unpublished")
    if set(raw) != {"kind", "function", "field"}:
        raise ValueError("metric expression has unknown or missing keys")
    if raw["kind"] != "aggregation" or raw["function"] != "sum":
        raise ValueError("only aggregation/sum is supported")
    reference = raw["field"]
    if (
        not isinstance(reference, str)
        or not reference
        or any(token in reference.lower() for token in ("select ", " from ", ";", "(", ")"))
    ):
        raise ValueError("field must be a safe semantic reference")
    policy = fields.get(reference)
    if policy is None or not policy.published:
        raise ValueError("semantic field is unavailable")
    if policy.classification != "measure" or not policy.additive:
        raise ValueError("sum requires an authorized additive measure")
    if group_by is not None and group_by not in policy.reachable_dimensions:
        raise ValueError("dimension is not reachable from the measure")
    return MetricExpression("aggregation", "sum", reference)
