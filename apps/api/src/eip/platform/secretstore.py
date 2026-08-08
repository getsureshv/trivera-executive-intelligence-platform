"""``SecretStore`` adapters (ADR-015).

Phase 1A shipped the port and the ``SecretValue`` type but no implementation,
because nothing yet held a secret. Per-tenant analytical credentials change
that: each tenant now has a database password, and ADR-015 requires those to be
reachable only through this abstraction — never a column, never a config file,
never a job payload.

Two adapters:

``InMemorySecretStore``  tests only. Never selectable from configuration.
``FileSecretStore``      ``local``/``ci``/``dev``. One ``0600`` file per secret
                         under a root directory that is outside source control.

A cloud adapter (AWS Secrets Manager, GCP Secret Manager, Vault) is the
production implementation and is deliberately absent: Phase 1A does not deploy
to production, and writing an untested cloud adapter would be worse than
declaring the gap. ``build_secret_store`` refuses to start in a production-like
environment for exactly that reason, rather than silently falling back to files.

Every adapter upholds the same invariants:

* values are returned as ``SecretValue``, which cannot be logged, formatted, or
  serialised;
* ``describe`` returns metadata and never a value;
* every read carries a ``purpose`` and is logged **without** the value, so the
  audit question "what read this secret, and why" is answerable.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from eip.platform.errors import ConfigurationError, NotFoundError
from eip.platform.logging import get_logger
from eip.platform.secrets import SecretRef, SecretStore, SecretValue
from eip.platform.settings import Settings

_log = get_logger("platform.secretstore")

#: Owner read/write only. A secret file readable by the group or world is not a
#: secret; the mode is asserted on read as well as set on write.
_SECRET_FILE_MODE: Final = 0o600
_SECRET_DIR_MODE: Final = 0o700


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    """What may be disclosed about a secret. Never the value."""

    ref: SecretRef
    created_at: datetime


class InMemorySecretStore:
    """Non-durable store for tests.

    Deliberately not selectable through configuration: a process that lost its
    secrets on restart would fail in confusing ways, and the failure would look
    like a database problem rather than a configuration one.
    """

    def __init__(self) -> None:
        self._values: dict[str, tuple[SecretValue, datetime]] = {}

    async def put(self, tenant_id: uuid.UUID, logical_name: str, value: SecretValue) -> SecretRef:
        ref = SecretRef(tenant_id=tenant_id, logical_name=logical_name, version="1")
        self._values[ref.path] = (value, datetime.now(UTC))
        return ref

    async def get(self, ref: SecretRef, *, purpose: str) -> SecretValue:
        entry = self._values.get(ref.path)
        if entry is None:
            raise NotFoundError()
        _log.info("secret.read", logical_name=ref.logical_name, purpose=purpose)
        return entry[0]

    async def rotate(self, ref: SecretRef, value: SecretValue) -> SecretRef:
        rotated = SecretRef(
            tenant_id=ref.tenant_id,
            logical_name=ref.logical_name,
            version=str(int(ref.version) + 1),
        )
        self._values[rotated.path] = (value, datetime.now(UTC))
        return rotated

    async def delete(self, ref: SecretRef) -> None:
        self._values.pop(ref.path, None)

    async def describe(self, ref: SecretRef) -> SecretMetadata:
        entry = self._values.get(ref.path)
        if entry is None:
            raise NotFoundError()
        return SecretMetadata(ref=ref, created_at=entry[1])


class FileSecretStore:
    """Filesystem-backed store for local development and CI.

    One JSON file per secret version, mode ``0600``, under a root directory that
    must live outside the repository. The layout mirrors the tenant-namespaced
    paths a cloud secret manager uses (``tenants/<tenant_id>/<name>``), so
    swapping in a real adapter is a change of class and nothing else.

    This is **not** a production secret manager: the values sit in plaintext on
    a local volume. That is acceptable where the credentials themselves are
    local-development placeholders, and ``build_secret_store`` refuses to select
    it anywhere else.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        # Best-effort on platforms without POSIX modes (Windows dev hosts).
        with_suppressed_oserror(lambda: self._root.chmod(_SECRET_DIR_MODE))

    def _path_for(self, ref: SecretRef) -> Path:
        # `path` is built from a UUID and a validated logical name, so it cannot
        # traverse; the resolve()/relative_to() check below makes that explicit
        # rather than assumed.
        candidate = (self._root / ref.path / f"{ref.version}.json").resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            msg = f"Refusing to resolve a secret path outside the store root: {ref.logical_name}"
            raise ConfigurationError(msg)
        return candidate

    async def put(self, tenant_id: uuid.UUID, logical_name: str, value: SecretValue) -> SecretRef:
        ref = SecretRef(tenant_id=tenant_id, logical_name=logical_name, version="1")
        self._write(ref, value)
        return ref

    def _write(self, ref: SecretRef, value: SecretValue) -> None:
        path = self._path_for(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        with_suppressed_oserror(lambda: path.parent.chmod(_SECRET_DIR_MODE))

        # Written with restrictive permissions from creation, not chmod'ed
        # afterwards: a window in which the file is world-readable is still an
        # exposure.
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _SECRET_FILE_MODE)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "value": value.reveal(),
                        "created_at": datetime.now(UTC).isoformat(),
                        "logical_name": ref.logical_name,
                        "version": ref.version,
                    },
                    handle,
                )
        except BaseException:
            with_suppressed_oserror(lambda: path.unlink(missing_ok=True))
            raise

        with_suppressed_oserror(lambda: path.chmod(_SECRET_FILE_MODE))

    async def get(self, ref: SecretRef, *, purpose: str) -> SecretValue:
        path = self._path_for(ref)
        if not path.exists():
            raise NotFoundError()

        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077 and os.name == "posix":
            msg = (
                f"Secret file for {ref.logical_name} is mode {mode:o}; it must not be "
                "readable by group or other."
            )
            raise ConfigurationError(msg)

        document = json.loads(path.read_text(encoding="utf-8"))
        _log.info("secret.read", logical_name=ref.logical_name, purpose=purpose)
        return SecretValue(str(document["value"]))

    async def rotate(self, ref: SecretRef, value: SecretValue) -> SecretRef:
        """Write a new version. The previous one is left in place.

        Overlapping versions are what make rotation safe for in-flight work: a
        connection opened moments before rotation keeps working until it is
        recycled (ADR-015 §3).
        """
        rotated = SecretRef(
            tenant_id=ref.tenant_id,
            logical_name=ref.logical_name,
            version=str(int(ref.version) + 1),
        )
        self._write(rotated, value)
        return rotated

    async def delete(self, ref: SecretRef) -> None:
        parent = self._path_for(ref).parent
        if parent.exists():
            for entry in parent.iterdir():
                entry.unlink(missing_ok=True)
            parent.rmdir()

    async def describe(self, ref: SecretRef) -> SecretMetadata:
        path = self._path_for(ref)
        if not path.exists():
            raise NotFoundError()
        document = json.loads(path.read_text(encoding="utf-8"))
        return SecretMetadata(ref=ref, created_at=datetime.fromisoformat(document["created_at"]))


def with_suppressed_oserror(action: object) -> None:
    """Run a filesystem permission call, ignoring platforms that lack it.

    ``chmod`` is meaningless on Windows development hosts. Failing there would
    block local development for no security benefit, while the mode check on
    read still enforces the invariant everywhere it applies.
    """
    with contextlib.suppress(OSError):  # pragma: no cover - platform dependent
        action()  # type: ignore[operator]


def build_secret_store(settings: Settings) -> SecretStore:
    """Select the secret store for this environment.

    Refuses in production-like environments rather than falling back to files.
    A plaintext-on-disk store that silently activated in production would be the
    same class of defect as the development token verifier that ADR-010's
    remediation removed.
    """
    if settings.is_production_like:
        msg = (
            f"No production SecretStore adapter is implemented, so environment "
            f"{settings.env.value!r} cannot start. Per-tenant analytical credentials "
            "require a real secret manager (ADR-015). Refusing to fall back to "
            "filesystem storage."
        )
        raise ConfigurationError(msg)

    root = Path(settings.secret_store_path)
    _log.info("secretstore.selected", backend="file", root=str(root))
    return FileSecretStore(root)
