from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.database import get_engine, get_session
from backend.models import Base

def create_app(db_path: str = "sports_picks.db") -> FastAPI:
    app = FastAPI(title="Sports Picks API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    engine = get_engine(db_path)
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

    app.include_router(picks_router, prefix="/picks", tags=["picks"])
    app.include_router(stats_router, prefix="/stats", tags=["stats"])
    app.include_router(backtest_router, prefix="/backtest", tags=["backtest"])
    app.include_router(games_router, prefix="/games", tags=["games"])
    app.include_router(props_router, prefix="/props", tags=["props"])
    app.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])

    from backend.api.users import router as users_router
    app.include_router(users_router, prefix="/users", tags=["users"])

    from backend.api.websocket import websocket_endpoint
    app.websocket("/ws")(websocket_endpoint)

    import os
    static_dir = os.path.join(os.path.dirname(__file__), "../../frontend/dist")
    if os.path.exists(static_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

    return app

app = create_app()
