import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.database import get_engine, get_session

logger = logging.getLogger(__name__)

# CORS allow-list. Defaults to local dev origins; override via env var in
# production (comma-separated). The frontend bundle is served by this same
# FastAPI process in deployment, so cross-origin browser calls only happen
# in local dev (Vite on :5173 → uvicorn on :8000).
_DEFAULT_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _allowed_origins() -> list[str]:
    raw = os.environ.get("ALLOWED_ORIGINS")
    if not raw:
        return _DEFAULT_DEV_ORIGINS
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app(db_path: str = "sports_picks.db") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Start the APScheduler cron jobs (morning_scout 8/9/10am ET +
        # 3am recalibration) when ENABLE_SCHEDULER=1. Default off so the
        # test suite — which constructs hundreds of in-memory FastAPI
        # apps — doesn't accidentally spawn cron threads.
        scheduler = None
        if os.environ.get("ENABLE_SCHEDULER", "0") == "1":
            try:
                from backend.config import load_config
                from backend.pipeline.scheduler import configure_scheduler
                config_path = os.environ.get("CONFIG_PATH", "config.yaml")
                config = load_config(config_path)
                # Honor whatever DB path the app was created with — the
                # config.yaml default ("sports_picks.db") may be wrong in
                # deployment (where DATABASE_PATH=/data/sports_picks.db).
                config["database_path"] = db_path
                scheduler = configure_scheduler(config, app.state.engine)
                scheduler.start()
                app.state.scheduler = scheduler
                logger.info("APScheduler started inside FastAPI lifespan")
            except Exception:
                logger.exception("Failed to start scheduler — continuing without cron jobs")
                app.state.scheduler = None
        else:
            app.state.scheduler = None
        app.state.loop = asyncio.get_running_loop()
        try:
            yield
        finally:
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)

    app = FastAPI(title="Sports Picks API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    app.state.loop = None
    engine = get_engine(db_path)
    from backend.database import run_migrations
    run_migrations(engine)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.state.engine = engine

    from backend.api.picks import router as picks_router
    from backend.api.stats import router as stats_router
    from backend.api.backtest import router as backtest_router
    from backend.api.games import router as games_router
    from backend.api.props import router as props_router
    from backend.api.pipeline_api import router as pipeline_router
    from backend.api.credits import router as credits_router

    app.include_router(picks_router, prefix="/picks", tags=["picks"])
    app.include_router(stats_router, prefix="/stats", tags=["stats"])
    app.include_router(backtest_router, prefix="/backtest", tags=["backtest"])
    app.include_router(games_router, prefix="/games", tags=["games"])
    app.include_router(props_router, prefix="/props", tags=["props"])
    app.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])
    app.include_router(credits_router, prefix="/credits", tags=["credits"])

    from backend.api.users import router as users_router
    app.include_router(users_router, prefix="/users", tags=["users"])

    from backend.api.websocket import websocket_endpoint
    app.websocket("/ws")(websocket_endpoint)

    static_dir = os.path.join(os.path.dirname(__file__), "../../frontend/dist")
    if os.path.exists(static_dir):
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse

        # Serve static assets (JS, CSS, images)
        app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

        # Serve known static files at root
        for static_file in ["favicon.svg", "icons.svg", "manifest.json", "registerSW.js"]:
            file_path = os.path.join(static_dir, static_file)
            if os.path.exists(file_path):
                @app.get(f"/{static_file}", include_in_schema=False)
                def serve_static(f=file_path):
                    return FileResponse(f)

        # SPA catch-all: serve index.html for any unmatched route
        index_path = os.path.join(static_dir, "index.html")
        @app.get("/{path:path}", include_in_schema=False)
        def spa_catch_all(path: str):
            return FileResponse(index_path)

    return app

# DATABASE_PATH can be overridden in deployment (e.g. Render's /tmp on free
# tier, or a mounted disk on paid). Defaults to the project-root SQLite file
# used in local development.
#: The ASGI app, built on first access rather than at import.
_app: FastAPI | None = None


def __getattr__(name: str):
    """Construct the ASGI app only when something actually asks for it.

    ``uvicorn backend.api.main:app`` resolves the attribute, so every launch
    site keeps working unchanged (``Dockerfile:44``,
    ``deploy/sports-picks-web.service:11``, ``start.sh:14``). The two
    ``start-server*.bat`` launchers that used to be listed here were deleted
    on 2026-09-19: they pointed at a OneDrive path dead since July 2026 and
    at system Python rather than the venv.

    Importing any *other* name no longer touches the disk. It used to:
    ``create_app`` runs ``run_migrations``, Python executes a module body on
    first import, and every API test does
    ``from backend.api.main import create_app`` -- so ``pytest`` in the repo
    root migrated the real ``sports_picks.db``. Harmless while every migration
    is additive, but ``migrate_api_usage`` contains a ``DROP TABLE``.

    PEP 562. Only called for names not already defined in the module.
    """
    if name == "app":
        global _app
        if _app is None:
            _app = create_app(os.environ.get("DATABASE_PATH", "sports_picks.db"))
        return _app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
