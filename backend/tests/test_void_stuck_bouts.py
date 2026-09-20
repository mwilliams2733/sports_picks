"""Voiding a bout no source can ever settle.

110 boxing bouts from 2026-03-20 to 2026-07-05 sit `scheduled` with 36
picks that can never be graded. ESPN has no boxing at all, and the odds
feed's /scores endpoint reaches back only 3 days, so no source this
repository has or can cheaply get will ever say who won them. They are not
pending -- they are unanswerable, and leaving them pending misreports the
book as having open positions it will never settle.

Voiding writes ``push``, not a new status word. ``push`` is already
understood everywhere that reads a result: the recalibrator does
``decided = total - pushes`` so it never enters a win-rate denominator,
and the payout is 0.0, so the stake is returned and ROI is untouched. A
new value would fall through every ``win``/``loss``/``push`` branch in
`api/stats.py`, `api/users.py` and `analysis/recalibrator.py` and be
silently miscounted.

Two refusals matter more than the writing:

* **A bout the odds feed could still settle is never voided.** The age
  floor is derived from `MAX_DAYS_FROM`, so it cannot drift out of step
  with what `finalize_combat` can actually reach.
* **A game with an already-graded pick is refused**, because voiding it
  would rewrite a recorded result.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.collectors.odds_scores import MAX_DAYS_FROM
from backend.database import get_session
from backend.models import Base, Game, PickModel, PickResult, Team
from backend.scripts.void_stuck_bouts import run_on_session, voidable

TODAY = datetime.date(2026, 9, 20)
OLD = datetime.date(2026, 3, 21)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _bout(session, day, home="Boxer A", away="Boxer B", sport="boxing"):
    def team(name):
        t = (session.query(Team)
             .filter(Team.sport == sport, Team.abbreviation == name).first())
        if t:
            return t
        t = Team(name=name, abbreviation=name, sport=sport)
        session.add(t)
        session.flush()
        return t

    g = Game(sport=sport, date=day, status="scheduled", season="2026",
             home_team_id=team(home).id, away_team_id=team(away).id)
    session.add(g)
    session.flush()
    return g


def _pick(session, game, pick_type="moneyline"):
    p = PickModel(game_id=game.id, strategy_id=1, pick_type=pick_type,
                  pick_value="HOME", confidence=3, edge_pct=5.0,
                  odds_at_pick=-110)
    session.add(p)
    session.flush()
    return p


def test_a_stuck_bout_is_marked_canceled(session):
    g = _bout(session, OLD)
    session.commit()

    summary = run_on_session(session, today=TODAY, apply=True)

    assert summary["voided"] == 1
    assert g.status == "canceled"


def test_its_picks_are_settled_as_push_with_no_payout(session):
    g = _bout(session, OLD)
    p = _pick(session, g)
    session.commit()

    run_on_session(session, today=TODAY, apply=True)

    result = session.query(PickResult).filter_by(pick_id=p.id).one()
    assert result.result == "push", (
        "a new status word would fall through every win/loss/push branch "
        "in stats, users and the recalibrator and be miscounted")
    assert result.payout == 0.0


def test_a_push_does_not_enter_the_recalibrator_denominator(session):
    """The reason `push` is the right word, asserted rather than assumed."""
    g = _bout(session, OLD)
    _pick(session, g)
    session.commit()
    run_on_session(session, today=TODAY, apply=True)

    rows = session.query(PickResult).all()
    total = len(rows)
    pushes = sum(1 for r in rows if r.result == "push")

    assert total - pushes == 0, (
        "voided picks must not count as decided trials; the recalibrator "
        "computes decided = total - pushes")


def test_dry_run_is_the_default_and_writes_nothing(session):
    g = _bout(session, OLD)
    p = _pick(session, g)
    session.commit()

    summary = run_on_session(session, today=TODAY)

    assert summary["voided"] == 1
    assert g.status == "scheduled"
    assert session.query(PickResult).filter_by(pick_id=p.id).count() == 0


def test_a_bout_the_odds_feed_could_still_settle_is_never_voided(session):
    """Inside the /scores window it is recoverable, not unanswerable."""
    recent = _bout(session, TODAY - datetime.timedelta(days=MAX_DAYS_FROM - 1))
    session.commit()

    summary = run_on_session(session, today=TODAY, apply=True)

    assert summary["voided"] == 0
    assert recent.status == "scheduled"


def test_a_game_with_a_graded_pick_is_refused(session):
    """Voiding it would rewrite a result that was actually recorded."""
    g = _bout(session, OLD)
    p = _pick(session, g)
    session.add(PickResult(pick_id=p.id, result="win", payout=0.91))
    session.commit()

    summary = run_on_session(session, today=TODAY, apply=True)

    assert summary["voided"] == 0
    assert summary["refused_graded"] == 1
    assert g.status == "scheduled"


def test_a_game_that_has_scores_is_refused(session):
    """It is finalizable, not unanswerable -- grading is the right answer."""
    g = _bout(session, OLD)
    g.home_score, g.away_score = 1, 0
    session.commit()

    summary = run_on_session(session, today=TODAY, apply=True)

    assert summary["voided"] == 0
    assert summary["refused_scored"] == 1


def test_only_the_named_sport_is_touched(session):
    boxing = _bout(session, OLD, "Boxer A", "Boxer B", sport="boxing")
    mma = _bout(session, OLD, "Fighter A", "Fighter B", sport="mma")
    session.commit()

    run_on_session(session, today=TODAY, apply=True, sport="boxing")

    assert boxing.status == "canceled"
    assert mma.status == "scheduled", "mma still has an ESPN path"


def test_voidable_uses_the_collector_reach_not_a_literal(session):
    """Derived: if /scores ever reaches further back, this follows."""
    edge = _bout(session, TODAY - datetime.timedelta(days=MAX_DAYS_FROM + 1))
    session.commit()

    assert voidable(session, "boxing", TODAY) == [edge]
