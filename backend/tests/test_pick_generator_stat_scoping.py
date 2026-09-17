"""``_get_team_stats`` must be scoped to the game being predicted (plan 008).

Before this fix the lookup filtered on ``team_id`` only, so whichever game
happened to have rows in ``team_stats`` supplied stats for *every* game that
team ever played -- in production, exactly one game (1014) did, which is why
some picks carried ``recent_form`` / ``net_rating`` rationale and others did
not.

``test_stats_come_from_the_game_being_predicted`` is the mutation target:
restoring the team-only filter makes it fail.
"""
from datetime import date

import pytest

from backend.models import Base, Team, Game, TeamStat
from backend.pipeline.pick_generator import _get_team_stats


@pytest.fixture
def two_games(db_session):
    Base.metadata.create_all(db_session.get_bind())
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    db_session.add_all([a, b])
    db_session.commit()

    g1 = Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
              home_team_id=a.id, away_team_id=b.id,
              home_score=110, away_score=100, status="final")
    g2 = Game(sport="nba", season="2024-2025", date=date(2024, 1, 5),
              home_team_id=a.id, away_team_id=b.id,
              home_score=120, away_score=100, status="final")
    db_session.add_all([g1, g2])
    db_session.commit()

    db_session.add_all([
        TeamStat(team_id=a.id, game_id=g1.id, stat_type="point_diff", value=-7.0),
        TeamStat(team_id=a.id, game_id=g1.id, stat_type="rest_days", value=1.0),
        TeamStat(team_id=a.id, game_id=g2.id, stat_type="point_diff", value=13.0),
        TeamStat(team_id=a.id, game_id=g2.id, stat_type="rest_days", value=4.0),
    ])
    db_session.commit()
    return db_session, a, g1, g2


def test_stats_come_from_the_game_being_predicted(two_games):
    """Mutation target for Step 7.

    Two games for the same team carry different stat values. Asking for game 2
    must return game 2's values, not game 1's and not a blend. Restoring the
    old ``filter(TeamStat.team_id == team_id)``-only query makes this fail,
    because the unscoped query returns both games' rows and the last one wins.
    """
    session, a, g1, g2 = two_games

    s1 = _get_team_stats(session, a.id, "nba", game_id=g1.id, game_date=g1.date)
    assert s1.point_diff == pytest.approx(-7.0)
    assert s1.rest_days == 1

    s2 = _get_team_stats(session, a.id, "nba", game_id=g2.id, game_date=g2.date)
    assert s2.point_diff == pytest.approx(13.0)
    assert s2.rest_days == 4


def test_scheduled_game_without_rows_falls_back_to_the_latest_prior_game(two_games):
    """At prediction time an upcoming game usually has no rows of its own.

    Falling back to the most recent *strictly earlier* game's rows is stale by
    at most one game but can never be lookahead. Falling back to "any row for
    this team" -- the old behaviour -- could pull a future game's values.
    """
    session, a, g1, g2 = two_games
    b_id = g2.away_team_id
    upcoming = Game(sport="nba", season="2024-2025", date=date(2024, 1, 9),
                    home_team_id=a.id, away_team_id=b_id, status="scheduled")
    session.add(upcoming)
    session.commit()

    s = _get_team_stats(session, a.id, "nba",
                        game_id=upcoming.id, game_date=upcoming.date)
    assert s.point_diff == pytest.approx(13.0), "should use game 2, the latest prior"
    assert s.rest_days == 4


def test_fallback_never_reaches_forward_in_time(two_games):
    """A game earlier than every stat row must not borrow a later game's stats."""
    session, a, g1, g2 = two_games
    b_id = g2.away_team_id
    earliest = Game(sport="nba", season="2024-2025", date=date(2023, 12, 1),
                    home_team_id=a.id, away_team_id=b_id, status="scheduled")
    session.add(earliest)
    session.commit()

    s = _get_team_stats(session, a.id, "nba",
                        game_id=earliest.id, game_date=earliest.date)
    # No prior rows exist -> documented defaults, not game 1's or game 2's values.
    assert s.point_diff == pytest.approx(0.0)
    assert s.rest_days == 2


def test_unmeasured_features_still_use_their_documented_defaults(two_games):
    """point_diff/rest_days are now real; ratings and pace are still defaults,
    because nothing collects possessions."""
    session, a, _, g2 = two_games
    s = _get_team_stats(session, a.id, "nba", game_id=g2.id, game_date=g2.date)
    assert s.offensive_rating == pytest.approx(100.0)
    assert s.defensive_rating == pytest.approx(100.0)
    assert s.pace == pytest.approx(100.0)


def test_fallback_ignores_rows_for_a_game_the_team_never_played(two_games):
    """Production holds legacy rows attaching 33 teams' stats to one game.

    A team must not inherit a stat line from a game it was not in.
    """
    session, a, g1, g2 = two_games
    b_id = g2.away_team_id
    other = Team(name="Delta", abbreviation="DEL", sport="nba")
    session.add(other)
    session.commit()

    orphan_game = Game(sport="nba", season="2024-2025", date=date(2024, 1, 6),
                       home_team_id=b_id, away_team_id=other.id,
                       home_score=100, away_score=99, status="final")
    session.add(orphan_game)
    session.commit()
    # Team A is not in orphan_game, yet carries a row against it.
    session.add(TeamStat(team_id=a.id, game_id=orphan_game.id,
                         stat_type="point_diff", value=-99.0))
    session.commit()

    upcoming = Game(sport="nba", season="2024-2025", date=date(2024, 1, 9),
                    home_team_id=a.id, away_team_id=b_id, status="scheduled")
    session.add(upcoming)
    session.commit()

    s = _get_team_stats(session, a.id, "nba",
                        game_id=upcoming.id, game_date=upcoming.date)
    assert s.point_diff == pytest.approx(13.0), "must skip the orphan row"


def test_elo_comes_from_elo_history_when_the_game_has_a_row(two_games):
    """Historical replay must use the rating the team carried into the game.

    `calibrated_model` trains on `elo_history[(team_id, game_id)]`. Serving
    the current `EloRating` instead -- an end-of-history rating that already
    reflects the outcome being predicted -- is both lookahead and a train/serve
    skew. For a genuinely upcoming game there is no history row and the current
    rating IS the pre-game rating, so live behaviour is unchanged.
    """
    from backend.models import EloHistory, EloRating
    session, a, g1, g2 = two_games
    session.add(EloRating(team_id=a.id, sport="nba", rating=1700.0))
    session.add(EloHistory(team_id=a.id, game_id=g2.id, sport="nba",
                           rating=1550.0))
    session.commit()

    s2 = _get_team_stats(session, a.id, "nba", game_id=g2.id, game_date=g2.date)
    assert s2.elo_rating == pytest.approx(1550.0), "must use the pre-game row"

    # No history row for game 1 -> current rating, the documented fallback.
    s1 = _get_team_stats(session, a.id, "nba", game_id=g1.id, game_date=g1.date)
    assert s1.elo_rating == pytest.approx(1700.0)
