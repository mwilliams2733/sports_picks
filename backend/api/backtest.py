import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from backend.database import get_session
from backend.models import StrategyModel, BacktestRun, BacktestPick

router = APIRouter()

class StrategyCreate(BaseModel):
    name: str
    description: str = ""
    config: dict
    sport: str | None = None
    strategy_type: str = "game"

class StrategyUpdate(BaseModel):
    description: str | None = None
    config: dict | None = None

@router.get("/strategies")
def list_strategies(request: Request):
    session = get_session(request.app.state.engine)
    try:
        rows = session.query(StrategyModel).all()
        return [{"id": s.id, "name": s.name, "description": s.description,
            "config": json.loads(s.config_json), "is_active": s.is_active, "sport": s.sport,
            "strategy_type": s.strategy_type} for s in rows]
    finally:
        session.close()

@router.post("/strategies", status_code=201)
def create_strategy(request: Request, body: StrategyCreate):
    session = get_session(request.app.state.engine)
    try:
        strat = StrategyModel(name=body.name, description=body.description,
            config_json=json.dumps(body.config), is_active=False, sport=body.sport,
            strategy_type=body.strategy_type)
        session.add(strat)
        session.commit()
        session.refresh(strat)
        return {"id": strat.id, "name": strat.name, "description": strat.description,
                "config": body.config, "is_active": strat.is_active, "sport": strat.sport}
    finally:
        session.close()

@router.put("/strategies/{strategy_id}")
def update_strategy(request: Request, strategy_id: int, body: StrategyUpdate):
    session = get_session(request.app.state.engine)
    try:
        strat = session.get(StrategyModel, strategy_id)
        if not strat: raise HTTPException(status_code=404)
        if body.description is not None: strat.description = body.description
        if body.config is not None: strat.config_json = json.dumps(body.config)
        session.commit()
        return {"id": strat.id, "name": strat.name, "updated": True}
    finally:
        session.close()

@router.patch("/strategies/{strategy_id}/promote")
def promote_strategy(request: Request, strategy_id: int):
    session = get_session(request.app.state.engine)
    try:
        strat = session.get(StrategyModel, strategy_id)
        if not strat: raise HTTPException(status_code=404)
        session.query(StrategyModel).filter(StrategyModel.sport == strat.sport).update({"is_active": False})
        strat.is_active = True
        session.commit()
        return {"id": strat.id, "name": strat.name, "is_active": True}
    finally:
        session.close()

class BacktestRunRequest(BaseModel):
    strategy_id: int
    start_date: str
    end_date: str

@router.post("/run")
def run_backtest(request: Request, body: BacktestRunRequest):
    from datetime import date as date_type
    session = get_session(request.app.state.engine)
    try:
        strat = session.get(StrategyModel, body.strategy_id)
        if not strat:
            raise HTTPException(status_code=404, detail="Strategy not found")
        start = date_type.fromisoformat(body.start_date)
        end = date_type.fromisoformat(body.end_date)
        if strat.strategy_type == "prop":
            from backend.backtesting.prop_backtester import PropBacktester
            config = json.loads(strat.config_json)
            bt = PropBacktester(config)
            sport = strat.sport or "nba"
            result = bt.backtest(session, sport, start, end)
        else:
            from backend.backtesting.backtester import Backtester
            from backend.pipeline.pick_generator import STRATEGY_MAP, _build_game_data
            config = json.loads(strat.config_json)
            strategy_cls = STRATEGY_MAP.get(strat.name)
            if not strategy_cls:
                raise HTTPException(status_code=400, detail="Unknown strategy")
            strategy = strategy_cls(strat.name, config)
            bt = Backtester(strategy)
            from backend.models import Game
            games = session.query(Game).filter(
                Game.status == "final", Game.date >= start, Game.date <= end).all()
            games_with_results = []
            for g in games:
                if g.home_score is not None and g.away_score is not None:
                    game_data = _build_game_data(session, g)
                    games_with_results.append((game_data, g.home_score, g.away_score))
            result = bt.run(games_with_results)
        run = BacktestRun(strategy_id=strat.id, status="completed",
            started_at=datetime.now(tz=timezone.utc),
            completed_at=datetime.now(tz=timezone.utc))
        session.add(run)
        session.commit()
        return result
    finally:
        session.close()

@router.get("/compare")
def compare_strategies(request: Request):
    session = get_session(request.app.state.engine)
    try:
        strategies = session.query(StrategyModel).all()
        results = []
        for strat in strategies:
            runs = session.query(BacktestRun).filter(BacktestRun.strategy_id == strat.id, BacktestRun.status == "completed").all()
            for run in runs:
                picks = session.query(BacktestPick).filter(BacktestPick.run_id == run.id).all()
                wins = sum(1 for p in picks if p.result == "win")
                losses = sum(1 for p in picks if p.result == "loss")
                total = wins + losses
                results.append({"strategy_id": strat.id, "strategy_name": strat.name,
                    "run_id": run.id, "wins": wins, "losses": losses, "total": total,
                    "win_rate": round(wins / total * 100, 2) if total > 0 else 0})
        return results
    finally:
        session.close()
