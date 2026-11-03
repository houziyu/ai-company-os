"""FastAPI application factory.

The factory takes explicit dependencies so tests can inject an isolated database
without monkeypatching module globals.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import register_error_handlers
from app.api.middleware import OriginGuardMiddleware, RequestContextMiddleware
from app.api.v1 import audit, auth, health, model_centre, organization, tasks
from app.config import Settings
from app.db.session import build_engine, build_session_factory
from app.logging import configure_logging
from app.providers.adapter import ProbeTransport
from app.security.secrets_cipher import SecretCipher
from app.services.auth_service import AuthService
from app.services.budget_service import BudgetService
from app.services.event_feed_service import EventFeedService
from app.services.health_service import HealthService
from app.services.model_centre_service import ModelCentreService

BACKEND_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
    backend_root: Path | None = None,
    probe_transport: ProbeTransport | None = None,
) -> FastAPI:
    """Build the API application.

    ``probe_transport`` lets tests supply an in-memory transport for provider
    probes. Production passes nothing, so a deployment can only reach providers
    over the real network; there is no configuration switch that swaps in a
    simulator.
    """
    settings = settings or Settings()
    configure_logging(settings.log_level)

    owns_engine = engine is None
    engine = engine or build_engine(settings)
    session_factory: sessionmaker[Session] = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "api starting",
            extra={"version": settings.app_version, "environment": settings.app_env},
        )
        try:
            yield
        finally:
            if owns_engine:
                engine.dispose()
            logger.info("api stopped")

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=settings.app_version,
        docs_url="/api/docs" if settings.enable_api_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_api_docs else None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.auth_service = AuthService(settings)
    app.state.health_service = HealthService(
        settings, session_factory, backend_root or BACKEND_ROOT
    )
    # The probe service needs its own session factory: the intent row must be
    # committed before the provider call, independent of the request transaction
    # (review finding R2).
    app.state.model_centre_service = ModelCentreService(
        transport=probe_transport,
        session_factory=session_factory,
        default_company_budget=settings.company_default_budget_tokens,
        probe_reservation_tokens=settings.probe_reservation_tokens,
        # ADR-0006: provider credentials are encrypted at rest. A missing or
        # malformed data key fails at construction so a deployment cannot run
        # while silently unable to store (or read) a key.
        cipher=SecretCipher.from_setting(settings.secret_encryption_key),
    )
    # Shared budget service: the API reads and the worker writes the same ledger,
    # so the configured default allowance must come from one place.
    app.state.budget_service = BudgetService(
        default_company_limit=settings.company_default_budget_tokens
    )
    # The SSE feed publishes events in commit order; one service instance is
    # enough because the ordering guarantee lives in the database lock.
    app.state.event_feed_service = EventFeedService()

    # CORS exists only for the Vite dev server; the production web container
    # serves the SPA same-origin, so no preflight path exists there.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(OriginGuardMiddleware, settings=settings)  # type: ignore[arg-type]
    app.add_middleware(RequestContextMiddleware)

    register_error_handlers(app)

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(organization.router, prefix=API_PREFIX)
    app.include_router(model_centre.router, prefix=API_PREFIX)
    app.include_router(audit.router, prefix=API_PREFIX)
    app.include_router(tasks.router, prefix=API_PREFIX)

    @app.get("/api/v1/meta", tags=["health"])
    def meta() -> dict[str, object]:
        """Public, non-secret metadata for the login page footer."""
        return {
            "appName": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env,
        }

    return app
