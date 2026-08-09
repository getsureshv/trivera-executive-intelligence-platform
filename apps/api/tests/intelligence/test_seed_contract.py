"""The checked-in demo fixture is exact, honest, and isolated from product code."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from eip.intelligence.seed import load_seed


def test_seed_decimal_reconciliation_and_honesty_boundary() -> None:
    payload = load_seed()
    dataset = payload["dataset"]
    assert dataset["origin"] == "seeded_demo"
    assert dataset["label"] == "Demo dataset / seeded demonstration data"
    facts = payload["facts"]
    observations = [Decimal(item["value"]) for item in facts if item["kind"] == "observation"]
    targets = [Decimal(item["value"]) for item in facts if item["kind"] == "target"]
    assert sum(observations[1:]) == observations[0]
    assert sum(targets[1:]) == targets[0]
    assert all(not isinstance(item["value"], float) for item in facts)


def test_demo_business_values_do_not_leak_into_production_source() -> None:
    root = Path(__file__).resolve().parents[3]
    forbidden = ("4210500", "3980000", "4500000", "1850000", "1410500", "950000")
    offenders: list[str] = []
    for path in sorted((root / "src").rglob("*")):
        if path.suffix not in {".py", ".ts", ".tsx"}:
            continue
        content = path.read_text(encoding="utf-8")
        if any(value in content for value in forbidden):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
