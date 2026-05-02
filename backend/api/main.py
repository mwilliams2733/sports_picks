import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.database import get_engine, get_session
from backend.models import Base

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
    app = FastAPI(title="Sports Picks API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    engine = get_engine(db_path)
    from backend.database import migrate_api_usage, migrate_game_start_time, migrate_player_stat_receptions, migrate_elo_history, migrate_parlays, migrate_pick_result_line_at_close, migrate_pick_model_prob
    migrate_api_usage(engine)
    migrate_game_start_time(engine)
    migrate_player_stat_receptions(engine)
    migrate_elo_history(engine)
    migrate_parlays(engine)
    migrate_pick_result_line_at_close(engine)
    migrate_pick_model_prob(engine)
    Base.metadata.create_all(engine)

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
app = create_app(os.environ.get("DATABASE_PATH", "sports_picks.db"))
