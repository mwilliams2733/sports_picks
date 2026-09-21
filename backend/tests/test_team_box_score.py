"""Team possessions, the input three model features have always lacked.

`team_stats.py` says so in its own docstring: `offensive_rating`,
`defensive_rating` and `pace` "all require possessions. Nothing in this
repository fetches possessions... They are therefore **not emitted at
all**: no value, no default, no proxy." The consumers' `or 100.0` defaults
stood in, so every game presented the same constant and the model could
only ever learn a zero coefficient for all three.

That refusal was right. This closes the hole it named rather than working
around it.

Possessions, Dean Oliver's formula:

    POSS = FGA - OREB + TOV + 0.44 * FTA

Every term is in ESPN's `boxscore.teams` block, which arrives in the SAME
summary payload `parse_box_score` already reads for player lines. Storing
it costs no extra request for any game collected from here on.

Why a separate table
--------------------
`TeamStat` holds **pre-game features**, computed strictly before the game
they hang off. A box score is the opposite: a fact ABOUT that game.
Putting a post-game measurement in the point-in-time table is how a
lookahead gets written by someone reading `TeamStat(game_id=G)` and
assuming it predates G. `team_box_scores` is post-game by name, the same
separation `player_stats.stat_type='game_log'` already uses.

Minutes, not assumed
--------------------
Pace is possessions per 48 minutes, so an overtime game is not comparable
to a regulation one. Team minutes are summed from the player block (240
for regulation NBA, 265 with one OT) rather than assumed, and a game whose
minutes cannot be determined stores None instead of a guess.
"""
import pytest

from backend.collectors.espn_box_score import parse_team_box


def _team_block(abbr, **stats):
    defaults = {
        "fieldGoalsMade-fieldGoalsAttempted": "31-87",
        "offensiveRebounds": "13",
        "turnovers": "10",
        "totalTurnovers": "14",
        "freeThrowsMade-freeThrowsAttempted": "20-28",
    }
    defaults.update(stats)
    return {
        "team": {"abbreviation": abbr},
        "statistics": [{"name": k, "displayValue": v} for k, v in defaults.items()],
    }


def _summary(*blocks, players=None):
    box = {"teams": list(blocks)}
    if players is not None:
        box["players"] = players
    return {"boxscore": box}


def test_possessions_use_the_attempted_half_of_the_made_attempted_pair():
    """FG arrives as "31-87". The attempts drive possessions; the makes are
    already in the score."""
    rows = parse_team_box(_summary(_team_block("NY")))

    assert len(rows) == 1
    # 87 - 13 + 14 + 0.44*28 = 100.32
    assert rows[0]["fga"] == 87
    assert rows[0]["oreb"] == 13
    assert rows[0]["fta"] == 28
    assert rows[0]["possessions"] == pytest.approx(100.32, abs=0.01)


def test_total_turnovers_is_preferred_over_the_player_attributed_count():
    """ESPN reports `turnovers` (10, charged to players) and
    `totalTurnovers` (14, including team turnovers). A possession ends on
    either, so the total is the right one."""
    rows = parse_team_box(_summary(_team_block("NY")))

    assert rows[0]["turnovers"] == 14


def test_a_block_missing_total_turnovers_falls_back_to_the_player_count():
    block = _team_block("NY")
    block["statistics"] = [s for s in block["statistics"]
                           if s["name"] != "totalTurnovers"]

    rows = parse_team_box(_summary(block))

    assert rows[0]["turnovers"] == 10


def test_both_teams_are_returned_with_their_own_totals():
    rows = parse_team_box(_summary(
        _team_block("NY"),
        _team_block("SA", **{"fieldGoalsMade-fieldGoalsAttempted": "40-90",
                             "offensiveRebounds": "8"})))

    by = {r["team_abbr"]: r for r in rows}
    assert by["NY"]["fga"] == 87
    assert by["SA"]["fga"] == 90
    assert by["SA"]["oreb"] == 8


def test_minutes_are_summed_from_the_player_block_not_assumed():
    """240 team-minutes is regulation; 265 is one overtime. Pace is
    per-48, so the difference is not cosmetic."""
    players = [{"team": {"abbreviation": "NY"},
                "statistics": [{"keys": ["minutes"], "labels": ["MIN"],
                                "athletes": [
                                    {"athlete": {"displayName": "A"}, "stats": ["40"]},
                                    {"athlete": {"displayName": "B"}, "stats": ["35"]}]}]}]

    rows = parse_team_box(_summary(_team_block("NY"), players=players))

    assert rows[0]["minutes"] == 75.0


def test_a_game_with_no_player_minutes_stores_none_rather_than_a_guess():
    """A pace computed against an assumed 240 would be wrong for every OT
    game and there would be no way to tell which."""
    rows = parse_team_box(_summary(_team_block("NY")))

    assert rows[0]["minutes"] is None


def test_a_block_missing_a_possession_term_yields_no_possessions():
    """Three of four terms is not a possession count. Substituting zero for
    the missing one silently understates it on every affected game."""
    block = _team_block("NY")
    block["statistics"] = [s for s in block["statistics"]
                           if s["name"] != "offensiveRebounds"]

    rows = parse_team_box(_summary(block))

    assert rows[0]["possessions"] is None
    assert rows[0]["fga"] == 87, "the terms that ARE present are still kept"


def test_a_payload_with_no_teams_block_yields_nothing():
    assert parse_team_box({"boxscore": {}}) == []
    assert parse_team_box({}) == []


def test_a_football_payload_yields_nothing():
    """Possessions are a basketball construct, and football's team block
    carries none of these fields. Returning partial rows would invite a
    pace number for a sport that has no such statistic."""
    block = {"team": {"abbreviation": "CAR"},
             "statistics": [{"name": "totalYards", "displayValue": "387"},
                            {"name": "firstDowns", "displayValue": "21"}]}

    rows = parse_team_box(_summary(block))

    assert rows[0]["possessions"] is None


# --- storage --------------------------------------------------------------

import datetime  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402

from backend.collectors.espn_box_score import (collect_team_box_scores,  # noqa: E402
                                               store_team_box)
from backend.database import get_session, run_migrations  # noqa: E402
from backend.models import Base, Game, Team, TeamBoxScore  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    run_migrations(engine)
    return get_session(engine)


def _game(session, sport="nba", home_score=110, away_score=104, espn_id="1"):
    h = Team(name="New York", abbreviation="NY", sport=sport)
    a = Team(name="San Antonio", abbreviation="SA", sport=sport)
    session.add_all([h, a])
    session.flush()
    g = Game(sport=sport, date=datetime.date(2026, 6, 13), season="2026",
             status="final", home_team_id=h.id, away_team_id=a.id,
             home_score=home_score, away_score=away_score, espn_id=espn_id)
    session.add(g)
    session.flush()
    return g


def test_both_sides_are_stored_against_our_own_team_ids(session):
    g = _game(session)
    payload = _summary(_team_block("NY"), _team_block("SA"))

    assert store_team_box(session, g, payload) == 2
    session.commit()

    rows = session.query(TeamBoxScore).all()
    assert {r.team_id for r in rows} == {g.home_team_id, g.away_team_id}


def test_points_come_from_the_game_not_the_box(session):
    """The score is already authoritative on `Game`. Re-deriving it from
    made field goals would disagree with what grading used."""
    g = _game(session, home_score=110, away_score=104)

    store_team_box(session, g, _summary(_team_block("NY"), _team_block("SA")))
    session.commit()

    by = {r.team_id: r for r in session.query(TeamBoxScore)}
    assert by[g.home_team_id].points == 110
    assert by[g.away_team_id].points == 104


def test_a_team_block_that_is_not_ours_is_skipped_not_misattributed(session):
    """Attributing a box score to the wrong team corrupts every rating
    derived from it."""
    g = _game(session)

    written = store_team_box(session, g, _summary(_team_block("NY"),
                                                  _team_block("XXX")))
    session.commit()

    assert written == 1
    assert session.query(TeamBoxScore).count() == 1


def test_storing_twice_updates_rather_than_duplicates(session):
    g = _game(session)
    payload = _summary(_team_block("NY"))

    store_team_box(session, g, payload)
    session.commit()
    store_team_box(session, g, _summary(
        _team_block("NY", **{"fieldGoalsMade-fieldGoalsAttempted": "40-95"})))
    session.commit()

    rows = session.query(TeamBoxScore).all()
    assert len(rows) == 1
    assert rows[0].fga == 95, "a re-run converges instead of duplicating"


def test_a_game_already_collected_is_skipped(session, monkeypatch):
    g = _game(session)
    store_team_box(session, g, _summary(_team_block("NY"), _team_block("SA")))
    session.commit()
    calls = []

    import backend.collectors.espn_box_score as mod
    monkeypatch.setattr(mod, "fetch_summary",
                        lambda *a, **k: calls.append(1) or {})

    result = collect_team_box_scores(session, "nba")

    assert calls == [], "a collected game must not be re-fetched"
    assert result["skipped"] == 1


def test_a_sport_without_possessions_is_refused_before_spending_requests(session):
    """Football has no possession construct, and a request per game to learn
    that is a request wasted against an API that throttles."""
    calls = []
    import backend.collectors.espn_box_score as mod
    monkeypatch_target = mod.fetch_summary
    mod.fetch_summary = lambda *a, **k: calls.append(1) or {}
    try:
        result = collect_team_box_scores(session, "nfl")
    finally:
        mod.fetch_summary = monkeypatch_target

    assert result["refused_sport"] is True
    assert calls == []
