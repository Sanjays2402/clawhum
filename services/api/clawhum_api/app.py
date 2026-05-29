from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from clawhum_core.logging import configure_logging, get_logger
from clawhum_core.telemetry import init_telemetry
from clawhum_core.version import __version__
from .state import AppState
from .routes import match as match_routes
from .routes import library as library_routes
from .routes import feedback as feedback_routes
from .routes import health as health_routes
from .routes import spotify as spotify_routes
from .middleware import RequestIDMiddleware, SimpleRateLimit


@asynccontextmanager
async def _lifespan(app: FastAPI):
    configure_logging()
    log = get_logger("clawhum.api")
    app.state.clawhum = AppState.boot(prefer_clap=False)  # default to fallback at startup; reindex can flip
    log.info("clawhum_boot", version=__version__,
             tracks=len(app.state.clawhum.tracks),
             vectors=app.state.clawhum.index.size())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="ClawHum", version=__version__, lifespan=_lifespan)
    app.add_middleware(SimpleRateLimit, max_per_minute=120)
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
    return app


app = create_app()

# Attach middleware after create_app returns? They are added via factory below.
