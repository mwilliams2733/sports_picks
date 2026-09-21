"""Games marked canceled that were actually played.

`_reconcile_against_espn` marks a game canceled when ESPN's list for that
exact date does not contain the team pair. It is the right idea, but the
lookup it trusts is narrower than the schedule:

* `ESPNCollector.fetch_scoreboard` sends only `dates`. For ncaab that
  returned **2 events for 2026-03-15**, a conference championship Sunday;
  with `groups=50` (Division I) the same call returns the games.
* ESPN files some events on the neighbouring date -- the same UTC/Eastern
  convention `espn_box_score.resolve_espn_event` already compensates for
  with a +/-1 day window. The reconciler compares one date only.

Result on 2026-09-20: of 24 games marked canceled, **15 had actually been
played and were final on ESPN**, holding 18 ungraded picks. Voiding those
picks would have recorded a push for games with real winners.

Only one candidate was genuinely absent: `PUR vs Queens University
Royals`, which no ESPN date in the window knows about.

Restoring, not voiding
----------------------
A game ESPN reports as final is restored with its score and event id, and
its picks grade normally afterwards. `_reconcile_against_espn` skips rows
whose status is `final`, so a restored game cannot be re-canceled by the
next run.

The refusals matter more than the restorations: this writes scores that
grade real money, so anything the lookup cannot confirm exactly is left
alone.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import Base, Game, Team
from backend.scripts.restore_miscanceled_games import (EspnResult,
                                                       miscanceled,
                                                       run_on_session)

DAY = datetime.date(2026, 3, 15)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _team(session, abbr, sport="ncaab"):
    t = (session.query(Team)
         .filter(Team.sport == sport, Team.abbreviation == abbr).first())
    if t:
        return t
    t = Team(name=abbr, abbreviation=abbr, sport=sport)
    session.add(t)
    session.flush()
    return t


def _game(session, home, away, *, sport="ncaab", status="canceled",
          day=DAY, home_score=None, away_score=None):
    g = Game(sport=sport, date=day, season="2026", status=status,
             home_team_id=_team(session, home, sport).id,
             away_team_id=_team(session, away, sport).id,
             home_score=home_score, away_score=away_score)
    session.add(g)
    session.flush()
    return g


def _lookup(mapping):
    """A stand-in for the ESPN call, keyed on our game id."""
    return lambda game, home_abbr, away_abbr: mapping.get(game.id)


def test_a_canceled_game_espn_calls_final_is_restored(session):
    g = _game(session, "YALE", "PENN")
    session.commit()

    summary = run_on_session(session, apply=True, lookup=_lookup({
        g.id: EspnResult(event_id="401851437", final=True,
                         scores={"YALE": 84, "PENN": 88})}))

    assert summary["restored"] == 1
    assert g.status == "final"
    assert g.espn_id == "401851437"


def test_scores_are_mapped_by_abbreviation_not_by_espn_order(session):
    """ESPN lists competitors in its own order, and the home side is not
    reliably first. Reading positionally records the game backwards, which
    grades every moneyline on it the wrong way."""
    g = _game(session, "YALE", "PENN")
    session.commit()

    run_on_session(session, apply=True, lookup=_lookup({
        g.id: EspnResult(event_id="1", final=True,
                         scores={"PENN": 88, "YALE": 84})}))

    assert (g.home_score, g.away_score) == (84, 88), \
        "YALE is our home team and scored 84, whatever order ESPN used"


def test_a_game_espn_does_not_know_is_left_canceled(session):
    """PUR vs Queens University Royals: no date in the window has it."""
    g = _game(session, "PUR", "Queens University Royals")
    session.commit()

    summary = run_on_session(session, apply=True, lookup=_lookup({}))

    assert summary["restored"] == 0
    assert summary["refused_absent"] == 1
    assert g.status == "canceled"


def test_a_game_espn_has_but_has_not_finished_is_refused(session):
    """A postponed or suspended game has no result to write. mlb CIN vs STL
    was found with a 0-0 line and must not be recorded as a nil-nil final."""
    g = _game(session, "CIN", "STL", sport="mlb")
    session.commit()

    summary = run_on_session(session, apply=True, lookup=_lookup({
        g.id: EspnResult(event_id="401815476", final=False,
                         scores={"CIN": 0, "STL": 0})}))

    assert summary["restored"] == 0
    assert summary["refused_not_final"] == 1
    assert g.status == "canceled"


def test_a_result_missing_one_of_our_teams_is_refused(session):
    """If the abbreviation we hold is not among ESPN's competitors, the
    match was loose and the orientation is unknown. Guessing writes a
    score against the wrong side."""
    g = _game(session, "USF", "WICH")
    session.commit()

    summary = run_on_session(session, apply=True, lookup=_lookup({
        g.id: EspnResult(event_id="9", final=True,
                         scores={"USF": 70, "SFLA": 55})}))

    assert summary["restored"] == 0
    assert summary["refused_unmatched"] == 1
    assert g.status == "canceled"


def test_a_canceled_game_that_already_has_a_score_is_not_a_candidate(session):
    """It was scored by something else; overwriting would discard that."""
    _game(session, "A", "B", home_score=70, away_score=60)
    session.commit()

    assert miscanceled(session) == []


def test_a_scheduled_game_is_never_touched(session):
    """Only `canceled` rows are in question. A scheduled game that has not
    happened yet is the finalizer's business, not this script's."""
    _game(session, "A", "B", status="scheduled")
    session.commit()

    assert miscanceled(session) == []


def test_a_final_game_is_never_touched(session):
    _game(session, "A", "B", status="final", home_score=1, away_score=0)
    session.commit()

    assert miscanceled(session) == []


def test_dry_run_is_the_default_and_writes_nothing(session):
    g = _game(session, "YALE", "PENN")
    session.commit()

    summary = run_on_session(session, lookup=_lookup({
        g.id: EspnResult(event_id="1", final=True,
                         scores={"YALE": 84, "PENN": 88})}))

    assert summary["restored"] == 1, "a dry run still reports what it would do"
    assert g.status == "canceled"
    assert g.home_score is None


def test_a_sport_filter_limits_what_is_considered(session):
    _game(session, "YALE", "PENN")
    _game(session, "ASU", "KU", sport="ncaaf")
    session.commit()

    assert len(miscanceled(session, sports=("ncaaf",))) == 1
    assert len(miscanceled(session)) == 2
