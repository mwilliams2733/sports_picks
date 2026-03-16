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

class AutoTuneRequest(BaseModel):
    strategy_id: int
    start_date: str
    end_date: str
    optimize_for: str = "roi"
    apply_best: bool = False


@router.post("/auto-tune")
def auto_tune(request: Request, body: AutoTuneRequest):
    """Grid-search strategy parameters and optionally apply the best config."""
    from datetime import date as date_type
    from backend.backtesting.auto_tuner import (
        tune_game_strategy, tune_prop_strategy, apply_tuned_config,
    )
    session = get_session(request.app.state.engine)
    try:
        strat = session.get(StrategyModel, body.strategy_id)
        if not strat:
            raise HTTPException(status_code=404, detail="Strategy not found")
        start = date_type.fromisoformat(body.start_date)
        end = date_type.fromisoformat(body.end_date)

        if strat.strategy_type == "prop":
            sport = strat.sport or "nba"
            result = tune_prop_strategy(session, sport, start, end, body.optimize_for)
        else:
            result = tune_game_strategy(session, strat.name, start, end, body.optimize_for)

        # Trim all_results to top 10 for response size
        if "all_results" in result:
            result["all_results"] = result["all_results"][:10]

        if body.apply_best and result.get("best_config"):
            apply_tuned_config(session, strat.id, result["best_config"])
            result["applied"] = True
            result["strategy_id"] = strat.id
        else:
            result["applied"] = False

        return result
    finally:
        session.close()


class RunAllRequest(BaseModel):
    sport: str = "nba"
    start_date: str = "2026-02-01"
    end_date: str = "2026-03-14"


# Sport -> prop market mapping
SPORT_MARKETS = {
    "nba": [
        "player_points", "player_rebounds", "player_assists", "player_threes",
        "player_blocks", "player_steals", "player_turnovers",
        "player_points_rebounds_assists", "player_points_rebounds",
        "player_points_assists", "player_rebounds_assists",
    ],
    "ncaab": [
        "player_points", "player_rebounds", "player_assists", "player_threes",
        "player_blocks", "player_steals", "player_turnovers",
    ],
    "nfl": [
        "player_pass_yds", "player_rush_yds", "player_reception_yds",
        "player_pass_tds", "player_anytime_td", "player_receptions",
    ],
    "ncaaf": [
        "player_pass_yds", "player_rush_yds", "player_reception_yds",
        "player_pass_tds", "player_anytime_td",
    ],
}

MARKET_LABELS = {
    "player_points": "Points", "player_rebounds": "Rebounds",
    "player_assists": "Assists", "player_threes": "3-Pointers",
    "player_blocks": "Blocks", "player_steals": "Steals",
    "player_turnovers": "Turnovers",
    "player_points_rebounds_assists": "Pts+Reb+Ast",
    "player_points_rebounds": "Pts+Reb", "player_points_assists": "Pts+Ast",
    "player_rebounds_assists": "Reb+Ast",
    "player_pass_yds": "Pass Yards", "player_rush_yds": "Rush Yards",
    "player_reception_yds": "Rec Yards", "player_pass_tds": "Pass TDs",
    "player_anytime_td": "Anytime TD", "player_receptions": "Receptions",
}


@router.post("/run-all")
def run_all_backtests(request: Request, body: RunAllRequest):
    """Run all strategy variants for a given sport and date range."""
    from datetime import date as date_type
    from backend.backtesting.backtester import Backtester
    from backend.backtesting.prop_backtester import PropBacktester
    from backend.pipeline.pick_generator import STRATEGY_MAP, _build_game_data
    from backend.models import Game

    session = get_session(request.app.state.engine)
    try:
        start = date_type.fromisoformat(body.start_date)
        end = date_type.fromisoformat(body.end_date)

        # Load all final games for the date range and sport
        games = session.query(Game).filter(
            Game.status == "final",
            Game.date >= start,
            Game.date <= end,
            Game.sport == body.sport,
        ).all()

        games_with_results = []
        for g in games:
            if g.home_score is not None and g.away_score is not None:
                game_data = _build_game_data(session, g)
                games_with_results.append((game_data, g.home_score, g.away_score))

        results = {}

        # Run each game strategy
        game_variants = ["ensemble", "recent_form", "value_only", "sport_specific"]
        for variant_name in game_variants:
            strategy_cls = STRATEGY_MAP.get(variant_name)
            if not strategy_cls:
                continue
            # Use default config for each
            default_configs = {
                "ensemble": {"min_edge": 3.0, "weights": {"pd": 0.3, "elo": 0.35, "rating": 0.25, "hca": 0.1}},
                "recent_form": {"min_edge": 5.0, "lookback": 10, "recent_weight": 0.6},
                "value_only": {"min_edge": 10.0},
                "sport_specific": {"min_edge": 5.0},
            }
            config = default_configs.get(variant_name, {})

            # Check if there's a saved strategy with tuned config
            saved = session.query(StrategyModel).filter(
                StrategyModel.name == variant_name,
            ).first()
            if saved:
                config = json.loads(saved.config_json)

            strategy = strategy_cls(variant_name, config)
            bt = Backtester(strategy)
            result = bt.run(games_with_results)
            results[variant_name] = result

        # Run prop backtester
        prop_config = {"recent_weight": 0.6, "season_weight": 0.4, "min_edge": 5.0, "lookback": 5, "min_minutes": 15}
        saved_prop = session.query(StrategyModel).filter(
            StrategyModel.strategy_type == "prop",
        ).first()
        if saved_prop:
            prop_config = json.loads(saved_prop.config_json)
        bt = PropBacktester(prop_config)
        prop_result = bt.backtest(session, body.sport, start, end)
        results["prop_value"] = prop_result

        # Add market info for the sport
        sport_markets = SPORT_MARKETS.get(body.sport, [])
        market_info = [{"key": k, "label": MARKET_LABELS.get(k, k)} for k in sport_markets]

        return {
            "sport": body.sport,
            "start_date": body.start_date,
            "end_date": body.end_date,
            "games_count": len(games_with_results),
            "variants": results,
            "sport_markets": market_info,
        }
    finally:
        session.close()


@router.get("/sport-markets")
def get_sport_markets(sport: str | None = None):
    """Return prop markets filtered by sport."""
    if sport and sport in SPORT_MARKETS:
        markets = SPORT_MARKETS[sport]
    else:
        markets = list(MARKET_LABELS.keys())
    return [{"key": k, "label": MARKET_LABELS.get(k, k)} for k in markets]


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
