import logging
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from backend.models import Game, Team, PlayerProp, PlayerStat, PickModel, StrategyModel, TeamStat
from backend.collectors.player_stats.collector import PlayerStatsCollector
from backend.collectors.player_stats.nba_api_source import NbaApiSource
from backend.collectors.player_stats.espn_stats_source import EspnStatsSource
from backend.collectors.player_stats.balldontlie_source import BallDontLieSource
from backend.collectors.player_stats.mysportsfeeds_source import MySportsFeedsSource
from backend.analysis.prop_analyzer import PropAnalyzer
from backend.analysis.odds_utils import calculate_payout
from backend.data_types import PropAnalysis

logger = logging.getLogger(__name__)


def _dedup_prop_analyses(analyses: list[PropAnalysis]) -> list[PropAnalysis]:
    """Collapse the same prop offered by multiple bookmakers into one pick.

    A prop is stored once per book, so the same (game, player, market, outcome,
    line) appears N times with an identical edge (edge ignores odds). Keep only
    the row with the best price (highest payout) for the bettor.
    """
    best: dict[tuple, PropAnalysis] = {}
    for a in analyses:
        key = (a.game_id, a.player_name, a.market, a.outcome, a.line)
        cur = best.get(key)
        if cur is None or calculate_payout(a.odds) > calculate_payout(cur.odds):
            best[key] = a
    return list(best.values())

def build_default_collector() -> PlayerStatsCollector:
    return PlayerStatsCollector({
        "nba": [NbaApiSource(), BallDontLieSource(), EspnStatsSource()],
        "nfl": [EspnStatsSource()],
        "ncaab": [EspnStatsSource()],
        "ncaaf": [EspnStatsSource()],
        "boxing": [EspnStatsSource()],
        "mma": [EspnStatsSource()],
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

    # Build game -> teams map and opponent defensive ratings cache
    game_teams = {}
    for g in games:
        game_teams[g.id] = (g.home_team_id, g.away_team_id)

    team_def_ratings = {}
    for tid in team_ids:
        def_stat = session.query(TeamStat).filter(
            TeamStat.team_id == tid,
            TeamStat.stat_type == "defensive_rating"
        ).order_by(TeamStat.id.desc()).first()
        if def_stat:
            team_def_ratings[tid] = def_stat.value

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

    # Generate game predictions for game script correlation
    from backend.pipeline.pick_generator import STRATEGY_MAP, _build_game_data
    import json as _json

    game_scripts = {}  # game_id -> {predicted_diff, ...}
    game_strategy = session.query(StrategyModel).filter(
        StrategyModel.is_active == True,
        StrategyModel.strategy_type == "game",
    ).first()

    if game_strategy:
        strategy_cls = STRATEGY_MAP.get(game_strategy.name)
        if strategy_cls:
            cfg = _json.loads(game_strategy.config_json)
            strat_instance = strategy_cls(game_strategy.name, cfg)
            for g in games:
                try:
                    game_data = _build_game_data(session, g)
                    # Get predicted point diff from the strategy
                    if hasattr(strat_instance, '_predicted_point_diff'):
                        diff = strat_instance._predicted_point_diff(game_data)
                        game_scripts[g.id] = {"predicted_diff": diff}
                    else:
                        # For strategies without _predicted_point_diff, use team stats
                        hs, aws = game_data.home_stats, game_data.away_stats
                        diff = hs.point_diff - aws.point_diff
                        game_scripts[g.id] = {"predicted_diff": diff}
                except Exception:
                    pass

    props_analyzed = 0
    winning: list[PropAnalysis] = []

    for prop in props:
        season_avg = session.query(PlayerStat).filter_by(
            player_name=prop.player_name, stat_type="season_avg").first()
        recent = (session.query(PlayerStat)
            .filter_by(player_name=prop.player_name, stat_type="game_log")
            .order_by(PlayerStat.game_date.desc()).limit(5).all())
        # Determine opponent defensive rating
        opponent_def = None
        player_team_id = None
        if season_avg:
            player_team_id = season_avg.team_id
        elif recent:
            player_team_id = recent[0].team_id
        if player_team_id and prop.game_id in game_teams:
            home_id, away_id = game_teams[prop.game_id]
            opp_id = away_id if player_team_id == home_id else home_id
            opponent_def = team_def_ratings.get(opp_id)

        # Determine game script for this prop
        game_script = None
        if prop.game_id in game_scripts:
            script = game_scripts[prop.game_id]
            if player_team_id and prop.game_id in game_teams:
                home_id_gs, away_id_gs = game_teams[prop.game_id]
                game_script = {
                    "predicted_diff": script["predicted_diff"],
                    "player_is_home": player_team_id == home_id_gs,
                }

        analysis = analyzer.analyze(prop, season_avg, recent,
                                    opponent_def_rating=opponent_def,
                                    game_script=game_script)
        props_analyzed += 1
        if analysis and analysis.confidence >= 1:
            winning.append(analysis)

    # Collapse the same prop offered by multiple bookmakers into one pick
    # (best price), then persist. Picks are only stored when a prop strategy
    # is active (strategy_id set).
    picks_generated = 0
    if strategy_id:
        for analysis in _dedup_prop_analyses(winning):
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
