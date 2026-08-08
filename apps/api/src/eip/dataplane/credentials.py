"""Per-tenant analytical database credentials (ADR-003 §2, ADR-015).

This module exists to close finding G10. Before it, every tenant's analytical
data was reached with the one shared ``eip_app`` credential, which was a member
of every per-tenant role and could ``SET ROLE`` into any of them. That made
isolation depend on the application choosing the right role — a choice, not a
constraint.

Now each tenant has its **own login role and its own password**. There is no
membership and no ``SET ROLE``. A connection authenticated as tenant A's role
holds ``USAGE`` on exactly one schema, so naming tenant B is refused by
PostgreSQL no matter what the calling code intended.

The password is generated here, handed straight to the ``SecretStore``, and
never returned to the caller as a plain string. The tenant row stores a
``SecretRef`` — a pointer and a version — so a dump of the metadata database
contains no credential material at all (ADR-015).
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import URL, make_url

from eip.platform.errors import ConfigurationError
from eip.platform.logging import get_logger
from eip.platform.secrets import SecretRef, SecretStore, SecretValue

_log = get_logger("dataplane.credentials")

#: 32 bytes of entropy, URL-safe. Long enough that the password is never the
#: weakest part of the connection, short enough for any driver.
_PASSWORD_BYTES: Final = 32

#: The logical name under which a tenant's analytical password is stored. The
#: tenant id is already the namespace (``SecretRef.path``), so the name only has
#: to distinguish this secret from a tenant's future ones.
ANALYTICAL_SECRET_NAME: Final = "analytical-db-password"  # noqa: S105 - a logical name, not a value


@dataclass(frozen=True, slots=True)
class AnalyticalCredential:
    """A tenant's analytical login identity.

    Carries a ``SecretRef``, never a password. The value is fetched at the
    moment a pool is built and is not retained on this object, so a credential
    cannot be logged, serialised, or passed through a job payload by accident.
    """

    tenant_id: uuid.UUID
    role: str
    secret_ref: SecretRef


def generate_password() -> SecretValue:
    """Generate a database password.

    Returned as ``SecretValue`` rather than ``str`` so it cannot be formatted
    into a log line or an exception message on its way to the store.
    """
    return SecretValue(secrets.token_urlsafe(_PASSWORD_BYTES))


class AnalyticalCredentialProvider:
    """Builds tenant-scoped database URLs from stored credentials.

    The only component that turns a ``SecretRef`` into something connectable.
    It reads from the ``SecretStore`` at point of use, holds nothing, and
    produces a SQLAlchemy ``URL`` whose ``repr`` masks the password — so even a
    stray ``print(engine.url)`` discloses nothing.
    """

    def __init__(self, *, secret_store: SecretStore, template_dsn: str) -> None:
        self._secrets = secret_store
        # Host, port, and database are taken from the application DSN; only the
        # user and password differ per tenant. Parsing it once here means a
        # tenant URL cannot accidentally point at a different server.
        try:
            template = make_url(template_dsn)
        except Exception as exc:
            msg = "Could not parse the application DSN to derive tenant connection URLs."
            raise ConfigurationError(msg) from exc

        self._drivername = template.drivername
        self._host = template.host
        self._port = template.port
        self._database = template.database

    async def url_for(self, credential: AnalyticalCredential) -> URL:
        """Return a connectable URL for one tenant.

        ``URL.create`` keeps the password out of the object's string form:
        SQLAlchemy renders it as ``***``. That is the difference between a
        password that merely *should not* be logged and one that is not logged
        even when something goes wrong.
        """
        password = await self._secrets.get(
            credential.secret_ref,
            purpose=f"open analytical pool for tenant {credential.tenant_id}",
        )
        return URL.create(
            drivername=self._drivername,
            username=credential.role,
            password=password.reveal(),
            host=self._host,
            port=self._port,
            database=self._database,
        )

    async def store_new_password(self, tenant_id: uuid.UUID, password: SecretValue) -> SecretRef:
        """Persist a freshly generated password and return its reference."""
        ref = await self._secrets.put(tenant_id, ANALYTICAL_SECRET_NAME, password)
        _log.info(
            "credentials.stored",
            tenant_id=str(tenant_id),
            logical_name=ref.logical_name,
            version=ref.version,
        )
        return ref

    async def rotate_password(
        self, credential: AnalyticalCredential, password: SecretValue
    ) -> SecretRef:
        ref = await self._secrets.rotate(credential.secret_ref, password)
        _log.info(
            "credentials.rotated",
            tenant_id=str(credential.tenant_id),
            version=ref.version,
        )
        return ref

    async def forget(self, credential: AnalyticalCredential) -> None:
        await self._secrets.delete(credential.secret_ref)
        _log.warning("credentials.deleted", tenant_id=str(credential.tenant_id))
