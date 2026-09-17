"""``_check_lookahead_spot`` must compare Elo on ONE basis (plan 008 review 2).

The function receives ``team_elo`` from ``home_stats.elo_rating``, which since
``8189510`` comes from ``_team_elo`` and is therefore on the replayed
``elo_history`` basis. It then fetched the next opponent's rating straight from
``EloRating``. Those two tables disagree by a mean of 53 points and up to 134 on
the backfilled production copy -- larger than the 50-point band this comparison
uses -- so the two sides of ``next_opp_elo > team_elo - 50`` were on different
bases. Before ``8189510`` both sides read ``EloRating``: wrong, but consistent.
This is the same defect that commit exists to remove, displaced into a neighbour.

The fix routes the opponent lookup through ``_team_elo`` using the **predicted
game's** date and **no game id**. Both alternatives leak:

* ``next_game.date`` would admit ``elo_history`` rows from games *between* the
  predicted game and the next one -- ratings that do not exist yet at
  prediction time;
* ``next_game.id`` would hit ``_team_elo``'s exact-``game_id`` match during
  historical replay and return ``next_game``'s own pre-game rating, which is
  likewise after ``game_date``.

``test_lookahead_follows_the_history_basis_not_elo_ratings`` is the mutation
target: restoring the raw ``EloRating`` query makes it fail.
"""
from datetime import date

import pytest

from backend.models import Base, Team, Game, EloRating, EloHistory
from backend.pipeline.pick_generator import _check_lookahead_spot


GAME_DATE = date(2024, 1, 10)


@pytest.fixture
def lookahead_setup(db_session):
    """A mismatch today, a tough opponent in 3 days, and two disagreeing tables.

    Next opponent ``C`` is seeded with a pre-game history rating of 1580.0 and
    an ``EloRating`` of 1400.0. Against ``team_elo=1600.0`` the threshold is
    ``1600 - 50 = 1550``, so the two tables give opposite answers: the history
    basis says lookahead spot, the stale table says no.
    """
    Base.metadata.create_all(db_session.get_bind())
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    c = Team(name="Gamma", abbreviation="GAM", sport="nba")
    db_session.add_all([a, b, c])
    db_session.commit()

    # A game in C's past, carrying C's pre-game history rating.
    past = Game(sport="nba", season="2024-2025", date=date(2024, 1, 4),
                home_team_id=c.id, away_team_id=b.id,
                home_score=99, away_score=90, status="final")
    # The game being predicted: A heavily favoured over B.
    today = Game(sport="nba", season="2024-2025", date=GAME_DATE,
                 home_team_id=a.id, away_team_id=b.id, status="scheduled")
    # A's next game, 3 days out, inside the 4-day nba window.
    nxt = Game(sport="nba", season="2024-2025", date=date(2024, 1, 13),
               home_team_id=a.id, away_team_id=c.id, status="scheduled")
    db_session.add_all([past, today, nxt])
    db_session.commit()

    db_session.add_all([
        EloHistory(team_id=c.id, game_id=past.id, sport="nba", rating=1580.0),
        EloRating(team_id=c.id, sport="nba", rating=1400.0),
    ])
    db_session.commit()
    return db_session, a, b, c, today, nxt, past


def test_lookahead_follows_the_history_basis_not_elo_ratings(lookahead_setup):
    """MUTATION TARGET.

    ``team_elo`` is on the history basis, so the opponent must be too. C's
    history rating (1580.0) clears ``team_elo - 50`` (1550.0); C's stale
    ``EloRating`` (1400.0) does not. The answer must be True.
    """
    session, a, b, _c, _today, _nxt, _past = lookahead_setup

    assert _check_lookahead_spot(session, a.id, b.id, GAME_DATE, "nba",
                                 team_elo=1600.0, opponent_elo=1400.0) is True, (
        "the next opponent's strength must be read on the same basis as "
        "team_elo, not from the stale EloRating table"
    )


def test_the_lookahead_probe_does_not_reach_past_the_predicted_game(lookahead_setup):
    """The opponent's rating is as known on ``game_date``, not on the next game's.

    A history row for C dated *after* the predicted game must not be visible:
    at prediction time it does not exist. Here that later row (1000.0) would
    flip the answer to False if it were reached.
    """
    session, a, b, c, _today, _nxt, _past = lookahead_setup
    between = Game(sport="nba", season="2024-2025", date=date(2024, 1, 12),
                   home_team_id=c.id, away_team_id=b.id,
                   home_score=80, away_score=95, status="final")
    session.add(between)
    session.commit()
    session.add(EloHistory(team_id=c.id, game_id=between.id, sport="nba",
                           rating=1000.0))
    session.commit()

    assert _check_lookahead_spot(session, a.id, b.id, GAME_DATE, "nba",
                                 team_elo=1600.0, opponent_elo=1400.0) is True, (
        "a rating from between the predicted game and the next game is "
        "lookahead; it must not be reachable"
    )


def test_an_unknown_next_opponent_is_not_a_lookahead_spot(db_session):
    """No rating source for the next opponent must stay False, not default to 1500.

    ``_team_elo`` returns 1500.0 when nothing is found, and ``1500.0 > 1600-50``
    is False here but True for any team_elo below 1550 -- so letting the default
    carry this would invent an opponent strength from nothing. The lookup is
    kept None-aware so the original ``return False`` survives.
    """
    Base.metadata.create_all(db_session.get_bind())
    a = Team(name="Alpha", abbreviation="ALP", sport="nba")
    b = Team(name="Beta", abbreviation="BET", sport="nba")
    c = Team(name="Gamma", abbreviation="GAM", sport="nba")
    db_session.add_all([a, b, c])
    db_session.commit()
    today = Game(sport="nba", season="2024-2025", date=GAME_DATE,
                 home_team_id=a.id, away_team_id=b.id, status="scheduled")
    nxt = Game(sport="nba", season="2024-2025", date=date(2024, 1, 13),
               home_team_id=a.id, away_team_id=c.id, status="scheduled")
    db_session.add_all([today, nxt])
    db_session.commit()

    # team_elo below 1550, so a 1500.0 default WOULD flip this to True.
    assert _check_lookahead_spot(db_session, a.id, b.id, GAME_DATE, "nba",
                                 team_elo=1500.0, opponent_elo=1350.0) is False, (
        "an opponent with no rating anywhere must not be treated as strong"
    )


def test_a_weak_next_opponent_is_still_not_a_lookahead_spot(lookahead_setup):
    """The fix must not make the function answer True unconditionally."""
    session, a, b, _c, _today, _nxt, _past = lookahead_setup

    # C's history rating is 1580.0; against team_elo 1900 the bar is 1850.
    assert _check_lookahead_spot(session, a.id, b.id, GAME_DATE, "nba",
                                 team_elo=1900.0, opponent_elo=1400.0) is False


def test_a_close_game_today_short_circuits_before_any_elo_lookup(lookahead_setup):
    """``elo_diff < 100`` still returns False without consulting the opponent."""
    session, a, b, _c, _today, _nxt, _past = lookahead_setup

    assert _check_lookahead_spot(session, a.id, b.id, GAME_DATE, "nba",
                                 team_elo=1600.0, opponent_elo=1550.0) is False
