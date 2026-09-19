import datetime

import pytest

from backend.collectors.espn_box_score import resolve_espn_event

_SB = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"


def _scoreboard(*short_names):
    return {"events": [{"id": f"40{i}", "shortName": n}
                       for i, n in enumerate(short_names)]}


def test_resolves_an_event_listed_on_the_previous_espn_day(httpx_mock):
    """Our Game.date runs a day ahead of ESPN's for most games.

    Of 8 sampled final NBA games, 7 matched at ESPN offset -1 and 1 matched
    exactly. Searching the exact date alone finds about one game in eight, and
    the misses look like missing data rather than a UTC/ET offset.
    """
    httpx_mock.add_response(url=f"{_SB}?dates=20251231", json=_scoreboard("GS @ CHA"))
    httpx_mock.add_response(url=f"{_SB}?dates=20251230", json=_scoreboard("MIN @ LAL"))

    event_id = resolve_espn_event(
        sport="nba", game_date=datetime.date(2025, 12, 31),
        home_abbr="LAL", away_abbr="MIN",
    )
    assert event_id == "400"


def test_an_exact_date_match_wins_over_a_neighbouring_day(httpx_mock):
    """The search order is 0, -1, +1. A team pairing that appears on both our
    date and the day before must resolve to ours, not the neighbour."""
    httpx_mock.add_response(url=f"{_SB}?dates=20251231", json=_scoreboard("MIN @ LAL"))

    event_id = resolve_espn_event(
        sport="nba", game_date=datetime.date(2025, 12, 31),
        home_abbr="LAL", away_abbr="MIN",
    )
    assert event_id == "400"


def test_returns_none_rather_than_a_wrong_event_when_no_day_matches(httpx_mock):
    """A near-miss must not resolve. Grading against the wrong game is worse
    than not grading: it produces a real, confident, wrong result."""
    for stamp in ("20251231", "20251230", "20260101"):
        httpx_mock.add_response(url=f"{_SB}?dates={stamp}", json=_scoreboard("BOS @ NY"))

    assert resolve_espn_event(
        sport="nba", game_date=datetime.date(2025, 12, 31),
        home_abbr="LAL", away_abbr="MIN",
    ) is None


def test_an_unsupported_sport_resolves_to_none_without_calling_espn(httpx_mock):
    """No registered responses: if this made a request, pytest-httpx would
    fail the test. Grading combat sports has no box-score concept."""
    assert resolve_espn_event(
        sport="mma", game_date=datetime.date(2025, 12, 31),
        home_abbr="A", away_abbr="B",
    ) is None


# --- box score parsing ------------------------------------------------------

from backend.collectors.espn_box_score import parse_box_score  # noqa: E402

_LABELS = ["MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK",
           "OREB", "DREB", "PF", "+/-"]


def _summary(*athletes, team="OKC"):
    return {"boxscore": {"players": [
        {"team": {"abbreviation": team},
         "statistics": [{"labels": _LABELS, "athletes": list(athletes)}]}
    ]}}


def test_threes_come_from_the_made_half_of_the_made_attempted_pair():
    """ESPN reports 3PT as "made-attempted" ("2-7"). Storing the raw string, or
    the attempted count, silently grades every threes prop against the wrong
    number."""
    rows = parse_box_score(_summary({
        "athlete": {"displayName": "Chet Holmgren"},
        "stats": ["26", "10", "3-8", "2-7", "4-6", "9", "2", "3", "1", "0",
                  "3", "6", "2", "+2"],
    }))
    assert len(rows) == 1
    assert rows[0]["threes"] == 2.0
    assert rows[0]["points"] == 10.0
    assert rows[0]["rebounds"] == 9.0
    assert rows[0]["assists"] == 2.0
    assert rows[0]["turnovers"] == 3.0
    assert rows[0]["steals"] == 1.0
    assert rows[0]["blocks"] == 0.0
    assert rows[0]["minutes"] == 26.0


def test_a_player_who_did_not_play_produces_no_row_at_all():
    """ESPN gives DNP players `stats: []` and `didNotPlay: true`.

    Zero-filling them is not a harmless default: a stored 0 rebounds makes
    "Under 1.5 Rebounds" grade as a WIN for a player who never took the court.
    An absent row leaves the pick ungraded, which is the honest outcome.
    """
    rows = parse_box_score(_summary({
        "athlete": {"displayName": "Bismack Biyombo"},
        "didNotPlay": True,
        "stats": [],
    }))
    assert rows == []


def test_labels_are_read_positionally_per_block_not_assumed():
    """Label order is a property of each statistics block. Hard-coding indexes
    works until a sport or a season reorders them, and then grades everything
    against the wrong column."""
    payload = {"boxscore": {"players": [{"statistics": [{
        "labels": ["PTS", "MIN", "REB"],
        "athletes": [{"athlete": {"displayName": "X"}, "stats": ["11", "30", "4"]}],
    }]}]}}
    rows = parse_box_score(payload)
    assert rows[0]["points"] == 11.0
    assert rows[0]["minutes"] == 30.0


def test_unmapped_labels_are_dropped_rather_than_stored_under_a_guess():
    """FG, FT, OREB, DREB, PF and +/- have no PlayerStat column. They must not
    be coerced into one that looks close."""
    rows = parse_box_score(_summary({
        "athlete": {"displayName": "Chet Holmgren"},
        "stats": ["26", "10", "3-8", "2-7", "4-6", "9", "2", "3", "1", "0",
                  "3", "6", "2", "+2"],
    }))
    assert set(rows[0]) == {
        "player_name", "team_abbr", "minutes", "points", "threes", "rebounds",
        "assists", "turnovers", "steals", "blocks",
    }


def test_each_row_carries_the_team_block_it_came_from():
    """`PlayerStat.team_id` is NOT NULL, so the collector has to know which of
    our teams a player belongs to. ESPN puts it on the block, not the athlete,
    so it has to be carried down per block rather than inferred from order --
    block order is not documented and guessing it mislabels a whole team."""
    payload = {"boxscore": {"players": [
        {"team": {"abbreviation": "OKC"},
         "statistics": [{"labels": ["PTS"],
                         "athletes": [{"athlete": {"displayName": "A"}, "stats": ["10"]}]}]},
        {"team": {"abbreviation": "SA"},
         "statistics": [{"labels": ["PTS"],
                         "athletes": [{"athlete": {"displayName": "B"}, "stats": ["20"]}]}]},
    ]}}
    rows = parse_box_score(payload)
    assert {r["player_name"]: r["team_abbr"] for r in rows} == {"A": "OKC", "B": "SA"}


# --- storage ----------------------------------------------------------------

from backend.collectors import espn_box_score as ebs  # noqa: E402
from backend.models import Base, Game, PlayerStat, Team  # noqa: E402


def _seed_final_game(session, game_date=datetime.date(2025, 12, 31)):
    Base.metadata.create_all(session.get_bind())
    home = Team(id=1, name="Oklahoma City Thunder", abbreviation="OKC", sport="nba")
    away = Team(id=2, name="San Antonio Spurs", abbreviation="SA", sport="nba")
    session.add_all([home, away])
    session.flush()
    game = Game(sport="nba", season="2025-26", date=game_date,
                home_team_id=1, away_team_id=2,
                home_score=110, away_score=105, status="final")
    session.add(game)
    session.commit()
    return game


def test_rows_are_stored_against_OUR_game_date_not_espns(db_session, monkeypatch):
    """`grade_prop_pick` is looked up with `game_date=game.date` -- OUR date.

    ESPN's date for the same game is usually a day earlier. Storing ESPN's date
    writes rows that are present, correct, and permanently invisible to
    grading: nothing errors, grading just silently finds nothing, which looks
    identical to the collector never having run.
    """
    game = _seed_final_game(db_session)
    monkeypatch.setattr(ebs, "resolve_espn_event", lambda **kw: "400")
    monkeypatch.setattr(ebs, "fetch_summary", lambda *a, **k: {"boxscore": {"players": [
        {"team": {"abbreviation": "OKC"},
         "statistics": [{"labels": ["PTS", "REB"],
                         "athletes": [{"athlete": {"displayName": "Chet Holmgren"},
                                       "stats": ["10", "9"]}]}]},
    ]}})

    written = ebs.collect_box_scores_for_final_games(db_session, "nba")
    assert written == 1

    row = db_session.query(PlayerStat).filter_by(stat_type="game_log").one()
    assert row.player_name == "Chet Holmgren"
    assert row.game_date == game.date          # ours, not ESPN's
    assert row.points == 10.0
    assert row.team_id == 1


def test_a_game_whose_event_cannot_be_resolved_writes_nothing(db_session, monkeypatch):
    """Unresolvable is not an error, but it must not produce rows either."""
    _seed_final_game(db_session)
    monkeypatch.setattr(ebs, "resolve_espn_event", lambda **kw: None)
    monkeypatch.setattr(ebs, "fetch_summary",
                        lambda *a, **k: pytest.fail("must not fetch without an event"))

    assert ebs.collect_box_scores_for_final_games(db_session, "nba") == 0
    assert db_session.query(PlayerStat).count() == 0


def test_a_game_already_collected_is_skipped(db_session, monkeypatch):
    """Resumable: ask per game whether it has rows, never track how far the run
    got. A hole punched in the middle must still be filled on the next run."""
    game = _seed_final_game(db_session)
    db_session.add(PlayerStat(
        player_name="Chet Holmgren", team_id=1, sport="nba",
        stat_type="game_log", game_date=game.date, points=10.0,
        source="espn", fetched_at=datetime.datetime.now(datetime.timezone.utc)))
    db_session.commit()

    calls = []
    monkeypatch.setattr(ebs, "resolve_espn_event",
                        lambda **kw: calls.append(kw) or "400")

    assert ebs.collect_box_scores_for_final_games(db_session, "nba") == 0
    assert calls == []          # never even looked the event up


# --- use the stored event id instead of searching ----------------------------

def test_a_game_with_an_espn_id_is_collected_without_searching_the_scoreboard(
        db_session, monkeypatch):
    """Plan 014 gave every game ESPN's event id. This collector predates it and
    still calls resolve_espn_event, which requests the scoreboard on three
    dates per game -- roughly 4000 requests for a 1014-game run instead of
    1014, and it reintroduces the date guesswork the id makes unnecessary.
    """
    game = _seed_final_game(db_session)
    game.espn_id = "401700777"
    db_session.commit()

    monkeypatch.setattr(ebs, "resolve_espn_event",
                        lambda **kw: pytest.fail(
                            "searched the scoreboard despite a stored espn_id"))
    seen = {}
    monkeypatch.setattr(ebs, "fetch_summary",
                        lambda sport, event_id, **k: seen.setdefault("id", event_id) and None
                        or {"boxscore": {"players": [
                            {"team": {"abbreviation": "OKC"},
                             "statistics": [{"labels": ["PTS"],
                                             "athletes": [{"athlete": {"displayName": "P"},
                                                           "stats": ["10"]}]}]}]}})

    written = ebs.collect_box_scores_for_final_games(db_session, "nba")

    assert seen["id"] == "401700777"
    assert written == 1


def test_a_game_without_an_espn_id_still_falls_back_to_searching(
        db_session, monkeypatch):
    """Rows the backfill could not identify -- 336 in production, mostly ncaab
    and boxing -- must still be collectable."""
    _seed_final_game(db_session)          # no espn_id

    monkeypatch.setattr(ebs, "resolve_espn_event", lambda **kw: "401700888")
    seen = {}
    monkeypatch.setattr(ebs, "fetch_summary",
                        lambda sport, event_id, **k: seen.setdefault("id", event_id) and None
                        or {"boxscore": {"players": [
                            {"team": {"abbreviation": "OKC"},
                             "statistics": [{"labels": ["PTS"],
                                             "athletes": [{"athlete": {"displayName": "P"},
                                                           "stats": ["10"]}]}]}]}})

    ebs.collect_box_scores_for_final_games(db_session, "nba")

    assert seen["id"] == "401700888"


def test_a_second_game_on_the_same_date_is_still_collected(db_session, monkeypatch):
    """The resumability check was per-DATE, so on any date with more than one
    game only the first was ever collected.

    Found on production: 1248 final NBA games across 176 dates produced 3826
    rows -- about 22 per date, i.e. one game each, where the true figure is
    nearer 25,000. Game 1603's props could not be graded because its date
    already held a different game's box score.

    PlayerStat has no game_id, but it has team_id, and two games on one date
    have different teams. That is what makes the check per-game.
    """
    game = _seed_final_game(db_session)          # teams 1 and 2, OKC/SA
    # A DIFFERENT game's box score already exists on the same date.
    db_session.add_all([
        Team(id=10, name="New York Knicks", abbreviation="NY", sport="nba"),
        Team(id=11, name="Boston Celtics", abbreviation="BOS", sport="nba"),
    ])
    db_session.flush()
    db_session.add(PlayerStat(
        player_name="Jalen Brunson", team_id=10, sport="nba",
        stat_type="game_log", game_date=game.date, points=30.0,
        source="espn", fetched_at=datetime.datetime.now(datetime.timezone.utc)))
    db_session.commit()

    monkeypatch.setattr(ebs, "resolve_espn_event", lambda **kw: "400")
    monkeypatch.setattr(ebs, "fetch_summary", lambda *a, **k: {"boxscore": {"players": [
        {"team": {"abbreviation": "OKC"},
         "statistics": [{"labels": ["PTS"],
                         "athletes": [{"athlete": {"displayName": "Chet Holmgren"},
                                       "stats": ["22"]}]}]}]}})

    written = ebs.collect_box_scores_for_final_games(db_session, "nba")

    assert written == 1, "the second game on this date was skipped"
    names = {p.player_name for p in db_session.query(PlayerStat).all()}
    assert "Chet Holmgren" in names
