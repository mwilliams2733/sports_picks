"""The snapshot series must be written by the pipeline, not just be writable.

This repo's recurring failure is a subsystem that runs, logs success, and
discards its output -- the prop pipeline fetched 6,188 lines a day and
generated zero picks for four months. `test_line_snapshots.py` proves
`record_snapshot` works when called. These tests prove `_store_odds` calls
it, which is the half that keeps going missing.

They also pin the thing that makes the table worth having: running the
collector twice with a moved line leaves BOTH prices on record, where the
`Odds` upsert leaves only the second.
"""
import datetime

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, LineSnapshot, Odds, Team
from backend.pipeline.full_pipeline import _store_odds

D20 = datetime.date(2026, 9, 20)


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "w.db")))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="Buffalo Bills", abbreviation="BUF", sport="nfl"),
               Team(id=2, name="Miami Dolphins", abbreviation="MIA", sport="nfl")])
    s.flush()
    s.add(Game(id=10, sport="nfl", season="2026-27", date=D20,
               status="scheduled", home_team_id=1, away_team_id=2,
               start_time=datetime.datetime(2026, 9, 20, 17, 0)))
    s.commit()
    return s


def _event(spread=-3.5, ml_home=-150, books=("draftkings",)):
    return [{
        "home_team": "Buffalo Bills", "away_team": "Miami Dolphins",
        "commence_time": "2026-09-20T17:00:00Z",
        "bookmakers": [{
            "key": b, "moneyline_home": ml_home, "moneyline_away": 130,
            "spread_home": spread, "spread_away": -spread, "over_under": 44.5,
            "spread_home_price": -110, "spread_away_price": -110,
            "over_price": -110, "under_price": -110,
        } for b in books],
    }]


def test_storing_odds_records_a_snapshot(session):
    """The wiring. Without this line the table stays empty forever while
    every other test in the suite passes."""
    _store_odds(session, "nfl", _event())

    assert session.query(LineSnapshot).count() == 1


def test_the_snapshot_carries_the_price_that_was_stored(session):
    _store_odds(session, "nfl", _event(spread=-3.5, ml_home=-150))

    snap = session.query(LineSnapshot).one()
    assert snap.game_id == 10 and snap.bookmaker == "draftkings"
    assert snap.spread_home == -3.5 and snap.moneyline_home == -150
    assert snap.over_under == 44.5 and snap.spread_home_price == -110


def test_a_moved_line_leaves_both_prices_on_record(session):
    """The reason the table exists. `Odds` keeps only the second price; the
    series keeps both, which is what an opening line or a CLV measurement
    needs."""
    _store_odds(session, "nfl", _event(spread=-3.5))
    _store_odds(session, "nfl", _event(spread=-6.5))

    assert [s.spread_home for s in
            session.query(LineSnapshot).order_by(LineSnapshot.id).all()] == [-3.5, -6.5]
    assert session.query(Odds).one().spread_home == -6.5, \
        "Odds must still be the current price"


def test_a_repeated_run_at_the_same_price_does_not_grow_the_table(session):
    """The scheduler scouts several times a day. An unchanged line is one
    row that held, not four identical rows."""
    for _ in range(4):
        _store_odds(session, "nfl", _event(spread=-3.5))

    assert session.query(LineSnapshot).count() == 1


def test_each_bookmaker_gets_its_own_snapshot(session):
    _store_odds(session, "nfl", _event(books=("draftkings", "fanduel")))

    assert {s.bookmaker for s in session.query(LineSnapshot).all()} == \
        {"draftkings", "fanduel"}


def test_an_unmatched_event_records_no_snapshot(session):
    """An event with no fixture already has its prices dropped rather than
    attached to another game. The snapshot must be dropped with them, not
    written against a guessed game_id."""
    event = _event()
    event[0]["home_team"] = "Nobody FC"

    _store_odds(session, "nfl", event)

    assert session.query(LineSnapshot).count() == 0
    assert session.query(Odds).count() == 0


def test_the_snapshot_is_committed_not_merely_flushed(session):
    """`_store_odds` commits. A snapshot left pending in the session would
    vanish on the next rollback -- present in tests, absent in production."""
    _store_odds(session, "nfl", _event())
    session.expunge_all()

    assert session.query(LineSnapshot).count() == 1
