"""Full pipeline: fetch games, odds, props from APIs → store in DB → generate picks."""
import logging
from datetime import date, datetime, time, timedelta, timezone
from sqlalchemy.orm import Session
from backend.collectors.espn import ESPNCollector
from backend.time_utils import et_date
from backend.collectors.odds_api import OddsAPICollector, redact_api_key
from backend.collectors.budget import check_budget, record_api_call, BudgetStatus
from backend.exceptions import BudgetExhaustedError
from backend.models import Team, Game, Odds, PlayerProp
from backend.team_identity import ABBREVIATION_SPORTS, canonical_abbr

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
            # Fallback for rows that have no espn_id yet -- ones created
            # before the column existed, or created from the Odds API, which
            # carries no ESPN identity.
            #
            # `espn_id.is_(None)` is load-bearing. Without it this matched any
            # row sharing (sport, date, teams), including rows that already
            # carry a DIFFERENT espn_id, so a genuinely distinct second game
            # between the same teams on the same day was silently folded into
            # the first. That is not hypothetical: mlb doubleheaders lost
            # their second game (CIN/STL 2026-05-23, BAL/DET 2026-05-24) and
            # so did the 2026 NBA All-Star Championship, which repeated the
            # round-robin's STRIPES v STARS pairing.
            existing = session.query(Game).filter(
                Game.sport == sport,
                Game.date == game_date,
                Game.home_team_id == home_id,
                Game.away_team_id == away_id,
                Game.espn_id.is_(None),
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
            if g.get("season_type") and g["season_type"] != "unknown":
                existing.season_type = g["season_type"]
            if "neutral_site" in g:
                # ESPN is authoritative here and the Odds API carries no
                # venue, so a row created from odds starts hosted and is
                # corrected the first time ESPN sees it. Overwritten rather
                # than filled-if-null: False is a real value, not a gap, so
                # there is no way to tell "not known yet" from "hosted".
                existing.neutral_site = bool(g["neutral_site"])
            touched.append(existing)
            continue

        game = Game(
            sport=sport, season=season_label, date=game_date,
            start_time=start_time, espn_id=espn_id,
            home_team_id=home_id, away_team_id=away_id,
            home_score=g["home_score"], away_score=g["away_score"],
            status=g["status"],
            neutral_site=g.get("neutral_site", False),
            season_type=g.get("season_type", "unknown"),
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


def _event_start(event: dict) -> datetime | None:
    raw = event.get("commence_time")
    if not raw:
        return None
    try:
        return _parse_start_time(raw)
    except (TypeError, ValueError):
        logger.warning("Unparseable commence_time %r on odds event", raw)
        return None


def _store_odds(session: Session, sport: str, odds_data: list[dict]) -> int:
    """Store odds on the game each event was priced for.

    The return value counts bookmaker rows written, not events -- the log
    line calling it "events" has always been wrong.
    """
    count = 0
    for event in odds_data:
        game = _find_game_by_teams(
            session, sport, event["home_team"], event["away_team"],
            when=_event_start(event),
        )
        if not game:
            # A bare `continue` here is how a whole slate could lose its
            # prices without anything noticing.
            logger.warning(
                "No %s game matches odds event %r vs %r at %s; its prices are "
                "dropped rather than attached to another fixture",
                sport, event.get("home_team"), event.get("away_team"),
                event.get("commence_time"),
            )
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


def _resolve_team(session: Session, sport: str, label: str) -> Team | None:
    """Find the team row an Odds API label refers to.

    Matches on name first -- the historical behaviour -- then on the canonical
    ESPN abbreviation, so "Pennsylvania Quakers" finds the row whose
    abbreviation is "PENN". Without the second step the name-only lookup
    misses, which both strands the game and skips the duplicate check below.
    """
    team = (
        session.query(Team)
        .filter(Team.sport == sport, Team.name == label)
        .first()
    )
    if team is not None:
        return team
    if sport not in ABBREVIATION_SPORTS:
        return None
    abbr = canonical_abbr(sport, label)
    if abbr is None:
        return None
    return (
        session.query(Team)
        .filter(Team.sport == sport, Team.abbreviation == abbr)
        .first()
    )


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

    # Resolve BEFORE the duplicate check below: that check is gated on both
    # sides resolving, so an unmatched label used to skip it and insert a twin
    # on every odds tick.
    home_team = _resolve_team(session, sport, home_name)
    away_team = _resolve_team(session, sport, away_name)

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

    # Create teams only if they don't exist.
    #
    # For combat sports the fighter's NAME is the identity, so a row keyed by
    # the label is correct. For team sports the identity is an abbreviation: a
    # row whose abbreviation is "Pennsylvania Quakers" can never match ESPN,
    # never gets an espn_id and never finalises -- which is how 59 ncaab games
    # and 413 picks ended up stranded. Resolve it, or refuse the whole game.
    #
    # Nothing is added until BOTH sides are settled, so an unidentifiable
    # opponent cannot leave a half-created game behind.
    pending: list[tuple[str, Team]] = []
    for side, team, label in (
        ("home", home_team, home_name), ("away", away_team, away_name),
    ):
        if team is not None:
            continue
        if sport not in ABBREVIATION_SPORTS:
            pending.append((side, Team(name=label, abbreviation=label, sport=sport)))
            continue
        abbr = canonical_abbr(sport, label)
        if abbr is None:
            logger.warning(
                "Cannot identify %s %s team %r; skipping game rather than "
                "creating a row that can never match ESPN", sport, side, label,
            )
            return
        pending.append((side, Team(name=label, abbreviation=abbr, sport=sport)))

    for side, team in pending:
        session.add(team)
        session.flush()
        if side == "home":
            home_team = team
        else:
            away_team = team

    season_label = f"{game_date.year}"
    session.add(Game(
        sport=sport, season=season_label, date=game_date,
        home_team_id=home_team.id, away_team_id=away_team.id,
        status="scheduled",
    ))
    session.commit()


#: How far a game may sit from an event's commence_time and still be the one
#: that event prices. Books and ESPN disagree by minutes, so this cannot be
#: exact; the next game of a series is ~24h away, so it must stay well under
#: that or a series collapses back onto one fixture.
_START_TIME_WINDOW = timedelta(hours=12)

#: For a game with no start_time, date is the only signal. One day of slack
#: absorbs the UTC/Eastern convention difference, which is the same problem
#: backfill_espn_ids carries neighbour-date logic for.
_DATE_WINDOW = timedelta(days=1)


def _lookup_team(session: Session, sport: str, label: str) -> Team | None:
    """The team a label names, by the same rules game creation uses.

    Exact name/abbreviation first, then `team_identity`. Without the second
    step this path and `_ensure_game_from_odds` disagree about the same
    string: the creation path resolves "Sam Houston State Bearkats" through
    an alias, while raw equality against ESPN's "Sam Houston Bearkats" fails.
    """
    team = session.query(Team).filter(
        Team.sport == sport,
        (Team.name == label) | (Team.abbreviation == label)
    ).first()
    if team is not None:
        return team
    abbr = canonical_abbr(sport, label)
    if abbr is None:
        return None
    return session.query(Team).filter(
        Team.sport == sport, Team.abbreviation == abbr
    ).first()


def _match_distance(game: Game, when: datetime) -> tuple[int, timedelta, timedelta]:
    """(precision, distance, allowed window) for a candidate game.

    ``precision`` is 0 when the game has a real start_time and 1 when only
    its date is known, and it sorts before the distance. That ordering is
    load-bearing rather than cosmetic: a late game's UTC timestamp falls on
    the next calendar day, so a date-only candidate dated that day scores a
    perfect zero on the date comparison and ties the game that actually
    starts at that instant. Ordering by date descending then handed the tie
    to the guess -- three mlb events matched the wrong fixture that way.
    """
    if game.start_time is not None:
        start = game.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        return 0, abs(start - when), _START_TIME_WINDOW
    midnight = datetime.combine(game.date, time(0), tzinfo=timezone.utc)
    return 1, abs(midnight - when.replace(hour=0, minute=0, second=0,
                                          microsecond=0)), _DATE_WINDOW


def _find_game_by_teams(session: Session, sport: str, home_name: str,
                        away_name: str, when: datetime | None = None) -> Game | None:
    """Find the game an odds event prices.

    ``when`` is the event's commence_time. Without it this used to order by
    date descending and take the first, which in a series -- the same two
    teams on consecutive days, routine in baseball -- wrote tonight's prices
    onto tomorrow's fixture. On 2026-09-19 that left 10 of 15 mlb games
    unpriced while tomorrow's rows carried prices that were not theirs.

    A candidate outside the window matches nothing. No price is better than
    another fixture's price.
    """
    home_team = _lookup_team(session, sport, home_name)
    away_team = _lookup_team(session, sport, away_name)
    if not home_team or not away_team:
        return None
    candidates = session.query(Game).filter(
        Game.sport == sport,
        Game.home_team_id == home_team.id,
        Game.away_team_id == away_team.id,
        Game.status.in_(["scheduled", "in_progress"]),
    ).order_by(Game.date.desc()).all()
    if not candidates:
        return None
    if when is None:
        # Callers with no commence_time keep the old behaviour.
        return candidates[0]

    best, best_rank = None, None
    for game in candidates:
        precision, distance, window = _match_distance(game, when)
        if distance > window:
            continue
        rank = (precision, distance)
        if best_rank is None or rank < best_rank:
            best, best_rank = game, rank
    return best


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
