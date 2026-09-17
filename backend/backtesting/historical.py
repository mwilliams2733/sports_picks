import asyncio
import logging
from datetime import date, timedelta, datetime, timezone
from sqlalchemy.orm import Session
from backend.collectors.espn import ESPNCollector
from backend.models import Team, Game, EloRating

logger = logging.getLogger(__name__)

# Season date ranges: (start_month, start_day, end_month, end_day)
SEASON_RANGES = {
    "nba": (10, 22, 6, 20),
    "nfl": (9, 5, 2, 10),
    "ncaab": (11, 4, 4, 8),
    "ncaaf": (8, 24, 1, 20),
    "boxing": (1, 1, 12, 31),
    "mma": (1, 1, 12, 31),
}


def _season_dates(sport: str, season_year: int) -> tuple[date, date]:
    """Return (start_date, end_date) for a given sport and season start year."""
    sm, sd, em, ed = SEASON_RANGES[sport]
    start = date(season_year, sm, sd)
    # Seasons that cross year boundary (NFL Sep→Feb, NCAAF Aug→Jan)
    end_year = season_year + 1 if em < sm else season_year
    end = date(end_year, em, ed)
    return start, end


async def fetch_season_games(sport: str, season_year: int, rate_limit: float = 1.0) -> list[dict]:
    """Fetch all games for a sport/season from ESPN. Returns list of game dicts."""
    start, end = _season_dates(sport, season_year)
    all_games = []
    collector = ESPNCollector()

    try:
        current = start
        while current <= end:
            date_str = current.strftime("%Y%m%d")
            try:
                games = await collector.fetch_scoreboard(sport, date_str)
                all_games.extend(games)
                if games:
                    logger.info(f"  {current}: {len(games)} games")
            except Exception as e:
                logger.warning(f"  {current}: fetch failed: {e}")
            current += timedelta(days=1)
            await asyncio.sleep(rate_limit)
    finally:
        await collector.close()

    logger.info(f"Fetched {len(all_games)} total {sport} games for {season_year}-{season_year + 1}")
    return all_games


def store_games(session: Session, sport: str, season_year: int, games: list[dict]) -> int:
    """Store fetched games into the database. Returns count of new games added."""
    season_label = f"{season_year}-{str(season_year + 1)[-2:]}"
    team_cache: dict[str, int] = {}
    count = 0

    for g in games:
        home_abbr = g["home_team"]
        away_abbr = g["away_team"]

        home_id = _ensure_team(session, team_cache, home_abbr, g["home_team_name"], sport)
        away_id = _ensure_team(session, team_cache, away_abbr, g["away_team_name"], sport)

        # Skip duplicates by checking espn_id-like uniqueness (date + teams)
        existing = session.query(Game).filter(
            Game.sport == sport,
            Game.date == _parse_date(g["date"]),
            Game.home_team_id == home_id,
            Game.away_team_id == away_id,
        ).first()
        if existing:
            # Update score/status if game is now final
            if g["status"] == "final" and existing.status != "final":
                existing.home_score = g["home_score"]
                existing.away_score = g["away_score"]
                existing.status = g["status"]
            continue

        game = Game(
            sport=sport, season=season_label, date=_parse_date(g["date"]),
            home_team_id=home_id, away_team_id=away_id,
            home_score=g["home_score"], away_score=g["away_score"],
            status=g["status"],
        )
        session.add(game)
        count += 1

    session.commit()
    logger.info(f"Stored {count} new {sport} games for season {season_label}")
    return count


def compute_historical_elo(session: Session, sport: str):
    """Replay all completed games, storing each team's **pre-game** rating.

    The replay itself is not implemented here.  It is delegated to
    :func:`backend.pipeline.team_stats.backfill_elo_history` -- the function
    the daily pipeline already calls -- so that the historical and live paths
    cannot drift apart again.  They previously did: this function stored the
    *post*-game rating in the same column the live path fills with the
    *pre*-game one, which is lookahead for every consumer that reads
    ``elo_history[(team_id, game_id)]`` as a feature for predicting that game.

    Delegation also inherits two guards this function never had: it skips
    games already present in the history instead of appending duplicate rows,
    and it refuses combat sports, whose history is owned by the grader with
    post-game semantics.

    Unlike the delegate, this function does persist current ratings to
    ``EloRating``, which is what callers of the backtesting path expect.
    Commits.
    """
    from backend.pipeline.team_stats import backfill_elo_history

    result = backfill_elo_history(session, sport)
    final_ratings: dict = result["final_ratings"]  # type: ignore[assignment]

    # Save final ratings to DB
    for team_abbr, rating in final_ratings.items():
        team = session.query(Team).filter(Team.abbreviation == team_abbr, Team.sport == sport).first()
        if not team:
            continue
        existing = session.query(EloRating).filter(EloRating.team_id == team.id, EloRating.sport == sport).first()
        if existing:
            existing.rating = rating
            existing.updated_at = datetime.now(tz=timezone.utc)
        else:
            session.add(EloRating(team_id=team.id, sport=sport, rating=rating, updated_at=datetime.now(tz=timezone.utc)))

    session.commit()
    logger.info(f"Computed ELO ratings for {len(final_ratings)} {sport} teams")


async def load_historical_data(session: Session, sport: str, seasons: list[int], rate_limit: float = 1.0):
    """Full pipeline: fetch games for multiple seasons, store, compute ELO."""
    for year in seasons:
        logger.info(f"Loading {sport} season {year}-{year + 1}...")
        games = await fetch_season_games(sport, year, rate_limit)
        store_games(session, sport, year, games)

    compute_historical_elo(session, sport)
    logger.info(f"Historical data load complete for {sport}")


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


def _parse_date(date_str: str) -> date:
    """Parse ESPN date format (ISO 8601) to a date object."""
    return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
