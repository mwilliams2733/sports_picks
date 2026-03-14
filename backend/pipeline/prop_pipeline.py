import logging
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from backend.models import Game, Team, PlayerProp, PlayerStat, PickModel, StrategyModel
from backend.collectors.player_stats.collector import PlayerStatsCollector
from backend.collectors.player_stats.nba_api_source import NbaApiSource
from backend.collectors.player_stats.espn_stats_source import EspnStatsSource
from backend.collectors.player_stats.balldontlie_source import BallDontLieSource
from backend.collectors.player_stats.mysportsfeeds_source import MySportsFeedsSource
from backend.analysis.prop_analyzer import PropAnalyzer

logger = logging.getLogger(__name__)

def build_default_collector() -> PlayerStatsCollector:
    return PlayerStatsCollector({
        "nba": [NbaApiSource(), BallDontLieSource(), EspnStatsSource()],
        "nfl": [EspnStatsSource()],
        "ncaab": [EspnStatsSource()],
        "ncaaf": [EspnStatsSource()],
    })

async def run_prop_pipeline(session: Session, target_date: date | None = None,
                            strategy_id: int | None = None) -> dict:
    target_date = target_date or date.today()
    collector = build_default_collector()
    try:
        return await _run_prop_pipeline_inner(session, collector, target_date, strategy_id)
    finally:
        await collector.close()

async def _run_prop_pipeline_inner(session, collector, target_date, strategy_id):
    games = session.query(Game).filter(Game.date == target_date, Game.status == "scheduled").all()
    if not games:
        return {"games": 0, "stats_fetched": 0, "props_analyzed": 0, "picks_generated": 0}

    team_ids = set()
    for g in games:
        team_ids.add(g.home_team_id)
        team_ids.add(g.away_team_id)

    stats_count = 0
    for tid in team_ids:
        team = session.get(Team, tid)
        if not team:
            continue
        stats, source = await collector.fetch_player_stats(team.sport, team.abbreviation)
        if stats and source:
            count = collector.store_stats(session, stats, "season_avg", team.id, team.sport, source)
            stats_count += count
            for s in stats:
                recent, rsource = await collector.fetch_player_recent(team.sport, s["player_name"], n=5)
                if recent and rsource:
                    collector.store_stats(session, recent, "game_log", team.id, team.sport, rsource)

    props = session.query(PlayerProp).join(Game).filter(Game.date == target_date).all()

    # Build analyzer from strategy config if available
    analyzer_kwargs = {}
    if strategy_id:
        import json
        strat = session.get(StrategyModel, strategy_id)
        if strat:
            cfg = json.loads(strat.config_json)
            analyzer_kwargs = {
                "season_weight": cfg.get("season_weight", 0.4),
                "recent_weight": cfg.get("recent_weight", 0.6),
                "min_edge": cfg.get("min_edge", 5.0),
            }
    analyzer = PropAnalyzer(**analyzer_kwargs)
    picks_generated = 0
    props_analyzed = 0

    for prop in props:
        season_avg = session.query(PlayerStat).filter_by(
            player_name=prop.player_name, stat_type="season_avg").first()
        recent = (session.query(PlayerStat)
            .filter_by(player_name=prop.player_name, stat_type="game_log")
            .order_by(PlayerStat.game_date.desc()).limit(5).all())
        analysis = analyzer.analyze(prop, season_avg, recent)
        props_analyzed += 1
        if analysis and analysis.confidence >= 1 and strategy_id:
            pick = PickModel(
                game_id=analysis.game_id, strategy_id=strategy_id,
                pick_type="prop",
                pick_value=f"{analysis.player_name} {analysis.outcome} {analysis.line} {_market_label(analysis.market)}",
                confidence=analysis.confidence, edge_pct=analysis.edge_pct,
                odds_at_pick=analysis.odds, created_at=datetime.now(tz=timezone.utc),
            )
            session.add(pick)
            picks_generated += 1
    session.commit()
    return {"games": len(games), "stats_fetched": stats_count,
            "props_analyzed": props_analyzed, "picks_generated": picks_generated}

def _market_label(market: str) -> str:
    labels = {"player_points": "Points", "player_rebounds": "Rebounds",
              "player_assists": "Assists", "player_threes": "3-Pointers",
              "player_points_rebounds_assists": "PRA",
              "player_pass_yds": "Pass Yards", "player_rush_yds": "Rush Yards"}
    return labels.get(market, market)
