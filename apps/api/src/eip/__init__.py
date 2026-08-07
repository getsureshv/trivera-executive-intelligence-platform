"""TriVera Executive Intelligence Platform — modular monolith (ADR-001).

Bounded contexts are packages under ``eip``. Cross-context access goes through
a context's public ``interfaces``/``service`` module, never its ``models`` or
internals; ``import-linter`` contracts in ``.importlinter`` enforce this in CI.
"""

__version__ = "0.1.0"
