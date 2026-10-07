import logging
from datetime import date, datetime, timedelta, timezone
import httpx
from sqlalchemy.orm import Session
from backend.models import Game, Team, PlayerProp, PlayerStat, PickModel, StrategyModel, TeamStat
from backend.pipeline.pick_versions import record_pick_version
from backend.collectors.player_stats.collector import PlayerStatsCollector
from backend.collectors.player_stats.nba_api_source import NbaApiSource
from backend.collectors.player_stats.espn_stats_source import EspnStatsSource
from backend.collectors.player_stats.balldontlie_source import BallDontLieSource
from backend.collectors.player_stats.mysportsfeeds_source import MySportsFeedsSource
from backend.analysis import football_defense, football_injuries, football_weather
from backend.collectors.weather import latest_weather
from backend.analysis.prop_analyzer import PropAnalyzer
from backend.analysis.prop_confidence import get_prop_thresholds
from backend.analysis.prop_markets import market_label
from backend.analysis.odds_utils import calculate_payout, InvalidOddsError
from backend.data_types import PropAnalysis
from backend.time_utils import as_utc, et_today

logger = logging.getLogger(__name__)


def _dedup_prop_analyses(analyses: list[PropAnalysis]) -> list[PropAnalysis]:
    """Collapse the same prop offered by multiple bookmakers into one pick.

    A prop is stored once per book, so the same (game, player, market, outcome,
    line) appears N times. Keep only the row with the best price (highest
    payout) for the bettor -- since 2026-10-04 also the row with the most
    edge, because edge is measured against the price.
    """
    best: dict[tuple, PropAnalysis] = {}
    for a in analyses:
        try:
            payout = calculate_payout(a.odds)
        except InvalidOddsError:
            logger.warning("Skipping prop with invalid odds=%r for %s %s", a.odds, a.player_name, a.market)
            continue
        key = (a.game_id, a.player_name, a.market, a.outcome, a.line)
        cur = best.get(key)
        if cur is None:
            best[key] = a
            continue
        cur_payout = calculate_payout(cur.odds)
        if payout > cur_payout or (payout == cur_payout and a.bookmaker < cur.bookmaker):
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
                            strategy_id: int | None = None,
                            sports: tuple[str, ...] | None = None) -> dict:
    """Collect player stats and generate prop picks for ``target_date``.

    ``sports`` limits which of the day's games are considered. It exists
    because `_run_window` runs once per window and called this with no
    filter, so every window collected stats for every sport playing that
    day: three windows on 2026-09-20 fetched every NFL roster three times.
    ESPN has no published rate limit but starts returning 403 once a
    client asks quickly enough, so the redundant volume is not free.

    ``None`` still means every sport, which is what `fetch_odds_now` and
    the pipeline API deliberately want.
    """
    target_date = target_date or et_today()
    collector = build_default_collector()
    try:
        return await _run_prop_pipeline_inner(session, collector, target_date,
                                              strategy_id, sports=sports)
    finally:
        await collector.close()

async def _run_prop_pipeline_inner(session, collector, target_date, strategy_id,
                                   sports=None):
    game_q = session.query(Game).filter(Game.date == target_date,
                                        Game.status == "scheduled")
    if sports:
        game_q = game_q.filter(Game.sport.in_(list(sports)))
    games = game_q.all()
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
            # game_log rows come from collectors/espn_box_score.py, which runs
            # post-game from morning_scout. Fetching "last 5" here was a second
            # path to the same table that never produced a row: nba_api raised
            # before its request (last_n_games is not a PlayerGameLog
            # parameter), stats.nba.com times out from this network, and the
            # ESPN athlete endpoint 404s. It was also the wrong shape -- a
            # pre-game fetch cannot contain the game being predicted, which is
            # what the box-score collector writes. Removing it drops one HTTP
            # call per player per run for no loss.

    # Only the games in scope. Unscoped, every window analysed every sport's
    # props for the date under one sport's thresholds, and re-priced them.
    all_props = (session.query(PlayerProp)
                 .filter(PlayerProp.game_id.in_([g.id for g in games])).all())
    props = current_props(all_props)

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

    # NFL pass / run yards allowed this season before each game's date (the
    # matchup factor; defensive_rating above is basketball-only). Measured
    # for nfl only, so ncaaf is left out.
    allowed_cache: dict[tuple[str, date], dict] = {}
    nfl_allowed: dict[int, dict] = {}
    for g in games:
        if g.sport == "nfl":
            key = (g.season, g.date)
            if key not in allowed_cache:
                allowed_cache[key] = football_defense.yards_allowed(
                    session, "nfl", g.season, g.date)
            nfl_allowed[g.id] = allowed_cache[key]

    # NFL: who is missing today (ESPN roster) and each team's season yards
    # leaders, for the teammate-out factor. One roster request per team; an
    # unreadable roster is None, which applies no adjustment.
    nfl_leaders: dict[tuple[int, int], dict[str, str]] = {}
    nfl_rosters: dict[int, dict | None] = {}
    nfl_games = [g for g in games if g.sport == "nfl"]
    if nfl_games:
        async with httpx.AsyncClient(timeout=20) as client:
            for g in nfl_games:
                for tid in (g.home_team_id, g.away_team_id):
                    nfl_leaders[(g.id, tid)] = football_injuries.season_leaders(
                        session, tid, g.season, g.date)
                    if tid not in nfl_rosters:
                        team = session.get(Team, tid)
                        nfl_rosters[tid] = (await football_injuries.fetch_roster(
                            client, team.abbreviation) if team else None)
        for (gid, tid), lead in nfl_leaders.items():
            roster = nfl_rosters.get(tid)
            out = {k: p for k, p in lead.items()
                   if roster is not None and football_injuries.is_absent(roster, p)}
            if out:
                logger.info("injuries: game %s team %s missing leaders %s", gid, tid, out)

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
    # One PropAnalyzer per sport (below): thresholds are keyed by sport, and an
    # unscoped call (fetch_odds_now, the pipeline API) spans several. This
    # used to build one analyzer from games[0]'s sport for all of them.
    sport_of = {g.id: g.sport for g in games}
    analyzers: dict[str, PropAnalyzer] = {}

    def analyzer_for(game_id: int) -> PropAnalyzer:
        sport = sport_of[game_id]
        if sport not in analyzers:
            analyzers[sport] = PropAnalyzer(
                **analyzer_kwargs, thresholds=get_prop_thresholds(session, sport))
        return analyzers[sport]

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

    # The latest captured forecast per NFL game (the window captures it just
    # before this runs). No forecast -- roofed, neutral, failed -- is no factor.
    nfl_weather = latest_weather(session, [g.id for g in nfl_games]) if nfl_games else {}

    props_analyzed = 0
    injury_adjusted = 0
    weather_adjusted = 0
    winning: list[PropAnalysis] = []
    # (game, player, market) keys this run gave an answer for. Only these
    # can be withdrawn: a stored prop this run never looked at is no answer,
    # not the answer "no pick". Every key on an in-scope game is answered --
    # analysed if a book still offers it, and "no longer offered" if no book
    # does (`current_props`) -- so a pulled prop's pick is withdrawn too.
    answered: set[tuple] = {(p.game_id, p.player_name, p.market) for p in all_props}

    for prop in props:
        season_avg = session.query(PlayerStat).filter_by(
            player_name=prop.player_name, stat_type="season_avg").first()
        # Every game in scope is on target_date (see the query above), so
        # bounding by it is exactly "strictly before the game being predicted".
        recent = _recent_form(session, prop.player_name, before=target_date)
        # Determine opponent defensive rating
        opponent_def = None
        player_team_id = None
        if season_avg:
            player_team_id = season_avg.team_id
        elif recent:
            player_team_id = recent[0].team_id
        matchup = None
        injury = None
        if player_team_id and (prop.game_id, player_team_id) in nfl_leaders:
            injury = football_injuries.injury_factor(
                prop.market, prop.player_name,
                nfl_leaders[(prop.game_id, player_team_id)],
                nfl_rosters.get(player_team_id))
            injury_adjusted += injury is not None
        weather = None
        if prop.game_id in nfl_weather:
            w = nfl_weather[prop.game_id]
            weather = football_weather.prop_factor(prop.market, w.precip_mm, w.wind_mph)
            weather_adjusted += weather is not None
        if player_team_id and prop.game_id in game_teams:
            home_id, away_id = game_teams[prop.game_id]
            opp_id = away_id if player_team_id == home_id else home_id
            opponent_def = team_def_ratings.get(opp_id)
            if prop.game_id in nfl_allowed:
                matchup = football_defense.matchup_factor(
                    nfl_allowed[prop.game_id], opp_id, prop.market)

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

        analysis = analyzer_for(prop.game_id).analyze(prop, season_avg, recent,
                                    opponent_def_rating=opponent_def,
                                    game_script=game_script,
                                    matchup_factor=matchup,
                                    injury_factor=injury,
                                    weather_factor=weather)
        props_analyzed += 1
        if analysis and analysis.confidence >= 1:
            winning.append(analysis)

    # Picks are only stored when a prop strategy is active (strategy_id set).
    picks_generated = picks_refreshed = picks_withdrawn = 0
    if strategy_id:
        picks_generated, picks_refreshed, picks_withdrawn = _store_prop_picks(
            session, winning, strategy_id, answered=answered)
    session.commit()
    if injury_adjusted:
        logger.info("injuries: adjusted %d NFL prop projection(s)", injury_adjusted)
    if weather_adjusted:
        logger.info("weather: adjusted %d NFL prop projection(s)", weather_adjusted)
    if picks_withdrawn:
        logger.info("Withdrew %d prop pick(s) that no longer qualify", picks_withdrawn)
    return {"games": len(games), "stats_fetched": stats_count,
            "props_analyzed": props_analyzed, "picks_generated": picks_generated,
            "picks_refreshed": picks_refreshed, "picks_withdrawn": picks_withdrawn}


#: Sports whose prop picks are stored as tracking picks (`tracking_only`):
#: generated and graded, never published or emailed. Football since
#: 2026-10-04 (owner): `player_stats` "season_avg" rows for nfl/ncaaf hold
#: season TOTALS, which the analyzer reads as per-game, so every projection
#: was inflated -- 451 graded props predicted 0.818 and hit 0.494, with no
#: signal at any confidence. Remove a sport once its props are fixed and
#: re-measured.
PROP_TRACKED_SPORTS = frozenset({"nfl", "ncaaf"})


#: Rows written by one prop fetch carry timestamps seconds apart; fetches
#: for a game are a window (hours) apart. Anything this far behind the
#: game's latest row was not in the latest fetch.
PROP_FETCH_SLACK = timedelta(minutes=10)


def current_props(rows: list[PlayerProp]) -> list[PlayerProp]:
    """The rows each game's most recent prop fetch returned.

    `full_pipeline._store_props` upserts per (game, book, market, player,
    outcome) and restamps ``fetched_at`` on every row a fetch returns. A
    row a book stopped offering -- the player was ruled out, the market
    pulled -- is never deleted and keeps its old stamp, so analysing every
    row kept publishing a prop no book offered, at its last line.

    Relative to the game's own latest fetch, not to the clock: a game not
    re-fetched since an earlier window still has current rows. Known gap:
    a fetch that returns nothing writes nothing, so a game whose books
    pulled every prop keeps its previous fetch as "current".
    """
    latest: dict[int, datetime] = {}
    for r in rows:
        ts = as_utc(r.fetched_at)
        if r.game_id not in latest or ts > latest[r.game_id]:
            latest[r.game_id] = ts
    return [r for r in rows
            if latest[r.game_id] - as_utc(r.fetched_at) <= PROP_FETCH_SLACK]


def _one_per_player_market(analyses: list[PropAnalysis]) -> list[PropAnalysis]:
    """Keep one line per (game, player, market): alternate lines are one bet.

    Over 204.5 / 205.5 / 207.5 / 211.5 pass yards for one quarterback resolve
    on the same yards. Stored as four picks, they were graded as four
    wagers. Only one is kept.

    Ranked by edge, not price: across lines a better price usually means a
    harder line, and edge is the only figure measured against the line.
    Ties go to the better payout, then to the bookmaker name, so the choice
    does not depend on input order.
    """
    groups: dict[tuple, list[PropAnalysis]] = {}
    for a in analyses:
        groups.setdefault((a.game_id, a.player_name, a.market), []).append(a)
    return [min(g, key=lambda a: (-a.edge_pct, -calculate_payout(a.odds),
                                  a.bookmaker))
            for g in groups.values()]


def _store_prop_picks(session: Session, analyses: list[PropAnalysis],
                      strategy_id: int, *,
                      answered: set[tuple] | None = None) -> tuple[int, int, int]:
    """Persist prop picks, one per (game, player, market, strategy).

    Returns ``(added, refreshed, withdrawn)``.

    ``answered`` is the (game, player, market) keys the run analysed. A
    stored pick whose key was answered but not produced has stopped
    qualifying and is withdrawn, on the same terms as a game pick
    (`pick_generator.withdraw_pick`: never graded, started or emailed); a
    withdrawn pick produced again is reinstated by `_refresh_prop_pick`.
    ``None`` withdraws nothing. Before this, a prop that stopped qualifying
    stayed published at its last edge until kickoff.

    Every window run re-analyses the whole
    day, and this used to ``session.add`` every result each time: 18 runs on
    2026-09-26 stored each prop 18 times, and grading counted every copy as
    a wager. Now an existing pick is refreshed in place, on exactly the
    terms game picks use (`pick_generator._refreshable`: never once graded,
    never once its game has started), and a started game gets no new pick,
    matching ``skip_started``.
    """
    from backend.pipeline.pick_generator import _refreshable, withdraw_pick
    from backend.models import EmailedPick, PickResult
    from backend.time_utils import game_start_utc

    answered = answered or set()
    chosen = _one_per_player_market(_dedup_prop_analyses(analyses))
    if not chosen and not answered:
        return 0, 0, 0
    game_ids = {a.game_id for a in chosen} | {k[0] for k in answered}
    games = {g.id: g for g in
             session.query(Game).filter(Game.id.in_(game_ids))}
    already: dict[tuple, PickModel] = {}
    # Ascending id, first wins: rows duplicated before this fix leave the
    # oldest as the one refreshed, and the rest untouched.
    for row in (session.query(PickModel)
                .filter(PickModel.strategy_id == strategy_id,
                        PickModel.pick_type == "prop",
                        PickModel.game_id.in_(game_ids))
                .order_by(PickModel.id.asc())):
        already.setdefault((row.game_id, row.prop_player, row.prop_market), row)
    graded = {pid for (pid,) in session.query(PickResult.pick_id).filter(
        PickResult.pick_id.in_([p.id for p in already.values()] or [-1]))}
    # Advice already sent: never withdrawn, and never moved to tracking.
    emailed = {pid for (pid,) in session.query(EmailedPick.pick_id).filter(
        EmailedPick.pick_id.in_([p.id for p in already.values()] or [-1]))}

    now = datetime.now(tz=timezone.utc)
    added = refreshed = 0
    for a in chosen:
        game = games.get(a.game_id)
        if game is None:
            continue
        tracked = game.sport in PROP_TRACKED_SPORTS
        existing = already.get((a.game_id, a.player_name, a.market))
        if existing is not None:
            if _refreshable(existing, game, graded):
                _refresh_prop_pick(existing, a)
                if existing.id not in emailed:
                    existing.tracking_only = tracked
                record_pick_version(session, existing, "refresh")
                refreshed += 1
            continue
        start = game_start_utc(game)
        if start is not None and start <= now:
            continue
        # No in-run registration needed: `chosen` is already one per key.
        new_pick = _build_prop_pick(a, strategy_id)
        new_pick.tracking_only = tracked
        session.add(new_pick)
        record_pick_version(session, new_pick, "insert")
        added += 1

    withdrawn = 0
    produced = {(a.game_id, a.player_name, a.market) for a in chosen}
    stale = [row for key, row in already.items()
             if key in answered and key not in produced]
    if stale:
        for row in stale:
            withdrawn += withdraw_pick(session, row, games[row.game_id],
                                       graded, emailed, now)
    session.flush()
    return added, refreshed, withdrawn


def _refresh_prop_pick(existing: PickModel, analysis: PropAnalysis) -> None:
    """Overwrite an ungraded, unstarted prop pick with a fresh analysis.

    Derived from `_build_prop_pick` so the two can never disagree about
    what a prop pick contains. `rationale_json` is not in the refreshed
    field list -- unlike a game pick, `_build_prop_pick` never sets it
    (props have no rationale), so there is nothing stale to refresh here.
    The game-pick equivalent of this bug (`pick_generator._refresh_pick`
    used to leave `rationale_json` untouched after a flip) does not apply.
    """
    fresh = _build_prop_pick(analysis, existing.strategy_id)
    for field in ("pick_value", "confidence", "edge_pct", "odds_at_pick",
                  "model_prob", "created_at"):
        setattr(existing, field, getattr(fresh, field))
    # Produced again, so no longer withdrawn.
    existing.withdrawn_at = None

def _recent_form(session: Session, player_name: str, *, before: date) -> list:
    """The player's last five game logs strictly BEFORE ``before``.

    The date bound is the point of this function. Without it the query took the
    five most recent rows outright, which reads the future: a prop on a game
    that has since been played would be analysed using that game's own box
    score. Harmless while ``game_log`` was empty; not harmless now that plan
    010's collector fills it, and recent form is 60% of the projection
    (``PropAnalyzer.recent_weight``).

    Same defect shape as the team-stat scoping plan 008 fixed in `399ac79`.
    """
    return (session.query(PlayerStat)
            .filter(PlayerStat.player_name == player_name,
                    PlayerStat.stat_type == "game_log",
                    PlayerStat.game_date < before)
            .order_by(PlayerStat.game_date.desc()).limit(5).all())


def _build_prop_pick(analysis, strategy_id: int) -> PickModel:
    """The PickModel for one analysed prop.

    ``prop_player`` and ``prop_market`` are written here, at generation time,
    because they cannot be recovered reliably afterwards: ``pick_value`` embeds
    a *display label*, and :func:`_market_label` leaves the alias spellings in
    ``MARKET_STAT_MAP`` unlabelled, so the reverse mapping is not one-to-one. Grading
    reads the market key, never the label.
    """
    return PickModel(
        game_id=analysis.game_id, strategy_id=strategy_id,
        pick_type="prop",
        pick_value=f"{analysis.player_name} {analysis.outcome} {analysis.line} {_market_label(analysis.market)}",
        confidence=analysis.confidence, edge_pct=analysis.edge_pct,
        odds_at_pick=analysis.odds, created_at=datetime.now(tz=timezone.utc),
        prop_player=analysis.player_name, prop_market=analysis.market,
        model_prob=getattr(analysis, "model_probability", None),
    )


#: Re-exported under its old name: backfill_prop_fields and its tests import
#: it from here. The table lives in prop_markets so the digest shares it.
_market_label = market_label
