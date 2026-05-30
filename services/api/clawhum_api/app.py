from __future__ import annotations

from contextlib import asynccontextmanager

from clawhum_core.error_tracking import init_error_tracking
from clawhum_core.logging import configure_logging, get_logger
from clawhum_core.settings import get_settings
from clawhum_core.telemetry import init_telemetry
from clawhum_core.version import __version__
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .audit import AuditLogMiddleware
from .metrics import PrometheusMiddleware, register_app_collector
from .metrics import router as metrics_router
from .middleware import RequestIDMiddleware, SimpleRateLimit
from .routes import feedback as feedback_routes
from .routes import health as health_routes
from .routes import library as library_routes
from .routes import match as match_routes
from .routes import privacy as privacy_routes
from .routes import spotify as spotify_routes
from .state import AppState
from .tenant import TenantScopeMiddleware


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_logging()
    log = get_logger("clawhum.api")
    sentry_active = init_error_tracking()
    # Drop any cached registry so settings overrides applied via env in
    # tests or restarts take effect immediately on boot.
    from .api_keys import get_registry, reset_registry_cache
    reset_registry_cache()
    registry = get_registry()
    app.state.clawhum = AppState.boot(prefer_clap=False)  # default to fallback at startup; reindex can flip
    log.info("clawhum_boot", version=__version__,
             tracks=len(app.state.clawhum.tracks),
             vectors=app.state.clawhum.index.size(),
             sentry=sentry_active,
             api_keys=len(registry.by_secret),
             auth_open=registry.is_open())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="ClawHum", version=__version__, lifespan=_lifespan)
    settings = get_settings()
    # Audit runs innermost so it sees the final status code, but is added
    # before RequestID so request_id is already attached to request.state.
    app.add_middleware(AuditLogMiddleware, enabled=settings.audit_enabled)
    app.add_middleware(TenantScopeMiddleware)
    app.add_middleware(SimpleRateLimit, max_per_minute=settings.rate_limit_per_minute)
    # Prometheus middleware sits outside rate limiting so 429 responses
    # are still counted, but inside RequestID so the matched route is
    # resolved by the time we record the sample.
    app.add_middleware(PrometheusMiddleware)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )
    init_telemetry(app)
    app.include_router(health_routes.router)
    app.include_router(match_routes.router)
    app.include_router(library_routes.router)
    app.include_router(feedback_routes.router)
    app.include_router(spotify_routes.router)
    app.include_router(privacy_routes.router)
    app.include_router(metrics_router)
    register_app_collector(app)
    return app


app = create_app()

# Attach middleware after create_app returns? They are added via factory below.
