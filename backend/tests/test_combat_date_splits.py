"""Two rows for one bout, each holding half of it.

ESPN's UFC scoreboard and the odds feed disagree about a card's date by a
day -- a Saturday-night card is 06-14 to one and 06-15 to the other. That
produced pairs where neither row is redundant:

    #1649  06-14  final      0 odds  2 elo_history
    #1296  06-15  scheduled  6 odds  0 elo_history

The ESPN-dated row has the RESULT, the odds-dated row has the MARKET.
Deleting either loses information, so these are merged rather than
deduplicated: the odds move onto the survivor and the emptied twin goes.

The survivor must be the `final` copy, because that is the row
`elo_history` points at. Keeping the other would orphan those rows and
force a second Elo replay.

Separately, a past-dated scheduled bout with a LATER-dated twin is a
reschedule, not a missed result: the fight moved and never happened on
that date. That is the only reliable way to tell it apart from a bout
that did happen and simply went unmatched -- the finalizer sees
"unmatched" for both. It explains 6 of 62 stuck rows; 53 have no twin at
all and are genuinely uncovered promotions.
"""
import datetime

import pytest
from sqlalchemy import create_engine

from backend.database import get_session
from backend.models import (Base, EloHistory, Game, Odds, PickModel,
                            PickResult, Team)
from backend.scripts.dedupe_combat_games import (merge_date_splits,
                                                 stale_reschedules,
                                                 void_reschedules)

DAY = datetime.date(2026, 6, 14)
TODAY = datetime.date(2026, 9, 20)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _team(session, name):
    t = (session.query(Team)
         .filter(Team.sport == "mma", Team.abbreviation == name).first())
    if t:
        return t
    t = Team(name=name, abbreviation=name, sport="mma")
    session.add(t)
    session.flush()
    return t


def _bout(session, day, home, away, *, home_score=None, away_score=None,
          odds=0):
    g = Game(sport="mma", date=day, season="2026",
             status="final" if home_score is not None else "scheduled",
             home_team_id=_team(session, home).id,
             away_team_id=_team(session, away).id,
             home_score=home_score, away_score=away_score)
    session.add(g)
    session.flush()
    for i in range(odds):
        session.add(Odds(game_id=g.id, bookmaker=f"bk{i}",
                         moneyline_home=-110, moneyline_away=-110))
    session.flush()
    return g


# --- merging a date split -------------------------------------------------

def test_the_final_copy_survives_and_the_odds_move_onto_it(session):
    """The final row owns elo_history; keeping the other would orphan it."""
    final = _bout(session, DAY, "Bo Nickal", "Kyle Daukaus",
                  home_score=1, away_score=0)
    session.add(EloHistory(team_id=final.home_team_id, game_id=final.id,
                           sport="mma", rating=1512.0))
    priced = _bout(session, DAY + datetime.timedelta(days=1),
                   "Bo Nickal", "Kyle Daukaus", odds=6)
    session.commit()
    final_id, priced_id = final.id, priced.id

    summary = merge_date_splits(session, "mma", apply=True)

    assert summary["merged"] == 1
    session.expire_all()
    assert session.query(Game).filter_by(id=priced_id).count() == 0
    assert session.query(Game).filter_by(id=final_id).count() == 1
    assert session.query(Odds).filter_by(game_id=final_id).count() == 6, \
        "the market must follow the surviving row"
    assert session.query(EloHistory).filter_by(game_id=final_id).count() == 1


def test_when_neither_is_final_the_priced_copy_survives(session):
    """No result to preserve, so keep the row that carries the market."""
    empty = _bout(session, DAY, "Diego Lopes", "Steve Garcia")
    priced = _bout(session, DAY + datetime.timedelta(days=1),
                   "Diego Lopes", "Steve Garcia", odds=6)
    session.commit()
    empty_id, priced_id = empty.id, priced.id

    merge_date_splits(session, "mma", apply=True)

    session.expire_all()
    assert session.query(Game).filter_by(id=priced_id).count() == 1
    assert session.query(Game).filter_by(id=empty_id).count() == 0


def test_dates_further_apart_are_not_a_split(session):
    """A months-apart pair is a reschedule, handled by the other path."""
    _bout(session, DAY, "Fighter A", "Fighter B", home_score=1, away_score=0)
    _bout(session, DAY + datetime.timedelta(days=60), "Fighter A", "Fighter B",
          odds=5)
    session.commit()

    assert merge_date_splits(session, "mma", apply=True)["merged"] == 0


def test_two_final_copies_are_refused(session):
    """Two results for one bout is not a split; it needs a human."""
    _bout(session, DAY, "Fighter A", "Fighter B", home_score=1, away_score=0)
    _bout(session, DAY + datetime.timedelta(days=1), "Fighter A", "Fighter B",
          home_score=1, away_score=0)
    session.commit()

    summary = merge_date_splits(session, "mma", apply=True)

    assert summary["merged"] == 0
    assert summary["refused"] == 1


def test_merge_dry_run_writes_nothing(session):
    _bout(session, DAY, "Bo Nickal", "Kyle Daukaus", home_score=1, away_score=0)
    priced = _bout(session, DAY + datetime.timedelta(days=1),
                   "Bo Nickal", "Kyle Daukaus", odds=6)
    session.commit()
    priced_id = priced.id

    summary = merge_date_splits(session, "mma")

    assert summary["merged"] == 1
    assert session.query(Game).filter_by(id=priced_id).count() == 1


# --- voiding a stale reschedule ------------------------------------------

def test_a_past_bout_with_a_later_twin_is_stale(session):
    old = _bout(session, datetime.date(2026, 6, 28), "Merab", "Petr Yan", odds=1)
    _bout(session, datetime.date(2026, 10, 24), "Petr Yan", "Merab", odds=5)
    session.commit()

    assert [g.id for g in stale_reschedules(session, "mma", TODAY)] == [old.id]


def test_a_past_bout_with_no_twin_is_left_alone(session):
    """53 of 62 stuck rows are these -- uncovered promotions, not moved."""
    _bout(session, datetime.date(2026, 5, 2), "Lone A", "Lone B")
    session.commit()

    assert stale_reschedules(session, "mma", TODAY) == []


def test_a_future_bout_with_a_later_twin_is_left_alone(session):
    """It has not happened yet; it cannot be stale."""
    _bout(session, datetime.date(2026, 12, 26), "Kape", "Van")
    _bout(session, datetime.date(2027, 6, 30), "Kape", "Van")
    session.commit()

    assert stale_reschedules(session, "mma", TODAY) == []


def test_voiding_settles_its_picks_as_push(session):
    """Same settlement as void_stuck_bouts: stake back, not a loss."""
    old = _bout(session, datetime.date(2026, 6, 28), "Merab", "Petr Yan")
    _bout(session, datetime.date(2026, 10, 24), "Petr Yan", "Merab")
    pick = PickModel(game_id=old.id, strategy_id=1, pick_type="moneyline",
                     pick_value="HOME", confidence=3, edge_pct=5.0,
                     odds_at_pick=-110)
    session.add(pick)
    session.flush()
    session.commit()
    pick_id = pick.id

    summary = void_reschedules(session, "mma", today=TODAY, apply=True)

    assert summary["voided"] == 1
    assert old.status == "canceled"
    result = session.query(PickResult).filter_by(pick_id=pick_id).one()
    assert (result.result, result.payout) == ("push", 0.0)


def test_void_dry_run_writes_nothing(session):
    old = _bout(session, datetime.date(2026, 6, 28), "Merab", "Petr Yan")
    _bout(session, datetime.date(2026, 10, 24), "Petr Yan", "Merab")
    session.commit()

    assert void_reschedules(session, "mma", today=TODAY)["voided"] == 1
    assert old.status == "scheduled"


def test_the_row_the_live_feed_knows_about_survives(session):
    """`odds_api_id` is the identity the odds feed matches on. Dropping the
    row that carries it means the next fetch re-creates it as a brand new
    duplicate, so it outranks a raw odds count."""
    tracked = _bout(session, datetime.date(2026, 12, 26), "Kape", "Van")
    tracked.odds_api_id = "abc123"
    untracked = _bout(session, datetime.date(2026, 12, 27), "Kape", "Van",
                      odds=5)
    session.commit()
    tracked_id, untracked_id = tracked.id, untracked.id

    merge_date_splits(session, "mma", apply=True)

    session.expire_all()
    assert session.query(Game).filter_by(id=tracked_id).count() == 1, \
        "the feed-tracked row must survive even with fewer odds rows"
    assert session.query(Game).filter_by(id=untracked_id).count() == 0
    assert session.query(Odds).filter_by(game_id=tracked_id).count() == 5
