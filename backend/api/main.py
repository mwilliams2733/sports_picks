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

    app.include_router(picks_router, prefix="/picks", tags=["picks"])
    app.include_router(stats_router, prefix="/stats", tags=["stats"])
    app.include_router(backtest_router, prefix="/backtest", tags=["backtest"])
    app.include_router(games_router, prefix="/games", tags=["games"])
    return app
