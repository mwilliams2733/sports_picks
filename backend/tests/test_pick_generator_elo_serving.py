"""Serving must read Elo on the same replay basis the model trains on (plan 008 review).

``EloRating`` -- the current-rating table -- is written for team sports at
exactly one place, ``backtesting/historical.py:compute_historical_elo``, whose
only entry point ``load_historical_data`` has no callers anywhere in the repo:
not the scheduler, not an API route. For NBA it is therefore frozen at whatever
a past manual run left behind, on a different replay basis from the
``elo_history`` that plan 008 populated. On the backfilled production copy the
two disagree by 53 points on average and up to 134, in both directions.

Every *genuinely upcoming* game has no ``EloHistory`` row of its own, so before
this fix ``_team_elo`` fell straight through to that stale table -- meaning the
model trained on replayed pre-game Elo and was served something else. This is a
train/serve skew that plan 008's own data change made worse, not better.

``test_serves_the_most_recent_prior_history_rating`` is the mutation target:
deleting the ``EloHistory``-by-date lookup makes it fail.
"""
from datetime import date

import pytest

from backend.models import Base, Team, Game, EloRating, EloHistory
from backend.pipeline.pick_generator import _team_elo


@pytest.fixture
def history_and_a_stale_rating(db_session):
    """Two played games with pre-game history, then an upcoming game with none.

    The ``EloRating`` row is seeded 300 points away from every history value so
    that a fallback to it cannot pass any assertion by coincidence, and the two
    history rows differ from each other so "most recent" is distinguishable
    from "any".
    """
    Base.metadata.create_all(db_session.get_bind())
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    db_session.add_all([a, b])
    db_session.commit()

    played_early = Game(sport="nba", season="2024-2025", date=date(2024, 1, 1),
                        home_team_id=a.id, away_team_id=b.id,
                        home_score=110, away_score=100, status="final")
    played_late = Game(sport="nba", season="2024-2025", date=date(2024, 1, 5),
                       home_team_id=a.id, away_team_id=b.id,
                       home_score=120, away_score=100, status="final")
    upcoming = Game(sport="nba", season="2024-2025", date=date(2024, 1, 9),
                    home_team_id=a.id, away_team_id=b.id, status="scheduled")
    db_session.add_all([played_early, played_late, upcoming])
    db_session.commit()

    db_session.add_all([
        EloHistory(team_id=a.id, game_id=played_early.id, sport="nba", rating=1400.0),
        EloHistory(team_id=a.id, game_id=played_late.id, sport="nba", rating=1450.0),
        # Stale, on a different replay basis, and far from both history values.
        EloRating(team_id=a.id, sport="nba", rating=1750.0),
    ])
    db_session.commit()
    return db_session, a, played_early, played_late, upcoming


def test_serves_the_most_recent_prior_history_rating(history_and_a_stale_rating):
    """MUTATION TARGET.

    An upcoming game has no ``EloHistory`` row of its own. The team does have
    earlier games that do. Serving must use the most recent of those (1450.0),
    which is the same basis the model trains on -- not the stale ``EloRating``
    (1750.0), and not the older history row (1400.0).
    """
    session, a, _early, _late, upcoming = history_and_a_stale_rating

    served = _team_elo(session, a.id, "nba", upcoming.id, upcoming.date)

    assert served == pytest.approx(1450.0), (
        "upcoming game must be served the most recent PRIOR elo_history rating, "
        "not the stale EloRating table"
    )
    assert served != pytest.approx(1750.0), "fell through to the stale EloRating"


def test_a_games_own_history_row_still_wins(history_and_a_stale_rating):
    """The exact-``game_id`` lookup keeps priority over the by-date fallback."""
    session, a, early, late, _upcoming = history_and_a_stale_rating

    assert _team_elo(session, a.id, "nba", early.id, early.date) == pytest.approx(1400.0)
    assert _team_elo(session, a.id, "nba", late.id, late.date) == pytest.approx(1450.0)


def test_fallback_never_reaches_forward_in_time(history_and_a_stale_rating):
    """A game before all history gets the current rating, never a later one.

    ``played_early`` on 2024-01-01 must not be handed the 2024-01-05 rating.
    Here it has its own row, so instead use a game that predates everything.
    """
    session, a, early, _late, _upcoming = history_and_a_stale_rating
    b = session.query(Team).filter(Team.abbreviation == "BET").one()
    earliest = Game(sport="nba", season="2023-2024", date=date(2023, 12, 1),
                    home_team_id=a.id, away_team_id=b.id, status="scheduled")
    session.add(earliest)
    session.commit()

    served = _team_elo(session, a.id, "nba", earliest.id, earliest.date)

    assert served == pytest.approx(1750.0), (
        "with no PRIOR history the EloRating fallback applies; a later history "
        "row must never be reached backwards in time"
    )


def test_team_with_no_history_at_all_falls_back_to_elo_rating(db_session):
    """The ``EloRating`` fallback is kept, last, for a team with no history."""
    Base.metadata.create_all(db_session.get_bind())
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    db_session.add_all([a, b])
    db_session.commit()
    g = Game(sport="nba", season="2024-2025", date=date(2024, 1, 9),
             home_team_id=a.id, away_team_id=b.id, status="scheduled")
    db_session.add(g)
    db_session.commit()
    db_session.add(EloRating(team_id=a.id, sport="nba", rating=1612.0))
    db_session.commit()

    assert _team_elo(db_session, a.id, "nba", g.id, g.date) == pytest.approx(1612.0)


def test_no_row_at_all_defaults_to_1500(db_session):
    """No history, no rating: the 1500.0 default, after both fallbacks."""
    Base.metadata.create_all(db_session.get_bind())
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    db_session.add_all([a, b])
    db_session.commit()
    g = Game(sport="nba", season="2024-2025", date=date(2024, 1, 9),
             home_team_id=a.id, away_team_id=b.id, status="scheduled")
    db_session.add(g)
    db_session.commit()

    assert _team_elo(db_session, a.id, "nba", g.id, g.date) == pytest.approx(1500.0)


def test_history_lookup_is_scoped_to_the_sport(history_and_a_stale_rating):
    """A history row from another sport must not leak into an nba lookup."""
    session, a, _early, _late, upcoming = history_and_a_stale_rating
    b = session.query(Team).filter(Team.abbreviation == "BET").one()
    other = Game(sport="ncaab", season="2024-2025", date=date(2024, 1, 8),
                 home_team_id=a.id, away_team_id=b.id,
                 home_score=70, away_score=60, status="final")
    session.add(other)
    session.commit()
    session.add(EloHistory(team_id=a.id, game_id=other.id, sport="ncaab",
                           rating=1234.0))
    session.commit()

    # More recent than the nba history, but a different sport.
    assert _team_elo(session, a.id, "nba", upcoming.id, upcoming.date) == pytest.approx(1450.0)
