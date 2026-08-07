"""Alembic environment.

Runs as the ``eip_migrator`` role — the schema owner. The runtime role
(``eip_app``) has no DDL rights at all, so schema changes cannot happen outside
a migration (guardrail 17).

The DSN comes from ``EIP_DB_MIGRATOR_DSN``; ``alembic.ini`` deliberately holds
no credential.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Importing the registry registers every mapper on Base.metadata, which
# autogenerate needs in order to diff the full schema.
import eip.models  # noqa: F401
from eip.platform.db import Base
from eip.platform.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().db_migrator_dsn)

target_metadata = Base.metadata


def _include_object(
    obj: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    """Keep autogenerate focused on the control plane.

    Tenant analytical schemas are created by the provisioning subsystem
    (ADR-003 §2), not by Alembic, so they must never appear in a migration
    diff — otherwise the first autogenerate after a tenant is created would
    propose dropping that tenant's data.
    """
    if type_ == "table":
        schema = getattr(obj, "schema", None)
        if schema is not None and schema != "public":
            return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
        # One transaction per migration: a failed migration leaves no partial
        # schema behind.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
