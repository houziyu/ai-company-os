# syntax=docker/dockerfile:1.7
# Backend image shared by the api, worker and migrate services.
# Test dependencies are never installed here (see requirements.txt vs
# requirements-dev.txt).

FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq is needed by psycopg at runtime; build tools are not required because
# psycopg[binary] ships wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# Run as an unprivileged user. The image contains no secrets; they arrive as
# environment variables or mounted secret files at runtime.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Liveness uses the endpoint that never touches the database, so a database
# outage does not send the container into a restart loop. Readiness (which does
# require a migrated database) is the compose healthcheck.
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
