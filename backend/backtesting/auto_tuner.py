"""Auto-tuner: grid-search strategy parameters and pick the best config."""
import json
import itertools
import logging
from datetime import date
from sqlalchemy.orm import Session

from backend.models import Game, StrategyModel
from backend.pipeline.pick_generator import STRATEGY_MAP, _build_game_data
from backend.backtesting.backtester import Backtester
from backend.backtesting.prop_backtester import PropBacktester

logger = logging.getLogger(__name__)

# ── Parameter grids per strategy name ──────────────────────

GAME_PARAM_GRIDS = {
    "ensemble": {
        "min_edge": [2.0, 3.0, 5.0, 7.0, 10.0],
        "weights_preset": [
            {"pd": 0.30, "elo": 0.35, "rating": 0.25, "hca": 0.10},
            {"pd": 0.25, "elo": 0.40, "rating": 0.25, "hca": 0.10},
            {"pd": 0.20, "elo": 0.30, "rating": 0.35, "hca": 0.15},
            {"pd": 0.35, "elo": 0.25, "rating": 0.30, "hca": 0.10},
        ],
    },
    "recent_form": {
        "min_edge": [3.0, 5.0, 7.0, 10.0],
        "lookback": [3, 5, 10],
        "recent_weight": [0.4, 0.5, 0.6, 0.7],
    },
    "value_only": {
        "min_edge": [5.0, 8.0, 10.0, 12.0, 15.0],
    },
    "sport_specific": {
        "min_edge": [3.0, 5.0, 7.0, 10.0],
    },
}

PROP_PARAM_GRID = {
    "min_edge": [3.0, 5.0, 7.0, 10.0],
    "recent_weight": [0.4, 0.5, 0.6, 0.7],
    "lookback": [3, 5, 7, 10],
    "min_minutes": [10, 15, 20],
}


def _expand_grid(grid: dict) -> list[dict]:
    """Expand a parameter grid into a list of config dicts."""
    keys = list(grid.keys())
    values = list(grid.values())
    configs = []
    for combo in itertools.product(*values):
        cfg = dict(zip(keys, combo))
        # Handle ensemble weights_preset -> weights
        if "weights_preset" in cfg:
            cfg["weights"] = cfg.pop("weights_preset")
        # Ensure recent_weight + pd_weight + venue_weight = 1.0 for recent_form
        if "recent_weight" in cfg and "weights" not in cfg and "season_weight" not in cfg:
            rw = cfg["recent_weight"]
            remaining = 1.0 - rw
            cfg["pd_weight"] = round(remaining * 0.5, 2)
            cfg["venue_weight"] = round(remaining * 0.5, 2)
        # For prop strategies, derive season_weight from recent_weight
        if "recent_weight" in cfg and "season_weight" not in cfg and "pd_weight" not in cfg:
            cfg["season_weight"] = round(1.0 - cfg["recent_weight"], 2)
        configs.append(cfg)
    return configs


def tune_game_strategy(
    session: Session,
    strategy_name: str,
    start_date: date,
    end_date: date,
    optimize_for: str = "roi",
) -> dict:
    """Grid-search game strategy parameters with walk-forward validation.

    Splits the date range: first 70% for training/tuning, last 30% for validation.
    Reports validation metrics to prevent look-ahead bias.
    """
    strategy_cls = STRATEGY_MAP.get(strategy_name)
    if not strategy_cls:
        return {"error": f"Unknown strategy: {strategy_name}"}

    grid = GAME_PARAM_GRIDS.get(strategy_name, {"min_edge": [3.0, 5.0, 7.0, 10.0]})
    configs = _expand_grid(grid)

    # Pre-load games once, ordered by date for temporal split
    games = session.query(Game).filter(
        Game.status == "final", Game.date >= start_date, Game.date <= end_date,
    ).order_by(Game.date).all()
    games_with_results = []
    for g in games:
        if g.home_score is not None and g.away_score is not None:
            game_data = _build_game_data(session, g)
            games_with_results.append((game_data, g.home_score, g.away_score))

    if not games_with_results:
        return {"error": "No completed games found in date range"}

    # Walk-forward split: 70% train, 30% validation
    split_idx = int(len(games_with_results) * 0.7)
    if split_idx < 10 or (len(games_with_results) - split_idx) < 5:
        train_games = games_with_results
        val_games = games_with_results
        walk_forward = False
    else:
        train_games = games_with_results[:split_idx]
        val_games = games_with_results[split_idx:]
        walk_forward = True

    logger.info(
        f"Auto-tuning {strategy_name}: {len(configs)} configs, "
        f"{len(train_games)} train / {len(val_games)} val games"
    )

    best_result = None
    best_config = None
    min_picks = 3
    all_results = []

    for cfg in configs:
        strategy = strategy_cls(strategy_name, cfg)
        bt = Backtester(strategy)
        train_result = bt.run(train_games)
        train_result.pop("picks", None)

        entry = {"config": cfg, "train": train_result}

        score = train_result.get(optimize_for, 0)
        min_picks = max(3, len(train_games) // 20)
        if train_result["total"] < min_picks:
            entry["validation"] = None
            all_results.append(entry)
            continue

        if walk_forward:
            val_result = bt.run(val_games)
            val_result.pop("picks", None)
            entry["validation"] = val_result
        else:
            entry["validation"] = train_result

        all_results.append(entry)

        if best_result is None or score > best_result.get("train", {}).get(optimize_for, 0):
            best_result = entry
            best_config = cfg

    return {
        "strategy_name": strategy_name,
        "walk_forward": walk_forward,
        "min_picks_required": min_picks,
        "train_games": len(train_games),
        "validation_games": len(val_games),
        "configs_tested": len(configs),
        "best_config": best_config,
        "best_result": best_result,
        "all_results": sorted(
            all_results,
            key=lambda r: (r.get("train") or {}).get(optimize_for, 0),
            reverse=True,
        ),
    }


def tune_prop_strategy(
    session: Session,
    sport: str,
    start_date: date,
    end_date: date,
    optimize_for: str = "roi",
) -> dict:
    """Grid-search prop backtester parameters and return the best config."""
    configs = _expand_grid(PROP_PARAM_GRID)

    logger.info(f"Auto-tuning prop strategy for {sport}: {len(configs)} configs")

    best_result = None
    best_config = None
    all_results = []

    for cfg in configs:
        bt = PropBacktester(cfg)
        result = bt.backtest(session, sport, start_date, end_date)
        result.pop("picks", None)

        entry = {"config": cfg, **result}
        all_results.append(entry)

        score = result.get(optimize_for, 0)
        if result["total"] < 5:
            continue
        if best_result is None or score > best_result.get(optimize_for, 0):
            best_result = result
            best_config = cfg

    return {
        "sport": sport,
        "configs_tested": len(configs),
        "best_config": best_config,
        "best_result": best_result,
        "all_results": sorted(all_results, key=lambda r: r.get(optimize_for, 0), reverse=True),
    }


def apply_tuned_config(session: Session, strategy_id: int, config: dict) -> bool:
    """Update a strategy's config_json with the tuned parameters."""
    strat = session.get(StrategyModel, strategy_id)
    if not strat:
        return False
    existing = json.loads(strat.config_json)
    existing.update(config)
    strat.config_json = json.dumps(existing)
    session.commit()
    return True
