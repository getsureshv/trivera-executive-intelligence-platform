"""Bounded-context import contracts (ADR-001).

"Strong bounded contexts" is a claim that must be checkable, not an aspiration
in a document. This module parses every source file's imports and asserts the
allowed dependency graph.

Implemented as an AST walk rather than with `import-linter` deliberately: that
tool's `grimp` backend is a compiled extension without wheels on every platform
we develop on, and a boundary check that cannot run on a developer's machine is
a boundary check that rots.

The contracts:

* ``eip.platform`` depends on no other context — it is the shared foundation.
* A context may import another context's ``interfaces``, ``service``, or
  ``models``-free public surface, never its internals.
* Only ``eip.api`` may import FastAPI.
* Vendor SDKs stay in adapters; business logic programs to ports
  (guardrail 15).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "eip"

#: Contexts, in dependency order. Each may depend on `platform` and on the
#: public surface of those listed before it.
CONTEXTS = ("platform", "identity", "governance", "dataplane", "api")

#: Operational entry points, not bounded contexts. They compose everything, are
#: never imported by application code, and are excluded from the graph rules.
ENTRYPOINTS = ("scripts",)

#: Cross-context dependencies that are permitted. Anything absent is a
#: violation. Kept explicit so widening it is a visible, reviewable diff.
ALLOWED: dict[str, set[str]] = {
    "platform": set(),
    "identity": {"platform", "governance", "dataplane"},
    "governance": {"platform"},
    "dataplane": {"platform"},
    # The HTTP layer composes everything; it owns no domain logic itself.
    "api": {"platform", "identity", "governance", "dataplane"},
    "scripts": {"platform", "identity", "governance", "dataplane"},
}


def _iter_modules() -> list[Path]:
    return sorted(path for path in SRC.rglob("*.py") if "__pycache__" not in path.parts)


def _context_of(path: Path) -> str | None:
    relative = path.relative_to(SRC)
    return relative.parts[0] if len(relative.parts) > 1 else None


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def test_contexts_respect_the_allowed_dependency_graph() -> None:
    violations: list[str] = []

    for path in _iter_modules():
        context = _context_of(path)
        if context is None or context not in ALLOWED:
            continue

        for module in _imported_modules(path):
            if not module.startswith("eip."):
                continue
            target = module.split(".")[1]
            if target == context or target not in CONTEXTS:
                continue
            if target not in ALLOWED[context]:
                violations.append(
                    f"{path.relative_to(SRC)}: '{context}' may not import '{target}' "
                    f"(allowed: {sorted(ALLOWED[context]) or 'none'})"
                )

    assert not violations, "Bounded-context violations (ADR-001):\n  " + "\n  ".join(violations)


def test_platform_depends_on_no_other_context() -> None:
    """The foundation must stay a foundation.

    If ``platform`` ever imports a context, the dependency graph acquires a
    cycle and the monolith stops being modular — which is the whole point of
    the layout.
    """
    violations = [
        f"{path.relative_to(SRC)} imports {module}"
        for path in _iter_modules()
        if _context_of(path) == "platform"
        for module in _imported_modules(path)
        if module.startswith("eip.") and module.split(".")[1] not in ("platform",)
    ]
    assert not violations, "eip.platform must not depend on any context:\n  " + "\n  ".join(
        violations
    )


def test_only_the_api_context_imports_fastapi() -> None:
    """Web-framework coupling stays at the edge.

    Domain code that imports FastAPI cannot be reused by the worker, cannot be
    tested without an HTTP layer, and drifts toward request-shaped design.
    """
    violations = [
        f"{path.relative_to(SRC)} imports {module}"
        for path in _iter_modules()
        if _context_of(path) not in ("api", None)
        for module in _imported_modules(path)
        if module.split(".")[0] in ("fastapi", "starlette")
    ]
    assert not violations, "Only eip.api may import a web framework (ADR-001):\n  " + "\n  ".join(
        violations
    )


def test_domain_code_does_not_import_a_database_driver() -> None:
    """Business logic programs to SQLAlchemy, never to a driver.

    A direct ``asyncpg`` import would couple the domain to PostgreSQL and would
    break the analytical-engine port ADR-008 depends on.
    """
    violations = [
        f"{path.relative_to(SRC)} imports {module}"
        for path in _iter_modules()
        for module in _imported_modules(path)
        if module.split(".")[0] in ("asyncpg", "psycopg", "psycopg2")
    ]
    assert not violations, "Database drivers must not be imported directly:\n  " + "\n  ".join(
        violations
    )


@pytest.mark.parametrize("context", ["identity", "governance", "dataplane"])
def test_contexts_do_not_import_api_internals(context: str) -> None:
    """Dependencies point inward. A domain context importing the HTTP layer
    inverts the architecture and creates an import cycle."""
    violations = [
        f"{path.relative_to(SRC)} imports {module}"
        for path in _iter_modules()
        if _context_of(path) == context
        for module in _imported_modules(path)
        if module.startswith("eip.api")
    ]
    assert not violations, f"'{context}' must not import eip.api:\n  " + "\n  ".join(violations)


def test_set_role_is_never_executed() -> None:
    """The shared ``SET ROLE`` mechanism is gone and must stay gone.

    Analytical isolation previously depended on one shared credential assuming a
    per-tenant role. That was enforced by PostgreSQL only *after* the switch,
    and the switch was an application decision — so code naming the wrong tenant
    would have been obeyed (finding G10).

    Each tenant now connects with its own credential. There is no role to
    assume, and ``eip_app`` is a member of nothing, so a reintroduced ``SET
    ROLE`` would fail at runtime — but it would also signal that someone was
    trying to rebuild the shared-credential design, which is why this fails the
    build instead.
    """
    executable = re.compile(r'SET\s+(?:LOCAL\s+)?ROLE\s+"')
    offenders = sorted(
        str(path.relative_to(SRC)).replace("\\", "/")
        for path in _iter_modules()
        if executable.search(path.read_text(encoding="utf-8"))
    )
    assert offenders == [], (
        "SET ROLE must not appear in application code; analytical access uses each "
        "tenant's own credential (ADR-003 §2). Found in: " + ", ".join(offenders)
    )
