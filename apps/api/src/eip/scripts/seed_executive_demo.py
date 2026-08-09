"""Seed/reset the approved demo against an explicitly selected healthy source."""

from __future__ import annotations

import argparse
import asyncio
import uuid

from eip.intelligence.seed import seed_demo
from eip.platform.db import create_engines, create_session_factory
from eip.platform.settings import get_settings


async def run(tenant_id: uuid.UUID, source_id: uuid.UUID, author_id: uuid.UUID) -> None:
    settings = get_settings()
    if not settings.env.allows_dev_auth:
        raise SystemExit("demo seeding is permitted only in local and CI environments")
    engines = create_engines(settings)
    try:
        sessions = create_session_factory(engines.platform)
        async with sessions() as session, session.begin():
            await seed_demo(session, tenant_id, author_id, source_id)
    finally:
        await engines.app.dispose()
        await engines.platform.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", type=uuid.UUID, required=True)
    parser.add_argument("--source-id", type=uuid.UUID, required=True)
    parser.add_argument("--author-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.tenant_id, args.source_id, args.author_id))


if __name__ == "__main__":
    main()
