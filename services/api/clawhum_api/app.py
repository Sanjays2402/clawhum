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
from .body_size_middleware import BodySizeMiddleware
from .budget_middleware import BudgetMiddleware
from .idempotency import IdempotencyMiddleware, build_store, register as _register_idem_store
from .metrics import PrometheusMiddleware, register_app_collector
from .metrics import router as metrics_router
from .middleware import (
    PatExpiryWarningMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    SimpleRateLimit,
)
from .residency import ResidencyMiddleware
from .routes import activity as activity_routes
from .routes import audit as audit_routes
from .routes import audit_forwarder as audit_forwarder_routes
from .routes import batch as batch_routes
from .routes import collections as collections_routes
from .routes import embed_origins as embed_origins_routes
from .routes import security_contacts as security_contacts_routes
from .routes import support_access as support_access_routes
from .routes import legal_hold as legal_hold_routes
from .routes import closure as closure_routes
from .routes import feedback as feedback_routes
from .routes import health as health_routes
from .routes import history as history_routes
from .routes import history_views as history_views_routes
from .routes import ip_allowlist as ip_allowlist_routes
from .routes import trusted_proxies as trusted_proxies_routes
from .routes import invite_domains as invite_domains_routes
from .routes import scope_policy as scope_policy_routes
from .routes import auth_methods_policy as auth_methods_policy_routes
from .routes import pat_auth_lockout as pat_auth_lockout_routes
from .routes import export_signing as export_signing_routes
from .routes import pat_concurrency as pat_concurrency_routes
from .routes import pat_secret_prefix as pat_secret_prefix_routes
from .routes import pat_expiry_warning as pat_expiry_warning_routes
from .routes import webhook_destination_cap as webhook_destination_cap_routes
from .routes import webhook_policy as webhook_policy_routes
from .routes import webhook_delivery_rate as webhook_delivery_rate_routes
from .routes import body_size as body_size_routes
from .routes import dpa as dpa_routes
from .routes import dsar as dsar_routes
from .routes import incidents as incidents_routes
from .routes import keys as keys_routes
from .routes import library as library_routes
from .routes import match as match_routes
from .routes import me as me_routes
from .routes import members as members_routes
from .routes import mfa as mfa_routes
from .routes import pitch as pitch_routes
from .routes import privacy as privacy_routes
from .routes import quotas as quotas_routes
from .routes import budget as budget_routes
from .routes import residency as residency_routes
from .routes import retention as retention_routes
from .routes import scim as scim_routes
from .routes import scim_admin as scim_admin_routes
from .routes import sessions as sessions_routes
from .routes import share as share_routes
from .routes import spotify as spotify_routes
from .routes import sso as sso_routes
from .routes import seat_limit as seat_limit_routes
from .routes import usage as usage_routes
from .routes import webhooks as webhooks_routes
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
    from . import trusted_proxies as _trusted_proxies
    _trusted_proxies.reset_cache()
    from . import security_contacts as _security_contacts
    _security_contacts.reset_cache()
    from . import support_access as _support_access
    _support_access.reset_cache()
    from . import closure as _workspace_closure
    _workspace_closure.reset_cache()
    from . import scope_policy as _scope_policy
    _scope_policy.reset_cache()
    from . import auth_methods_policy as _auth_methods_policy
    _auth_methods_policy.reset_cache()
    from . import pat_concurrency as _pat_concurrency
    _pat_concurrency.reset_cache()
    from . import pat_secret_prefix as _pat_secret_prefix
    _pat_secret_prefix.reset_cache()
    from . import pat_expiry_warning as _pat_expiry_warning
    _pat_expiry_warning.reset_cache()
    from . import webhook_destination_cap as _webhook_destination_cap
    _webhook_destination_cap.reset_cache()
    from . import webhook_policy as _webhook_policy
    _webhook_policy.reset_cache()
    from . import webhook_delivery_rate as _webhook_delivery_rate
    _webhook_delivery_rate.reset_cache()
    from . import body_size as _body_size
    _body_size.reset_cache()
    from . import sso_store as _sso_store
    _sso_store.reset_cache()
    from . import quota_store as _quota_store
    _quota_store.reset_cache()
    from . import budget_store as _budget_store
    _budget_store.reset_cache()
    from .usage import reset_month_cache as _reset_month_cache
    _reset_month_cache()
    from . import residency_store as _residency_store
    _residency_store.reset_cache()
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
    # PAT expiry warning runs as close to the response edge as the
    # audit middleware so it sees the final response object and can
    # set Sunset/Deprecation headers on every PAT-authenticated
    # response, including 4xx and 5xx, without changing status codes.
    app.add_middleware(PatExpiryWarningMiddleware)
    app.add_middleware(UsageRecorderMiddleware)
    # Budget enforcement runs after Usage records the event so a request
    # blocked at the cap is never charged, and inside TenantScope so
    # request.state.tenant_id is resolved before the cap lookup.
    app.add_middleware(BudgetMiddleware)
    # Body size cap runs alongside the budget so a request rejected for
    # size is never billed; it also resolves the tenant from the API
    # key header directly so the cap applies before any route runs.
    app.add_middleware(BodySizeMiddleware)
    app.add_middleware(TenantScopeMiddleware)
    app.add_middleware(SimpleRateLimit, max_per_minute=settings.rate_limit_per_minute)
    # Idempotency-Key replay cache sits outside the rate limiter so a
    # cached replay does not double-charge the workspace quota, and
    # inside RequestID so replays still carry the original request id.
    _idem_store = build_store(settings)
    _register_idem_store(_idem_store)
    app.add_middleware(
        IdempotencyMiddleware,
        enabled=settings.idempotency_enabled,
        store=_idem_store,
    )
    # Residency runs outside the rate limiter so a 451 response is not
    # also counted against the workspace quota; it sits inside RequestID
    # so structured logs still carry the request id for the rejection.
    app.add_middleware(
        ResidencyMiddleware,
        node_region=settings.region,
        enforcement=settings.residency_enforcement,
    )
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
        expose_headers=["X-Request-ID", "traceparent", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "X-RateLimit-Scope", "X-RateLimit-Plan", "X-RateLimit-Limit-Day", "X-RateLimit-Remaining-Day", "X-Data-Region", "X-Workspace-Region", "Retry-After", "Idempotent-Replayed", "X-Original-Request-ID", "X-Budget-Limit", "X-Budget-Used", "X-Budget-Remaining", "X-Budget-Status", "X-Budget-Enforcement", "Sunset", "Deprecation", "X-Clawhum-Token-Expires-In", "X-Clawhum-Token-Expires-At", "Link"],
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
    app.include_router(trusted_proxies_routes.router)
    app.include_router(invite_domains_routes.router)
    app.include_router(scope_policy_routes.router)
    app.include_router(auth_methods_policy_routes.router)
    app.include_router(pat_auth_lockout_routes.router)
    app.include_router(pat_concurrency_routes.router)
    app.include_router(pat_secret_prefix_routes.router)
    app.include_router(pat_expiry_warning_routes.router)
    app.include_router(webhook_destination_cap_routes.router)
    app.include_router(webhook_policy_routes.router)
    app.include_router(webhook_delivery_rate_routes.router)
    app.include_router(body_size_routes.router)
    app.include_router(body_size_routes.router, prefix="/v1")
    app.include_router(dpa_routes.router)
    app.include_router(dsar_routes.router)
    app.include_router(incidents_routes.router)
    app.include_router(mfa_routes.router)
    app.include_router(members_routes.router)
    app.include_router(sso_routes.router)
    app.include_router(retention_routes.router)
    app.include_router(audit_routes.router)
    app.include_router(audit_forwarder_routes.router)
    app.include_router(quotas_routes.router)
    app.include_router(budget_routes.router)
    app.include_router(residency_routes.router)
    app.include_router(scim_routes.router)
    app.include_router(scim_admin_routes.router)
    app.include_router(sessions_routes.router)
    app.include_router(seat_limit_routes.router)
    app.include_router(embed_origins_routes.router)
    app.include_router(security_contacts_routes.router)
    app.include_router(support_access_routes.router)
    app.include_router(legal_hold_routes.router)
    app.include_router(closure_routes.router)
    app.include_router(export_signing_routes.router)
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
    app.include_router(trusted_proxies_routes.router, prefix="/v1")
    app.include_router(invite_domains_routes.router, prefix="/v1")
    app.include_router(scope_policy_routes.router, prefix="/v1")
    app.include_router(auth_methods_policy_routes.router, prefix="/v1")
    app.include_router(pat_concurrency_routes.router, prefix="/v1")
    app.include_router(pat_secret_prefix_routes.router, prefix="/v1")
    app.include_router(pat_expiry_warning_routes.router, prefix="/v1")
    app.include_router(webhook_destination_cap_routes.router, prefix="/v1")
    app.include_router(webhook_policy_routes.router, prefix="/v1")
    app.include_router(dpa_routes.router, prefix="/v1")
    app.include_router(dsar_routes.router, prefix="/v1")
    app.include_router(incidents_routes.router, prefix="/v1")
    app.include_router(mfa_routes.router, prefix="/v1")
    app.include_router(members_routes.router, prefix="/v1")
    app.include_router(sso_routes.router, prefix="/v1")
    app.include_router(retention_routes.router, prefix="/v1")
    app.include_router(audit_routes.router, prefix="/v1")
    app.include_router(audit_forwarder_routes.router, prefix="/v1")
    app.include_router(export_signing_routes.router, prefix="/v1")
    app.include_router(quotas_routes.router, prefix="/v1")
    app.include_router(budget_routes.router, prefix="/v1")
    app.include_router(residency_routes.router, prefix="/v1")
    app.include_router(library_routes.router, prefix="/v1")
    app.include_router(scim_routes.router, prefix="/v1")
    app.include_router(scim_admin_routes.router, prefix="/v1")
    app.include_router(sessions_routes.router, prefix="/v1")
    app.include_router(seat_limit_routes.router, prefix="/v1")
    app.include_router(embed_origins_routes.router, prefix="/v1")
    app.include_router(security_contacts_routes.router, prefix="/v1")
    app.include_router(support_access_routes.router, prefix="/v1")
    app.include_router(legal_hold_routes.router, prefix="/v1")
    app.include_router(closure_routes.router, prefix="/v1")
    app.include_router(metrics_router)
    register_app_collector(app)
    return app


app = create_app()

# Attach middleware after create_app returns? They are added via factory below.
