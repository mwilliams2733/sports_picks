"""Picks left pending on a game that is already canceled.

`voidable` only considers rows whose status is ``scheduled``, because it
exists to answer "this bout is too old for any source". A game that has
ALREADY been canceled is past that question and falls through every path:
its picks stay pending forever, and the book reports open positions it
will never settle.

21 such picks existed on 2026-09-20. 18 of them turned out to be on games
that had actually been played and were wrongly canceled -- those are
`restore_miscanceled_games`' business and must be graded, not voided. The
remaining 3 are on `PUR vs Queens University Royals`, which no ESPN date
in the window knows about.

That ordering is the point: **restore first, settle what is left.** Running
this before the restore would book a push for four games with real winners.

A canceled game carrying a SCORE is refused. A score means the game has a
result, so voiding would discard it -- the same refusal `voidable` makes.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, PickModel, PickResult, Team
from backend.scripts.void_stuck_bouts import settle_stranded, stranded_picks

DAY = datetime.date(2026, 3, 20)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _game(session, sport="ncaab", status="canceled", home_score=None,
          away_score=None):
    def team(name):
        t = Team(name=name, abbreviation=name, sport=sport)
        session.add(t)
        session.flush()
        return t

    g = Game(sport=sport, date=DAY, season="2026", status=status,
             home_team_id=team(f"H{id(session) % 997}").id,
             away_team_id=team(f"A{id(session) % 991}").id,
             home_score=home_score, away_score=away_score)
    session.add(g)
    session.flush()
    return g


def _pick(session, game, *, graded=None):
    p = PickModel(game_id=game.id, strategy_id=1, pick_type="moneyline",
                  pick_value="HOME", confidence=3, edge_pct=5.0,
                  odds_at_pick=-110)
    session.add(p)
    session.flush()
    if graded:
        session.add(PickResult(pick_id=p.id, result=graded, payout=0.9))
        session.flush()
    return p


def test_a_canceled_game_with_pending_picks_is_stranded(session):
    g = _game(session)
    _pick(session, g)
    session.commit()

    found = stranded_picks(session)

    assert [game.id for game, _ in found] == [g.id]
    assert len(found[0][1]) == 1


def test_a_canceled_game_with_no_picks_is_not_stranded(session):
    """110 voided boxing bouts are canceled and carry nothing pending.
    Reporting them as work would bury the three rows that matter."""
    _game(session, sport="boxing")
    session.commit()

    assert stranded_picks(session) == []


def test_a_canceled_game_whose_picks_are_already_settled_is_not_stranded(session):
    """The bouts voided earlier already booked their picks as push."""
    g = _game(session, sport="boxing")
    _pick(session, g, graded="push")
    session.commit()

    assert stranded_picks(session) == []


def test_a_canceled_game_carrying_a_score_is_refused(session):
    """A score is a result. Voiding it would discard a real outcome --
    and a canceled row WITH a score is exactly what the restore script
    produces if it ever runs out of order."""
    g = _game(session, home_score=70, away_score=62)
    _pick(session, g)
    session.commit()

    summary = settle_stranded(session, apply=True)

    assert summary["voided"] == 0
    assert summary["refused_scored"] == 1
    assert session.query(PickResult).count() == 0


def test_a_scheduled_game_is_not_this_functions_business(session):
    """`voidable` owns those, with an age floor derived from the odds feed."""
    g = _game(session, status="scheduled")
    _pick(session, g)
    session.commit()

    assert stranded_picks(session) == []


def test_a_final_game_is_never_stranded(session):
    """A pending pick on a final game is a GRADING gap, not a void."""
    g = _game(session, status="final", home_score=70, away_score=62)
    _pick(session, g)
    session.commit()

    assert stranded_picks(session) == []


def test_settling_books_a_push_not_a_loss(session):
    """Same settlement as every other void: stake back, excluded from the
    win-rate denominator."""
    g = _game(session)
    p = _pick(session, g)
    session.commit()
    pick_id = p.id

    summary = settle_stranded(session, apply=True)

    assert summary["voided"] == 1
    assert summary["picks_settled"] == 1
    r = session.query(PickResult).filter_by(pick_id=pick_id).one()
    assert (r.result, r.payout) == ("push", 0.0)


def test_dry_run_is_the_default_and_writes_nothing(session):
    g = _game(session)
    _pick(session, g)
    session.commit()

    summary = settle_stranded(session)

    assert summary["voided"] == 1
    assert session.query(PickResult).count() == 0


def test_a_sport_filter_limits_what_is_settled(session):
    g1 = _game(session, sport="ncaab")
    _pick(session, g1)
    g2 = _game(session, sport="ncaaf")
    _pick(session, g2)
    session.commit()

    assert len(stranded_picks(session, sports=("ncaab",))) == 1
    assert len(stranded_picks(session)) == 2
