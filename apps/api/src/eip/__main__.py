"""API entrypoint: ``python -m eip``."""

from __future__ import annotations

import uvicorn

from eip.platform.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "eip.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.env.allows_dev_auth,
        # The application emits its own structured access log with correlation
        # ids (ADR-014); uvicorn's would be a second, context-free copy.
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
