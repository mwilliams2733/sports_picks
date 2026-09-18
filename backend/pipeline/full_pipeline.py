"""Full pipeline: fetch games, odds, props from APIs → store in DB → generate picks."""
import logging
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session
from backend.collectors.espn import ESPNCollector
from backend.time_utils import et_date
from backend.collectors.odds_api import OddsAPICollector, redact_api_key
from backend.collectors.budget import check_budget, record_api_call, BudgetStatus
from backend.exceptions import BudgetExhaustedError
from backend.models import Team, Game, Odds, PlayerProp

logger = logging.getLogger(__name__)

ALL_SPORTS = ["nba", "nfl", "ncaab", "ncaaf", "boxing", "mma", "mlb"]


async def fetch_and_store_games(session: Session, sports: list[str],
                                target_date: date, *,
                                reconcile: bool = True) -> int:
    """Fetch one date's games from ESPN and store them in the DB.

    ``reconcile=False`` is finalize-only: scores and status are upserted, but
    :func:`_reconcile_against_espn` is skipped. Used for lookback days, where a
    team-pair match failure would mark a real game ``canceled`` instead of
    ``final`` -- turning a matching bug into data loss on exactly the rows a
    lookback exists to rescue.
    """
    espn = ESPNCollector()
    total = 0
    date_str = target_date.strftime("%Y%m%d")
    try:
        for sport in sports:
            try:
                games = await espn.fetch_scoreboard(sport, date_str)
                stored = _store_games(session, sport, target_date, games,
                                      reconcile=reconcile)
                total += stored
                logger.info(f"Stored {stored} {sport} games for {target_date}")
            except Exception as e:
                logger.warning("ESPN fetch failed for %s: %s: %s", sport, type(e).__name__, e)
    finally:
        await espn.close()
    return total


async def fetch_and_store_odds(session: Session, sports: list[str], api_key: str,
                                budget: dict | None = None) -> int:
    """Fetch odds from The Odds API and store them.

    For sports without ESPN coverage (boxing), also creates games from Odds API events.
    """
    collector = OddsAPICollector(api_key)
    total = 0
    try:
        for sport in sports:
            try:
                if budget:
                    status = check_budget(session, budget)
                    if status == BudgetStatus.MONTHLY_EXHAUSTED:
                        from backend.collectors.budget import get_credit_summary
                        summary = get_credit_summary(session, budget)
                        raise BudgetExhaustedError(summary["monthly_used"], budget["monthly_limit"], summary["daily_used"])
                    if status == BudgetStatus.RESERVE_EXHAUSTED:
                        logger.warning(f"Budget reserve exhausted, stopping odds fetch for {sport}")
                        break
                odds_data = await collector.fetch_odds(sport)
                record_api_call(session, "odds", sport, collector.requests_remaining)
                # For each event, ensure a game exists (creates from Odds API if needed)
                for event in odds_data:
                    _ensure_game_from_odds(session, sport, event)
                stored = _store_odds(session, sport, odds_data)
                total += stored
                logger.info(f"Stored odds for {stored} {sport} events (remaining: {collector.requests_remaining})")
            except BudgetExhaustedError:
                raise
            except Exception as e:
                logger.warning(
                    "Odds API fetch failed for %s: %s: %s",
                    sport, type(e).__name__, redact_api_key(str(e)),
                )
    finally:
        await collector.close()
    return total


async def fetch_and_store_props(session: Session, sports: list[str], api_key: str,
                                 budget: dict | None = None,
                                 window_game_ids: set[int] | None = None) -> int:
    """Fetch player props from The Odds API and store them.

    Only fetches for sports that have prop markets defined.
    Limits to games that exist in our DB to avoid excessive API calls.
    """
    from backend.collectors.odds_api import PROP_MARKETS
    collector = OddsAPICollector(api_key)
    total = 0
    try:
        for sport in sports:
            # Skip sports with no prop markets
            if not PROP_MARKETS.get(sport):
                continue
            try:
                if budget:
                    status = check_budget(session, budget)
                    if status == BudgetStatus.MONTHLY_EXHAUSTED:
                        from backend.collectors.budget import get_credit_summary
                        summary = get_credit_summary(session, budget)
                        raise BudgetExhaustedError(summary["monthly_used"], budget["monthly_limit"], summary["daily_used"])
                events = await collector.fetch_events(sport)
                record_api_call(session, "events", sport, collector.requests_remaining)
                for event in events:
                    event_id = event.get("id")
                    if not event_id:
                        continue
                    # Only fetch props for games we have in the DB
                    game = _find_game_for_event(session, sport, event)
                    if not game:
                        continue
                    if window_game_ids is not None and game.id not in window_game_ids:
                        continue
                    # Budget check before expensive prop call
                    if budget:
                        status = check_budget(session, budget)
                        if status in (BudgetStatus.MONTHLY_EXHAUSTED, BudgetStatus.RESERVE_EXHAUSTED):
                            logger.warning(f"Budget limit reached, stopping prop fetch for {sport}")
                            break
                    props = await collector.fetch_player_props(sport, event_id)
                    record_api_call(session, "player_props", sport, collector.requests_remaining)
                    if not props:
                        continue
                    stored = _store_props(session, game.id, props)
                    total += stored
                logger.info(f"Stored {total} props for {sport}")
            except BudgetExhaustedError:
                raise
            except Exception as e:
                logger.warning(
                    "Props fetch failed for %s: %s: %s",
                    sport, type(e).__name__, redact_api_key(str(e)),
                )
    finally:
        await collector.close()
    return total


def _store_games(session: Session, sport: str, target_date: date,
                 games: list[dict], *, reconcile: bool = True) -> int:
    """Store ESPN games into the database, creating teams as needed.

    After upserting whatever ESPN returned, reconcile against ESPN's
    authoritative list for `target_date`: any pending row in our DB that
    ESPN didn't list is marked status='canceled' (and excluded from
    Today's Picks). A row that was previously canceled but now appears in
    ESPN's list (e.g. rescheduled postponement) is restored to 'scheduled'.
    Final / in_progress rows are never altered. Reconciliation is skipped
    when ESPN returned zero events for the target_date sport — we can't
    distinguish an off-day from an outage, so we leave the DB alone.
    """
    team_cache: dict[str, int] = {}
    count = 0
    season_label = f"{target_date.year}-{target_date.year + 1}"
    # Rows we touched this run, so their point-in-time team_stats can be
    # (re)computed once scores have landed. See _refresh_team_stats below.
    touched: list[Game] = []
    # Unordered team pairs ESPN listed for target_date. Used for reconciliation.
    espn_pairs_target_date: set[frozenset[int]] = set()

    for g in games:
        home_abbr = g["home_team"]
        away_abbr = g["away_team"]
        home_id = _ensure_team(session, team_cache, home_abbr, g["home_team_name"], sport)
        away_id = _ensure_team(session, team_cache, away_abbr, g["away_team_name"], sport)

        game_date = et_date(g["date"])
        start_time = _parse_start_time(g["date"])

        if game_date == target_date:
            espn_pairs_target_date.add(frozenset({home_id, away_id}))

        # ESPN's event id is the only stable identity we have. Matching on it
        # first is what lets the same game be recognised when it was stored
        # under a different date convention -- ESPN timestamps in UTC but
        # files its scoreboard by Eastern date, so an evening game lands a day
        # late and a (date, teams) lookup misses it entirely.
        existing = None
        espn_id = g.get("espn_id")
        if espn_id:
            existing = session.query(Game).filter(
                Game.sport == sport, Game.espn_id == espn_id
            ).first()
            if existing is not None and existing.date != game_date:
                logger.info("Correcting %s game %s date: %s -> %s",
                            sport, existing.id, existing.date, game_date)
                existing.date = game_date

        if existing is None:
            # Fallback for rows created before espn_id existed.
            existing = session.query(Game).filter(
                Game.sport == sport,
                Game.date == game_date,
                Game.home_team_id == home_id,
                Game.away_team_id == away_id,
            ).first()

        if existing:
            if g["status"] == "final" and existing.status != "final":
                existing.home_score = g["home_score"]
                existing.away_score = g["away_score"]
                existing.status = g["status"]
            if existing.start_time is None:
                existing.start_time = start_time
            if existing.espn_id is None and espn_id:
                # Rows predating the column acquire it as they are seen, so
                # the backfill script is a catch-up rather than the only path.
                existing.espn_id = espn_id
            touched.append(existing)
            continue

        game = Game(
            sport=sport, season=season_label, date=game_date,
            start_time=start_time, espn_id=espn_id,
            home_team_id=home_id, away_team_id=away_id,
            home_score=g["home_score"], away_score=g["away_score"],
            status=g["status"],
        )
        session.add(game)
        touched.append(game)
        count += 1

    if reconcile and espn_pairs_target_date:
        _reconcile_against_espn(session, sport, target_date, espn_pairs_target_date)

    session.commit()
    _refresh_team_stats(session, touched)
    return count


def _refresh_team_stats(session: Session, games: list[Game]) -> None:
    """Recompute point-in-time team_stats for the games this run touched.

    This is the only production producer of TeamStat rows. It runs here, off
    the games/scores path, and deliberately NOT off the odds or props paths:
    the stats depend on scores and dates only.

    The values are computed from games strictly *before* each game's own date
    (see backend.pipeline.team_stats), so a game's own result can never enter
    its own features even though it has just been written as final. The write
    is an upsert, so a game re-seen on a later run is updated, not duplicated.

    A failure here must not lose the games we just stored, so it is logged
    rather than raised -- the backfill script can always fill the gap, and the
    resume check is per game.
    """
    if not games:
        return
    from backend.pipeline.team_stats import (
        COMBAT_SPORTS, backfill_elo_history, update_team_stats_for_games,
    )
    try:
        written = update_team_stats_for_games(session, games)
        # The backfill script is a one-off; without this, elo_history would
        # stop growing the day it finishes. backfill_elo_history replays the
        # sport and skips games that already have rows, so this only appends
        # the new games -- with the correct pre-game rating for each.
        # Combat sports are excluded: grader._apply_combat_elo_update owns
        # their history and writes it post-game.
        for sport in {g.sport for g in games} - set(COMBAT_SPORTS):
            backfill_elo_history(session, sport)
        session.commit()
        logger.info("Refreshed %d team_stat values across %d games",
                    written, len(games))
    except Exception:
        session.rollback()
        logger.exception("team_stats refresh failed for %d games", len(games))


def _reconcile_against_espn(session: Session, sport: str, target_date: date,
                            espn_pairs: set[frozenset[int]]) -> None:
    """Mark/unmark canceled status for (sport, target_date) rows based on
    whether ESPN's authoritative list includes the team pair. Caller
    should only invoke this when ESPN returned at least one event for the
    target_date sport — otherwise we can't tell an outage from an off-day.
    """
    db_games = (
        session.query(Game)
        .filter(Game.sport == sport, Game.date == target_date)
        .all()
    )
    for db_game in db_games:
        if db_game.status in ("final", "in_progress"):
            continue
        pair = frozenset({db_game.home_team_id, db_game.away_team_id})
        if pair in espn_pairs:
            if db_game.status == "canceled":
                db_game.status = "scheduled"
        else:
            if db_game.status != "canceled":
                db_game.status = "canceled"


def _store_odds(session: Session, sport: str, odds_data: list[dict]) -> int:
    """Store odds linked to games by matching team names."""
    count = 0
    for event in odds_data:
        game = _find_game_by_teams(session, sport, event["home_team"], event["away_team"])
        if not game:
            continue

        for bk in event["bookmakers"]:
            existing = session.query(Odds).filter(
                Odds.game_id == game.id, Odds.bookmaker == bk["key"]
            ).first()
            if existing:
                existing.moneyline_home = bk["moneyline_home"]
                existing.moneyline_away = bk["moneyline_away"]
                existing.spread_home = bk["spread_home"]
                existing.spread_away = bk["spread_away"]
                existing.over_under = bk["over_under"]
                existing.timestamp = datetime.now(tz=timezone.utc)
            else:
                session.add(Odds(
                    game_id=game.id, bookmaker=bk["key"],
                    moneyline_home=bk["moneyline_home"], moneyline_away=bk["moneyline_away"],
                    spread_home=bk["spread_home"], spread_away=bk["spread_away"],
                    over_under=bk["over_under"],
                ))
            count += 1

    session.commit()
    return count


def _store_props(session: Session, game_id: int, props: list[dict]) -> int:
    """Store player props for a game."""
    count = 0
    for p in props:
        existing = session.query(PlayerProp).filter(
            PlayerProp.game_id == game_id,
            PlayerProp.bookmaker == p["bookmaker"],
            PlayerProp.market == p["market"],
            PlayerProp.player_name == p["player_name"],
            PlayerProp.outcome == p["outcome"],
        ).first()
        if existing:
            existing.line = p["line"]
            existing.odds = p["odds"]
            existing.fetched_at = datetime.now(tz=timezone.utc)
        else:
            session.add(PlayerProp(
                game_id=game_id, bookmaker=p["bookmaker"],
                market=p["market"], player_name=p["player_name"],
                outcome=p["outcome"], line=p["line"], odds=p["odds"],
            ))
        count += 1
    session.commit()
    return count


def _ensure_game_from_odds(session: Session, sport: str, event: dict) -> None:
    """Create a game from Odds API event if it doesn't already exist in the DB.

    First tries to match an existing game by team names. Only creates new
    teams/games for sports without ESPN coverage (boxing, etc.).
    """
    home_name = event.get("home_team", "")
    away_name = event.get("away_team", "")
    commence = event.get("commence_time", "")
    if not home_name or not away_name or not commence:
        return

    # The Odds API's commence_time is UTC too, and this date is matched
    # against game rows that are now Eastern-dated. Using a different
    # convention here would recreate the very split this fixes.
    game_date = et_date(commence)

    # Try to find existing teams by name first
    home_team = session.query(Team).filter(Team.sport == sport, Team.name == home_name).first()
    away_team = session.query(Team).filter(Team.sport == sport, Team.name == away_name).first()

    # If both teams already exist, check for an exact (sport, date, teams)
    # match BEFORE creating a duplicate — and don't filter by status here.
    # Once a game grades to 'final', the previous lookup-by-status would miss
    # it and re-insert a phantom 'scheduled' duplicate on the next Odds tick.
    if home_team and away_team:
        existing = (
            session.query(Game)
            .filter(
                Game.sport == sport,
                Game.date == game_date,
                Game.home_team_id == home_team.id,
                Game.away_team_id == away_team.id,
            )
            .first()
        )
        if existing:
            return

    # Create teams only if they don't exist (primarily for boxing/MMA fighters)
    if not home_team:
        home_team = Team(name=home_name, abbreviation=home_name, sport=sport)
        session.add(home_team)
        session.flush()
    if not away_team:
        away_team = Team(name=away_name, abbreviation=away_name, sport=sport)
        session.add(away_team)
        session.flush()

    season_label = f"{game_date.year}"
    session.add(Game(
        sport=sport, season=season_label, date=game_date,
        home_team_id=home_team.id, away_team_id=away_team.id,
        status="scheduled",
    ))
    session.commit()


def _find_game_by_teams(session: Session, sport: str, home_name: str, away_name: str) -> Game | None:
    """Find a game by matching team names or abbreviations."""
    home_team = session.query(Team).filter(
        Team.sport == sport,
        (Team.name == home_name) | (Team.abbreviation == home_name)
    ).first()
    away_team = session.query(Team).filter(
        Team.sport == sport,
        (Team.name == away_name) | (Team.abbreviation == away_name)
    ).first()
    if not home_team or not away_team:
        return None
    return session.query(Game).filter(
        Game.sport == sport,
        Game.home_team_id == home_team.id,
        Game.away_team_id == away_team.id,
        Game.status.in_(["scheduled", "in_progress"]),
    ).order_by(Game.date.desc()).first()


def _find_game_for_event(session: Session, sport: str, event: dict) -> Game | None:
    """Find a game matching an Odds API event."""
    return _find_game_by_teams(session, sport, event.get("home_team", ""), event.get("away_team", ""))


def _ensure_team(session: Session, cache: dict[str, int], abbr: str, name: str, sport: str) -> int:
    key = f"{sport}:{abbr}"
    if key in cache:
        return cache[key]
    team = session.query(Team).filter(Team.abbreviation == abbr, Team.sport == sport).first()
    if not team:
        team = Team(name=name, abbreviation=abbr, sport=sport)
        session.add(team)
        session.flush()
    cache[key] = team.id
    return team.id


def _parse_start_time(date_str: str) -> datetime:
    """Parse ISO datetime string to full UTC datetime."""
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
