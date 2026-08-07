# API and worker image.
#
# Both services share one image: ADR-001 has the worker importing the api
# package rather than duplicating the domain, so a second image would only
# duplicate layers and create version skew between two things that must always
# agree.
#
# Build context is the repository root:
#   docker build -f infra/docker/api.Dockerfile .

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency manifests first, so the (slow) install layer is cached until a
# dependency actually changes.
COPY apps/api/pyproject.toml /app/apps/api/pyproject.toml
COPY apps/api/README.md* /app/apps/api/
COPY apps/worker/pyproject.toml /app/apps/worker/pyproject.toml

# A stub package satisfies the build backend while only manifests are present.
RUN mkdir -p /app/apps/api/src/eip /app/apps/worker/src/eip_worker \
 && touch /app/apps/api/src/eip/__init__.py /app/apps/worker/src/eip_worker/__init__.py \
 && pip install -e "/app/apps/api[postgres,dev]" \
 && pip install --no-deps -e /app/apps/worker \
 && pip install "dramatiq[redis]>=1.17"

COPY apps/api /app/apps/api
COPY apps/worker /app/apps/worker

# Run as a non-root user. A container that does not need root should not have
# it, and the application never writes to the filesystem — logs go to stdout
# (ADR-014 §1).
RUN useradd --create-home --uid 10001 eip \
 && chown -R eip:eip /app
USER eip

ENV PYTHONPATH=/app/apps/api/src:/app/apps/worker/src

# --- api -------------------------------------------------------------------
FROM base AS api
WORKDIR /app/apps/api
EXPOSE 8000
CMD ["python", "-m", "eip"]

# --- worker ----------------------------------------------------------------
FROM base AS worker
WORKDIR /app/apps/api
EXPOSE 8001
CMD ["python", "-m", "eip_worker"]

# --- migrations ------------------------------------------------------------
# A one-shot container. Runs as the migrator role; the runtime services have no
# DDL rights at all, so schema changes cannot happen outside this path
# (guardrail 17).
FROM base AS migrate
WORKDIR /app/apps/api
CMD ["alembic", "upgrade", "head"]
