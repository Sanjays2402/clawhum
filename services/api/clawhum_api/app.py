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
from .middleware import RequestIDMiddleware, SecurityHeadersMiddleware, SimpleRateLimit
from .routes import batch as batch_routes
from .routes import feedback as feedback_routes
from .routes import health as health_routes
from .routes import library as library_routes
from .routes import match as match_routes
from .routes import me as me_routes
from .routes import pitch as pitch_routes
from .routes import privacy as privacy_routes
from .routes import history as history_routes
from .routes import history_views as history_views_routes
from .routes import share as share_routes
from .routes import spotify as spotify_routes
from .routes import usage as usage_routes
from .routes import webhooks as webhooks_routes
from .routes import activity as activity_routes
from .routes import collections as collections_routes
from .routes import keys as keys_routes
from .routes import ip_allowlist as ip_allowlist_routes
from .routes import mfa as mfa_routes
from .routes import members as members_routes
from .routes import sso as sso_routes
from .routes import retention as retention_routes
from .routes import audit as audit_routes
from .state import AppState
from .tenant import TenantScopeMiddleware
from .usage import UsageRecorderMiddleware


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
    from . import ip_allowlist as _ip_allowlist
    _ip_allowlist.reset_cache()
    from . import sso_store as _sso_store
    _sso_store.reset_cache()
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
    app.add_middleware(UsageRecorderMiddleware)
    app.add_middleware(TenantScopeMiddleware)
    app.add_middleware(SimpleRateLimit, max_per_minute=settings.rate_limit_per_minute)
    # Prometheus middleware sits outside rate limiting so 429 responses
    # are still counted, but inside RequestID so the matched route is
    # resolved by the time we record the sample.
    app.add_middleware(PrometheusMiddleware)
    app.add_middleware(RequestIDMiddleware)
    cors_origins = settings.cors_origins_list()
    cors_allow_credentials = settings.cors_allow_credentials and cors_origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=settings.cors_methods_list(),
        allow_headers=settings.cors_headers_list(),
        allow_credentials=cors_allow_credentials,
        expose_headers=["X-Request-ID", "traceparent", "X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After"],
        max_age=600,
    )
    # Security headers run outermost so they apply to every response,
    # including CORS preflights and rate-limit 429s. Browsers honor
    # HSTS only on HTTPS responses; the middleware checks the request
    # scheme (or X-Forwarded-Proto) before setting the header so local
    # http development is unaffected.
    app.add_middleware(
        SecurityHeadersMiddleware,
        enabled=settings.security_headers_enabled,
        hsts_max_age=settings.security_hsts_max_age,
        hsts_include_subdomains=settings.security_hsts_include_subdomains,
        hsts_preload=settings.security_hsts_preload,
        csp=settings.security_csp,
        referrer_policy=settings.security_referrer_policy,
        permissions_policy=settings.security_permissions_policy,
    )
    init_telemetry(app)
    app.include_router(health_routes.router)
    app.include_router(match_routes.router)
    app.include_router(batch_routes.router)
    app.include_router(library_routes.router)
    app.include_router(pitch_routes.router)
    app.include_router(feedback_routes.router)
    app.include_router(spotify_routes.router)
    app.include_router(privacy_routes.router)
    app.include_router(share_routes.router)
    app.include_router(history_views_routes.router)
    app.include_router(history_routes.router)
    app.include_router(me_routes.router)
    app.include_router(usage_routes.router)
    app.include_router(webhooks_routes.router)
    app.include_router(activity_routes.router)
    app.include_router(collections_routes.router)
    app.include_router(keys_routes.router)
    app.include_router(ip_allowlist_routes.router)
    app.include_router(mfa_routes.router)
    app.include_router(members_routes.router)
    app.include_router(sso_routes.router)
    app.include_router(retention_routes.router)
    app.include_router(audit_routes.router)
    # Stable, version-pinned public API surface. The same routers are
    # mounted again under /v1 so integrators can target a URL we will not
    # break, while existing unversioned routes stay alive for the web UI.
    app.include_router(match_routes.router, prefix="/v1")
    app.include_router(batch_routes.router, prefix="/v1")
    app.include_router(share_routes.router, prefix="/v1")
    app.include_router(collections_routes.router, prefix="/v1")
    app.include_router(history_views_routes.router, prefix="/v1")
    app.include_router(history_routes.router, prefix="/v1")
    app.include_router(me_routes.router, prefix="/v1")
    app.include_router(usage_routes.router, prefix="/v1")
    app.include_router(webhooks_routes.router, prefix="/v1")
    app.include_router(keys_routes.router, prefix="/v1")
    app.include_router(ip_allowlist_routes.router, prefix="/v1")
    app.include_router(mfa_routes.router, prefix="/v1")
    app.include_router(members_routes.router, prefix="/v1")
    app.include_router(sso_routes.router, prefix="/v1")
    app.include_router(retention_routes.router, prefix="/v1")
    app.include_router(audit_routes.router, prefix="/v1")
    app.include_router(library_routes.router, prefix="/v1")
    app.include_router(metrics_router)
    register_app_collector(app)
    return app


app = create_app()

# Attach middleware after create_app returns? They are added via factory below.
