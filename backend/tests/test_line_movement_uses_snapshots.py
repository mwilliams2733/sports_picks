"""Line movement must read the snapshot series, not the `Odds` table.

The bug this file exists to close
---------------------------------
`analyze_line_movement` and `get_steam_moves` both queried `Odds` for a game
and ordered by timestamp, calling the result "snapshots". But `Odds` holds
one row per (game, BOOKMAKER), upserted in place, so ordering those rows
orders books rather than observations. `snapshots[0]` was DraftKings and
`snapshots[-1]` was BetRivers, and the reported "spread_move" was the
disagreement between two books at one instant.

Confirmed in production on 2026-09-22: game 1018 had 15 Odds rows, one per
bookmaker, spreads ranging -7.0 to -2.5. The module reported a 4.5-point
line move on a line that may never have moved at all.

Its old test passed only because the fixture wrote two `dk` rows for one
game -- a state `_store_odds` cannot produce, because it upserts on
(game_id, bookmaker). A test whose fixture builds an unreachable state is
testing something the system never does.

Both functions now read `line_snapshots`, which is per-observation, and both
require a bookmaker so that cross-book spread can never be read as movement
again.
"""
import datetime

import pytest

from backend.analysis.line_movement import analyze_line_movement, get_steam_moves
from backend.analysis.line_snapshots import record_snapshot
from backend.database import get_engine, get_session
from backend.models import Base, Game, Odds, Team

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
               home_team_id=1, away_team_id=2))
    s.commit()
    return s


def _obs(session, book, spread, hour, ml_home=-150, ou=44.5):
    record_snapshot(session, 1, book,
                    dict(moneyline_home=ml_home, moneyline_away=130,
                         spread_home=spread, spread_away=-spread,
                         over_under=ou, spread_home_price=-110,
                         spread_away_price=-110, over_price=-110,
                         under_price=-110),
                    now=datetime.datetime(2026, 9, 20, hour, tzinfo=UTC))


# --- the regression -------------------------------------------------------

def test_two_bookmakers_at_one_instant_is_not_movement(session):
    """THE test. Twelve books disagreeing by four points is not a line that
    moved four points, and this is exactly what the old code reported."""
    _obs(session, "draftkings", -3.5, 9)
    _obs(session, "betrivers", -7.0, 9)

    assert analyze_line_movement(session, 1, bookmaker="draftkings") is None, \
        "one observation per book is not a series"


def test_odds_rows_alone_produce_no_movement(session):
    """The old implementation read these and reported movement from them."""
    session.add_all([
        Odds(game_id=1, bookmaker="draftkings", spread_home=-3.5,
             timestamp=datetime.datetime(2026, 9, 20, 9)),
        Odds(game_id=1, bookmaker="betrivers", spread_home=-7.0,
             timestamp=datetime.datetime(2026, 9, 20, 10)),
    ])
    session.commit()

    assert analyze_line_movement(session, 1, bookmaker="draftkings") is None


def test_one_book_moving_over_time_is_movement(session):
    """The other half -- without it, a function returning None always would
    pass every test above."""
    _obs(session, "draftkings", -3.5, 9, ml_home=-150, ou=44.5)
    _obs(session, "draftkings", -5.5, 14, ml_home=-190, ou=47.5)

    result = analyze_line_movement(session, 1, bookmaker="draftkings")

    assert result is not None
    assert result["spread_move"] == pytest.approx(-2.0)
    assert result["ml_move_home"] == -40
    assert result["ou_move"] == pytest.approx(3.0)
    assert result["snapshots"] == 2


def test_another_books_prices_do_not_enter_the_series(session):
    """A second book quoting wildly must not change the first book's move."""
    _obs(session, "draftkings", -3.5, 9)
    _obs(session, "draftkings", -5.5, 14)
    solo = analyze_line_movement(session, 1, bookmaker="draftkings")

    _obs(session, "betrivers", -14.0, 10)
    _obs(session, "betrivers", -1.0, 12)

    assert analyze_line_movement(session, 1, bookmaker="draftkings") == solo


# --- steam moves ----------------------------------------------------------

def test_a_steam_move_is_reported_between_consecutive_observations(session):
    _obs(session, "draftkings", -3.5, 9)
    _obs(session, "draftkings", -5.5, 14)

    moves = get_steam_moves(session, 1, bookmaker="draftkings", threshold=1.0)

    assert len(moves) == 1
    assert moves[0]["spread_before"] == -3.5
    assert moves[0]["spread_after"] == -5.5
    assert moves[0]["move"] == pytest.approx(-2.0)


def test_a_move_under_the_threshold_is_not_steam(session):
    _obs(session, "draftkings", -3.5, 9)
    _obs(session, "draftkings", -4.0, 14)

    assert get_steam_moves(session, 1, bookmaker="draftkings", threshold=1.0) == []


def test_steam_moves_do_not_span_bookmakers(session):
    """The same bug in the same module. Two books one point apart is not a
    one-point steam move."""
    _obs(session, "draftkings", -3.5, 9)
    _obs(session, "betrivers", -7.0, 10)

    assert get_steam_moves(session, 1, bookmaker="draftkings", threshold=1.0) == []


def test_a_bookmaker_is_required(session):
    """Movement without a book is the bug, so it is not expressible."""
    _obs(session, "draftkings", -3.5, 9)

    with pytest.raises(TypeError):
        analyze_line_movement(session, 1)
