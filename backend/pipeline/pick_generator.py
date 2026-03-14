import json
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from backend.models import Game, PickModel, StrategyModel, Odds, TeamStat, EloRating
from backend.data_types import GameData, TeamStats, OddsSnapshot
from backend.analysis.variants.ensemble import EnsembleStrategy

STRATEGY_MAP = {"ensemble": EnsembleStrategy}

def generate_and_store_picks(session: Session, strategy_id: int, target_date: date | None = None) -> int:
    target_date = target_date or date.today()
    strat_row = session.query(StrategyModel).get(strategy_id)
    if not strat_row: return 0
    config = json.loads(strat_row.config_json)
    strategy_cls = STRATEGY_MAP.get(strat_row.name)
    if not strategy_cls: return 0
    strategy = strategy_cls(strat_row.name, config)
    games = session.query(Game).filter(Game.date == target_date, Game.status == "scheduled").all()
    count = 0
    for game in games:
        game_data = _build_game_data(session, game)
        picks = strategy.predict(game_data)
        for pick in picks:
            if pick.confidence >= 1:
                db_pick = PickModel(game_id=game.id, strategy_id=strategy_id,
                    pick_type=pick.pick_type, pick_value=pick.pick_value,
                    confidence=pick.confidence, edge_pct=pick.edge_pct,
                    odds_at_pick=pick.odds_at_pick, created_at=datetime.now(tz=timezone.utc))
                session.add(db_pick)
                count += 1
    session.commit()
    return count

def _build_game_data(session: Session, game) -> GameData:
    home_stats = _get_team_stats(session, game.home_team_id, game.sport)
    away_stats = _get_team_stats(session, game.away_team_id, game.sport)
    odds_rows = session.query(Odds).filter(Odds.game_id == game.id).all()
    odds = [OddsSnapshot(bookmaker=o.bookmaker, moneyline_home=o.moneyline_home or 0,
        moneyline_away=o.moneyline_away or 0, spread_home=o.spread_home or 0.0,
        spread_away=o.spread_away or 0.0, over_under=o.over_under or 0.0) for o in odds_rows]
    return GameData(game_id=game.id, sport=game.sport, date=game.date,
        home_team_id=game.home_team_id, away_team_id=game.away_team_id,
        home_stats=home_stats, away_stats=away_stats, odds=odds, week=game.week)

def _get_team_stats(session: Session, team_id: int, sport: str) -> TeamStats:
    stats_rows = session.query(TeamStat).filter(TeamStat.team_id == team_id).all()
    stats_dict = {s.stat_type: s.value for s in stats_rows}
    elo_row = session.query(EloRating).filter(EloRating.team_id == team_id, EloRating.sport == sport).first()
    return TeamStats(point_diff=stats_dict.get("point_diff", 0.0),
        home_record=(int(stats_dict.get("home_wins", 0)), int(stats_dict.get("home_losses", 0))),
        away_record=(int(stats_dict.get("away_wins", 0)), int(stats_dict.get("away_losses", 0))),
        last_n_record=(int(stats_dict.get("last_n_wins", 0)), int(stats_dict.get("last_n_losses", 0))),
        offensive_rating=stats_dict.get("offensive_rating", 100.0),
        defensive_rating=stats_dict.get("defensive_rating", 100.0),
        pace=stats_dict.get("pace", 100.0), strength_of_schedule=stats_dict.get("sos", 0.5),
        elo_rating=elo_row.rating if elo_row else 1500.0, rest_days=int(stats_dict.get("rest_days", 2)),
        turnover_margin=stats_dict.get("turnover_margin"), red_zone_pct=stats_dict.get("red_zone_pct"),
        conference_strength=stats_dict.get("conference_strength"))
