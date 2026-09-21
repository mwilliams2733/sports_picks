"""Post-game player box scores from ESPN, keyed on a finished Game.

Deliberately separate from ``collectors/player_stats``. That package is
player-keyed and pre-game: it answers "how has this player been doing lately",
which is a prediction feature. Grading needs the opposite -- "what did this
player actually do in that game" -- so this module is keyed on a Game and only
ever runs after one is final.

``Game.espn_id`` carries ESPN's event id since plan 014, so a game that has
one is fetched directly -- one request instead of up to four, and no date
guesswork at all.

Rows without an id fall back to searching the scoreboard by date plus team
abbreviations, over a +/-1 day window. That window exists because ESPN
timestamps in UTC while filing its scoreboard by Eastern date: of 8 sampled
final NBA games, 7 matched at offset -1 and 1 matched exactly. The order is
0, -1, +1 so an exact match always wins. Plan 014 fixed the storage side, but
rows predating it -- 336 in production, mostly ncaab and boxing -- still need
the search.
"""
import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

#: Sports with an ESPN box score. Combat sports have no such concept and are
#: absent deliberately rather than by omission.
SPORT_PATHS = {
    "nba": "basketball/nba",
    "nfl": "football/nfl",
    "ncaab": "basketball/mens-college-basketball",
    "ncaaf": "football/college-football",
}
_BASE = "https://site.api.espn.com/apis/site/v2/sports"
_TIMEOUT = 20.0

#: ESPN box-score *key* -> PlayerStat field. Keys are preferred over labels
#: because they are unambiguous across a payload: football splits its box
#: score into per-category blocks that reuse the same label for different
#: stats. ``YDS`` appears under passing, rushing, receiving, interceptions,
#: kickReturns, puntReturns and punting; ``REC`` is receptions under
#: ``receiving`` but fumbles recovered under ``fumbles``. Only keys that map
#: to a ``_STAT_FIELDS`` column appear; the rest are ignored rather than
#: stored under a guessed name.
_KEY_FIELD = {
    # basketball
    "minutes": "minutes", "points": "points", "rebounds": "rebounds",
    "assists": "assists", "steals": "steals", "blocks": "blocks",
    "turnovers": "turnovers",
    # football
    "passingYards": "pass_yards",
    "rushingYards": "rush_yards",
    "receivingYards": "rec_yards",
    "receptions": "receptions",
    # ``touchdowns`` means touchdowns SCORED, which is what
    # ``player_anytime_td`` resolves on. ``passingTouchdowns`` is deliberately
    # absent: a quarterback who throws three has not scored. Return and
    # defensive scores are absent too, because ESPN reports a pick-six under
    # BOTH ``defensiveTouchdowns`` and ``interceptionTouchdowns`` and summing
    # them would count one play twice.
    "rushingTouchdowns": "touchdowns",
    "receivingTouchdowns": "touchdowns",
}
#: Keys ESPN reports as "made-attempted"; only the made half is a stat we
#: store. "2-7" means two threes made, not two-to-seven of anything.
_KEY_MADE_ATTEMPTED = {
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted": "threes",
}

#: Fallbacks for payloads that omit ``keys``. Basketball-only by nature: a
#: label map cannot express football (see ``_KEY_FIELD``), so a football block
#: without keys yields no stats rather than wrong ones.
_LABEL_FIELD = {
    "MIN": "minutes", "PTS": "points", "REB": "rebounds", "AST": "assists",
    "STL": "steals", "BLK": "blocks", "TO": "turnovers",
}
_MADE_ATTEMPTED = {"3PT": "threes"}

#: Fields fed by more than one block, which must accumulate rather than
#: overwrite. A back who scores once rushing and once receiving scored twice.
_ADDITIVE_FIELDS = {"touchdowns"}


def resolve_espn_event(sport: str, game_date: date, home_abbr: str,
                       away_abbr: str, client: httpx.Client | None = None) -> str | None:
    """The ESPN event id for this game, or ``None`` if no day in the window matches.

    Returns ``None`` rather than a best guess. Grading against the wrong game
    produces a confident wrong result, which is strictly worse than leaving the
    pick ungraded.
    """
    path = SPORT_PATHS.get(sport)
    if path is None:
        return None

    owns_client = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        for delta in (0, -1, 1):
            stamp = (game_date + timedelta(days=delta)).strftime("%Y%m%d")
            try:
                resp = client.get(f"{_BASE}/{path}/scoreboard", params={"dates": stamp})
                resp.raise_for_status()
                events = resp.json().get("events", [])
            except Exception as exc:
                logger.warning("ESPN scoreboard %s failed: %s", stamp, exc)
                continue
            for event in events:
                name = event.get("shortName", "")
                if home_abbr in name and away_abbr in name:
                    return str(event.get("id"))
        return None
    finally:
        if owns_client:
            client.close()


def parse_box_score(summary: dict) -> list[dict]:
    """Player rows from an ESPN ``summary`` payload.

    Players who did not play are omitted entirely rather than zero-filled: a
    stored zero is a measurement, and grading an Under against a player who
    never appeared would record a confident wrong win.

    Labels are read positionally against each block's own ``labels`` list
    rather than by fixed index, so a sport or season that reorders them does
    not silently grade every prop against the wrong column.
    """
    merged: dict[tuple, dict] = {}
    for block in summary.get("boxscore", {}).get("players", []):
        # ESPN puts the team on the block, not the athlete. Carry it down
        # rather than inferring from block order, which is undocumented and
        # would mislabel an entire team if it ever changed.
        team_abbr = (block.get("team") or {}).get("abbreviation")
        for stat_block in block.get("statistics", []):
            names, field_map, made_map = _naming(stat_block)
            if not names:
                continue
            for entry in stat_block.get("athletes", []):
                values = entry.get("stats") or []
                if entry.get("didNotPlay") or len(values) != len(names):
                    continue
                player = (entry.get("athlete") or {}).get("displayName", "")
                if not player:
                    continue
                # One player, one game, one row. Football lists a back who
                # runs and catches under both `rushing` and `receiving`, and
                # `store_stats` upserts on
                # (player_name, sport, stat_type, game_date) -- so emitting a
                # row per block would write two rows that collapse into one
                # anyway, with `touchdowns` overwritten instead of summed.
                row = merged.setdefault(
                    (team_abbr, player),
                    {"player_name": player, "team_abbr": team_abbr})
                for name, raw in zip(names, values):
                    field = field_map.get(name)
                    if field is not None:
                        _put(row, field, raw)
                        continue
                    made_field = made_map.get(name)
                    if made_field is not None:
                        _put(row, made_field, str(raw).split("-")[0])
    return list(merged.values())


def _naming(stat_block: dict) -> tuple[list, dict, dict]:
    """The column names for one block, and the maps that read them.

    Prefers ``keys``, which are unique across the whole payload. Falls back
    to ``labels`` only for a block with no category ``name`` -- that is the
    basketball shape, where a label IS unambiguous. A *named* block is a
    football category, where labels collide (``YDS`` in seven of them), so a
    football block that arrived without keys yields nothing rather than
    plausible-looking wrong numbers.

    ``keys`` is trusted only when it is the same length as the values it
    describes; a mismatched array would read every column shifted.
    """
    keys = stat_block.get("keys") or []
    labels = stat_block.get("labels") or []
    if keys and _fits(stat_block, keys):
        return list(keys), _KEY_FIELD, _KEY_MADE_ATTEMPTED
    if stat_block.get("name") is None:
        return list(labels), _LABEL_FIELD, _MADE_ATTEMPTED
    return [], {}, {}


def _fits(stat_block: dict, names: list) -> bool:
    """Whether ``names`` lines up with the block's athlete rows."""
    for entry in stat_block.get("athletes", []):
        values = entry.get("stats") or []
        if values:
            return len(values) == len(names)
    return True


def _put(row: dict, field: str, raw) -> None:
    """Store one parsed value, accumulating the fields fed by several blocks."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return
    if field in _ADDITIVE_FIELDS:
        row[field] = row.get(field, 0.0) + value
    else:
        row[field] = value


def fetch_summary(sport: str, event_id: str,
                  client: httpx.Client | None = None) -> dict:
    """The raw ESPN ``summary`` payload for one event, or ``{}`` on failure."""
    path = SPORT_PATHS.get(sport)
    if path is None:
        return {}
    owns_client = client is None
    client = client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.get(f"{_BASE}/{path}/summary", params={"event": event_id})
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("ESPN summary %s failed: %s", event_id, exc)
        return {}
    finally:
        if owns_client:
            client.close()


def collect_box_scores_for_final_games(session, sport: str | None = None) -> int:
    """Store post-game player lines for final games that do not have them yet.

    Returns the number of ``PlayerStat`` rows written.

    **This commits, unlike most of ``backend.pipeline``**, because
    ``PlayerStatsCollector.store_stats`` commits internally. That is the right
    behaviour here -- a run interrupted after twenty games keeps those twenty,
    and the per-game skip below means the next run resumes from where the data
    actually is -- but a caller must not assume it can roll the whole
    collection back.

    Resumable by construction: each game is asked individually whether it
    already has ``game_log`` rows, so a run that stopped halfway, or a hole
    punched in the middle of the history, is filled correctly on the next run.
    Nothing tracks how far the work got.
    """
    from backend.collectors.player_stats.collector import PlayerStatsCollector
    from backend.models import Game, PlayerStat, Team

    query = session.query(Game).filter(
        Game.status == "final",
        Game.home_score.isnot(None),
        Game.away_score.isnot(None),
    )
    if sport is not None:
        query = query.filter(Game.sport == sport)
    games = [g for g in query.order_by(Game.date).all() if g.sport in SPORT_PATHS]

    # ``store_stats`` owns name normalisation and the upsert on
    # (player_name, sport, stat_type, game_date). Reused rather than
    # reimplemented so the two write paths cannot drift; it touches no
    # fallback chain, so an empty collector is a legitimate way to reach it.
    collector = PlayerStatsCollector({})
    written = 0

    for game in games:
        home = session.get(Team, game.home_team_id)
        away = session.get(Team, game.away_team_id)
        if not home or not away:
            continue

        # Per GAME, not per date. Checking only (sport, date) meant that on any
        # date with more than one game only the first was ever collected -- on
        # production that was 176 of 1248 final games, and it is why a game's
        # props could not be graded when another game shared its date.
        # PlayerStat has no game_id, but two games on one date have different
        # teams, so team_id is what distinguishes them.
        already = (
            session.query(PlayerStat.id)
            .filter(PlayerStat.stat_type == "game_log",
                    PlayerStat.sport == game.sport,
                    PlayerStat.game_date == game.date,
                    PlayerStat.team_id.in_((home.id, away.id)))
            .first()
        )
        if already is not None:
            continue

        # Prefer the stored id: exact, and one request instead of up to four.
        event_id = game.espn_id
        if event_id is None:
            event_id = resolve_espn_event(
                sport=game.sport, game_date=game.date,
                home_abbr=home.abbreviation, away_abbr=away.abbreviation,
            )
        if event_id is None:
            logger.info("No ESPN event for %s %s @ %s on %s",
                        game.sport, away.abbreviation, home.abbreviation, game.date)
            continue

        rows = parse_box_score(fetch_summary(game.sport, event_id))
        if not rows:
            continue

        by_abbr = {home.abbreviation: home.id, away.abbreviation: away.id}
        for row in rows:
            team_id = by_abbr.get(row.get("team_abbr"))
            if team_id is None:
                # A block we cannot tie to either side of OUR game. Skipping is
                # the only honest option: PlayerStat.team_id is NOT NULL, and
                # defaulting it would attribute a player to a team they did not
                # play for.
                continue
            # OUR game's date, never ESPN's. grade_prop_pick looks up
            # game_date=game.date; ESPN's date is usually a day earlier, so
            # storing it writes rows that are correct and permanently
            # invisible to grading.
            row["game_date"] = game.date.strftime("%Y-%m-%d")
            written += collector.store_stats(
                session, [row], "game_log", team_id, game.sport, "espn"
            )

    return written
