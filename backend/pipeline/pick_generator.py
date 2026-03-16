import json
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.orm import Session
from backend.models import Game, PickModel, StrategyModel, Odds, TeamStat, EloRating
from backend.data_types import GameData, TeamStats, OddsSnapshot
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.analysis.variants.recent_form import RecentFormStrategy
from backend.analysis.variants.value_only import ValueOnlyStrategy
from backend.analysis.variants.sport_specific import SportSpecificStrategy
from backend.analysis.variants.prop_value import PropValueStrategy

STRATEGY_MAP = {
    "ensemble": EnsembleStrategy,
    "recent_form": RecentFormStrategy,
    "value_only": ValueOnlyStrategy,
    "sport_specific": SportSpecificStrategy,
    "prop_value": PropValueStrategy,
}

def generate_and_store_picks(session: Session, strategy_id: int, target_date: date | None = None) -> int:
    target_date = target_date or date.today()
    strat_row = session.get(StrategyModel, strategy_id)
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

def _check_schedule_fatigue(session: Session, team_id: int, game_date: date, sport: str) -> tuple[bool, float]:
    """Check if team is playing 3rd game in 4 nights."""
    if sport not in ("nba", "ncaab"):
        return False, 0.0

    four_days_ago = game_date - timedelta(days=3)
    recent_games = session.query(Game).filter(
        Game.date >= four_days_ago,
        Game.date < game_date,
        ((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
    ).count()

    if recent_games >= 2:  # 2 games in last 3 days + today = 3 in 4
        severity = min(recent_games / 3, 1.0)  # Scale severity
        return True, severity
    return False, 0.0


def _check_lookahead_spot(session: Session, team_id: int, opponent_team_id: int,
                          game_date: date, sport: str,
                          team_elo: float, opponent_elo: float) -> bool:
    """Check if this is a lookahead spot - easy game before a tough one."""
    # Only relevant for weekly sports (NFL, NCAAF) or when games are spaced out
    if sport in ("nfl", "ncaaf"):
        look_ahead_window = timedelta(days=10)
    else:
        look_ahead_window = timedelta(days=4)

    # Current game is a mismatch (team is heavily favored)
    elo_diff = team_elo - opponent_elo
    if elo_diff < 100:  # Not a big favorite, no lookahead risk
        return False

    # Check if next game is against a strong opponent
    next_game = session.query(Game).filter(
        Game.date > game_date,
        Game.date <= game_date + look_ahead_window,
        ((Game.home_team_id == team_id) | (Game.away_team_id == team_id))
    ).order_by(Game.date).first()

    if not next_game:
        return False

    # Get next opponent's ELO
    next_opp_id = next_game.away_team_id if next_game.home_team_id == team_id else next_game.home_team_id
    next_opp_elo = session.query(EloRating).filter(
        EloRating.team_id == next_opp_id
    ).first()

    if next_opp_elo and next_opp_elo.rating > team_elo - 50:
        # Next opponent is roughly equal or better
        return True

    return False


def _build_game_data(session: Session, game) -> GameData:
    home_stats = _get_team_stats(session, game.home_team_id, game.sport)
    away_stats = _get_team_stats(session, game.away_team_id, game.sport)
    odds_rows = session.query(Odds).filter(Odds.game_id == game.id).all()
    odds = [OddsSnapshot(bookmaker=o.bookmaker, moneyline_home=o.moneyline_home or 0,
        moneyline_away=o.moneyline_away or 0, spread_home=o.spread_home or 0.0,
        spread_away=o.spread_away or 0.0, over_under=o.over_under or 0.0) for o in odds_rows]

    # Schedule context
    h_fatigued, h_fatigue_score = _check_schedule_fatigue(session, game.home_team_id, game.date, game.sport)
    a_fatigued, a_fatigue_score = _check_schedule_fatigue(session, game.away_team_id, game.date, game.sport)
    home_stats.is_schedule_fatigued = h_fatigued
    home_stats.schedule_fatigue_score = h_fatigue_score
    away_stats.is_schedule_fatigued = a_fatigued
    away_stats.schedule_fatigue_score = a_fatigue_score

    h_lookahead = _check_lookahead_spot(session, game.home_team_id, game.away_team_id,
                                         game.date, game.sport,
                                         home_stats.elo_rating, away_stats.elo_rating)
    a_lookahead = _check_lookahead_spot(session, game.away_team_id, game.home_team_id,
                                         game.date, game.sport,
                                         away_stats.elo_rating, home_stats.elo_rating)
    home_stats.is_lookahead_spot = h_lookahead
    away_stats.is_lookahead_spot = a_lookahead

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
