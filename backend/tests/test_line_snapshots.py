"""The append-only price history behind opening lines and line movement.

`Odds` holds one row per (game, bookmaker) and `_store_odds` overwrites it in
place, so every price this project has ever seen except the latest is gone.
That is why `analyze_line_movement` was measuring the disagreement between
two BOOKMAKERS and calling it movement over time -- ordering `Odds` rows by
timestamp orders books, not observations.

`line_snapshots` is the missing series. These tests pin three things it has
to get right or it records the wrong history:

* an unchanged re-observation must NOT append a row, but must still record
  that we looked -- otherwise the table cannot tell "the line held for six
  hours" from "we stopped watching";
* a price disappearing is a change, not a non-observation;
* the CLOSING line is the last snapshot before kickoff, not the last
  snapshot. After kickoff the feed serves in-play prices, which nobody could
  have taken pre-game.
"""
import datetime

import pytest

from backend.analysis.line_snapshots import (PRICE_FIELDS, closing_line,
                                             line_history, opening_line,
                                             record_snapshot)
from backend.database import get_engine, get_session
from backend.models import Base, Game, LineSnapshot, Team

UTC = datetime.timezone.utc


@pytest.fixture()
def session():
    s = get_session(get_engine(":memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026-27",
               date=datetime.date(2026, 9, 20), status="scheduled",
               home_team_id=1, away_team_id=2,
               start_time=datetime.datetime(2026, 9, 20, 17, 0)))
    s.add(Game(id=2, sport="nfl", season="2026-27",
               date=datetime.date(2026, 9, 20), status="scheduled",
               home_team_id=1, away_team_id=2))          # no start_time
    s.commit()
    return s


def _prices(**kw):
    base = dict(moneyline_home=-150, moneyline_away=130, spread_home=-3.5,
                spread_away=3.5, over_under=44.5, spread_home_price=-110,
                spread_away_price=-110, over_price=-110, under_price=-110)
    base.update(kw)
    return base


def _at(hour, minute=0):
    return datetime.datetime(2026, 9, 20, hour, minute, tzinfo=UTC)


# --- appending ------------------------------------------------------------

def test_a_first_observation_is_recorded(session):
    assert record_snapshot(session, 1, "dk", _prices(), now=_at(9)) is True

    rows = session.query(LineSnapshot).all()
    assert len(rows) == 1
    assert rows[0].bookmaker == "dk" and rows[0].spread_home == -3.5


def test_every_price_field_is_stored(session):
    """A field the recorder silently drops is a field the history loses."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))

    row = session.query(LineSnapshot).one()
    for field in PRICE_FIELDS:
        assert getattr(row, field) == _prices()[field], field


def test_an_unchanged_reobservation_appends_nothing(session):
    """Six scout runs at the same price are one line, not six."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))

    assert record_snapshot(session, 1, "dk", _prices(), now=_at(12)) is False
    assert session.query(LineSnapshot).count() == 1


def test_an_unchanged_reobservation_still_records_that_we_looked(session):
    """Without this the table cannot distinguish a line that held for three
    hours from one we stopped watching after the first look."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))
    record_snapshot(session, 1, "dk", _prices(), now=_at(12))

    row = session.query(LineSnapshot).one()
    assert row.captured_at.replace(tzinfo=UTC) == _at(9)
    assert row.last_seen_at.replace(tzinfo=UTC) == _at(12)


def test_a_changed_price_appends_a_new_row(session):
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))

    assert record_snapshot(session, 1, "dk", _prices(spread_home=-4.5),
                           now=_at(12)) is True
    assert session.query(LineSnapshot).count() == 2


def test_the_earlier_row_is_not_rewritten_when_the_line_moves(session):
    """Append-only is the whole point. Updating in place would reproduce the
    exact bug this table exists to fix."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))
    record_snapshot(session, 1, "dk", _prices(spread_home=-4.5), now=_at(12))

    assert [r.spread_home for r in line_history(session, 1)] == [-3.5, -4.5]


def test_a_change_in_the_juice_alone_is_a_change(session):
    """-3.5 at -110 and -3.5 at -125 are different prices. A recorder
    watching only the line number would miss half of all movement."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))

    assert record_snapshot(session, 1, "dk", _prices(spread_home_price=-125),
                           now=_at(12)) is True


def test_a_price_disappearing_is_a_change(session):
    """A book pulling its total is a market event, not a missed look."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))

    assert record_snapshot(session, 1, "dk", _prices(over_under=None),
                           now=_at(12)) is True


def test_a_price_reappearing_is_a_change(session):
    record_snapshot(session, 1, "dk", _prices(over_under=None), now=_at(9))

    assert record_snapshot(session, 1, "dk", _prices(over_under=44.5),
                           now=_at(12)) is True


def test_a_line_returning_to_an_earlier_price_appends_a_row(session):
    """Comparison is against the LATEST snapshot, not any earlier one. A
    line that moves -3.5 -> -4.5 -> -3.5 moved twice."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))
    record_snapshot(session, 1, "dk", _prices(spread_home=-4.5), now=_at(12))
    record_snapshot(session, 1, "dk", _prices(), now=_at(14))

    assert [r.spread_home for r in line_history(session, 1)] == [-3.5, -4.5, -3.5]


# --- series are kept apart ------------------------------------------------

def test_each_bookmaker_is_its_own_series(session):
    """The bug being fixed: two books' prices read as one book moving."""
    record_snapshot(session, 1, "dk", _prices(spread_home=-3.5), now=_at(9))
    record_snapshot(session, 1, "fd", _prices(spread_home=-7.0), now=_at(9))

    assert record_snapshot(session, 1, "dk", _prices(spread_home=-3.5),
                           now=_at(12)) is False, "fd's price ended dk's run"
    assert len(line_history(session, 1, bookmaker="dk")) == 1


def test_each_game_is_its_own_series(session):
    record_snapshot(session, 1, "dk", _prices(), now=_at(9))
    record_snapshot(session, 2, "dk", _prices(), now=_at(9))

    assert len(line_history(session, 1)) == 1
    assert len(line_history(session, 2)) == 1


def test_history_is_returned_oldest_first(session):
    for hour, spread in ((14, -5.5), (9, -3.5), (12, -4.5)):
        record_snapshot(session, 1, "dk", _prices(spread_home=spread),
                        now=_at(hour))

    assert [r.spread_home for r in line_history(session, 1)] == [-3.5, -4.5, -5.5]


# --- opening and closing --------------------------------------------------

def test_the_opening_line_is_the_earliest_snapshot(session):
    record_snapshot(session, 1, "dk", _prices(spread_home=-3.5), now=_at(9))
    record_snapshot(session, 1, "dk", _prices(spread_home=-4.5), now=_at(12))

    assert opening_line(session, 1, bookmaker="dk").spread_home == -3.5


def test_the_closing_line_is_the_last_one_before_kickoff(session):
    """Game 1 starts at 17:00. The 18:00 quote is an in-play price that
    nobody could have taken pre-game; treating it as the close would grade
    every pick against a number from the second quarter."""
    record_snapshot(session, 1, "dk", _prices(spread_home=-3.5), now=_at(9))
    record_snapshot(session, 1, "dk", _prices(spread_home=-4.5), now=_at(16, 30))
    record_snapshot(session, 1, "dk", _prices(spread_home=-9.5), now=_at(18))

    assert closing_line(session, 1, bookmaker="dk").spread_home == -4.5


def test_a_snapshot_exactly_at_kickoff_is_not_the_close(session):
    record_snapshot(session, 1, "dk", _prices(spread_home=-3.5), now=_at(9))
    record_snapshot(session, 1, "dk", _prices(spread_home=-4.5), now=_at(17))

    assert closing_line(session, 1, bookmaker="dk").spread_home == -3.5


def test_without_a_start_time_the_latest_snapshot_is_the_close(session):
    """Game 2 has no start_time. Unknown is not past -- the convention
    `skip_started` already uses."""
    record_snapshot(session, 2, "dk", _prices(spread_home=-3.5), now=_at(9))
    record_snapshot(session, 2, "dk", _prices(spread_home=-4.5), now=_at(18))

    assert closing_line(session, 2, bookmaker="dk").spread_home == -4.5


def test_a_game_with_only_in_play_prices_has_no_closing_line(session):
    """None, not the in-play quote. A fabricated close is worse than an
    absent one -- it would silently enter a CLV measurement."""
    record_snapshot(session, 1, "dk", _prices(), now=_at(18))

    assert closing_line(session, 1, bookmaker="dk") is None


def test_a_game_with_no_snapshots_has_no_opening_or_closing_line(session):
    assert opening_line(session, 1, bookmaker="dk") is None
    assert closing_line(session, 1, bookmaker="dk") is None


def test_without_a_bookmaker_every_book_is_considered(session):
    record_snapshot(session, 1, "dk", _prices(), now=_at(12))
    record_snapshot(session, 1, "fd", _prices(), now=_at(9))

    assert opening_line(session, 1).bookmaker == "fd"
