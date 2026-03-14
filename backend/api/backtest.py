import json
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

class StrategyUpdate(BaseModel):
    description: str | None = None
    config: dict | None = None

@router.get("/strategies")
def list_strategies(request: Request):
    session = get_session(request.app.state.engine)
    try:
        rows = session.query(StrategyModel).all()
        return [{"id": s.id, "name": s.name, "description": s.description,
            "config": json.loads(s.config_json), "is_active": s.is_active, "sport": s.sport} for s in rows]
    finally:
        session.close()

@router.post("/strategies", status_code=201)
def create_strategy(request: Request, body: StrategyCreate):
    session = get_session(request.app.state.engine)
    try:
        strat = StrategyModel(name=body.name, description=body.description,
            config_json=json.dumps(body.config), is_active=False, sport=body.sport)
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
        strat = session.query(StrategyModel).get(strategy_id)
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
        strat = session.query(StrategyModel).get(strategy_id)
        if not strat: raise HTTPException(status_code=404)
        session.query(StrategyModel).filter(StrategyModel.sport == strat.sport).update({"is_active": False})
        strat.is_active = True
        session.commit()
        return {"id": strat.id, "name": strat.name, "is_active": True}
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
