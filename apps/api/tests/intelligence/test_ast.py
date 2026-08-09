"""Closed governed-metric expression boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from eip.intelligence.ast import SemanticFieldPolicy, validate_metric_expression


def _fields(*, additive: bool = True, published: bool = True) -> dict[str, SemanticFieldPolicy]:
    return {
        "Revenue.Amount": SemanticFieldPolicy(
            reference="Revenue.Amount",
            classification="measure",
            additive=additive,
            published=published,
            reachable_dimensions=frozenset({"segment"}),
        )
    }


def test_accepts_only_the_closed_sum_ast() -> None:
    parsed = validate_metric_expression(
        {"kind": "aggregation", "function": "sum", "field": "Revenue.Amount"},
        _fields(),
        group_by="segment",
        as_of_at=datetime.now(UTC),
    )
    assert parsed.kind == "aggregation"
    assert parsed.function == "sum"


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ({"kind": "sql", "function": "sum", "field": "Revenue.Amount"}, "aggregation/sum"),
        ({"kind": "aggregation", "function": "avg", "field": "Revenue.Amount"}, "aggregation/sum"),
        (
            {"kind": "aggregation", "function": "sum", "field": "SELECT value FROM fact"},
            "safe semantic",
        ),
        (
            {"kind": "aggregation", "function": "sum", "field": "Revenue.Amount", "sql": "x"},
            "unknown",
        ),
        ({"kind": "aggregation", "function": "sum", "field": 1.5}, "floating-point"),
    ],
)
def test_rejects_executable_unknown_and_binary_float_inputs(
    expression: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_metric_expression(expression, _fields())


def test_rejects_unknown_nonadditive_unpublished_and_unreachable_metadata() -> None:
    expression = {"kind": "aggregation", "function": "sum", "field": "Revenue.Amount"}
    with pytest.raises(ValueError, match="unavailable"):
        validate_metric_expression(expression, {})
    with pytest.raises(ValueError, match="additive"):
        validate_metric_expression(expression, _fields(additive=False))
    with pytest.raises(ValueError, match="unavailable"):
        validate_metric_expression(expression, _fields(published=False))
    with pytest.raises(ValueError, match="unpublished"):
        validate_metric_expression(expression, _fields(), configuration_published=False)
    with pytest.raises(ValueError, match="not reachable"):
        validate_metric_expression(expression, _fields(), group_by="unreachable")


def test_rejects_timezone_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        validate_metric_expression(
            {"kind": "aggregation", "function": "sum", "field": "Revenue.Amount"},
            _fields(),
            as_of_at=datetime(2026, 1, 1),  # noqa: DTZ001 - deliberately invalid input
        )
