"""The closing line must be a consensus, computed the way odds_at_pick is.

`capture_closing_odds` used to do this:

    session.query(Odds).filter(Odds.game_id == id).order_by(
        Odds.timestamp.desc()).first()

`Odds` holds one row per (game, BOOKMAKER), so that returns whichever book
happened to be written last in the final upsert pass -- not a closing line.
Meanwhile `odds_at_pick` is a CONSENSUS across every book, built by
`average_odds`. Subtracting one from the other measured two things at once:
the price movement CLV is for, and the gap between one arbitrary book and
the field.

Before 2026-09-23 that last write was also frequently an IN-PLAY price, for
the reason fixed in 7463de7.

So the close is now: each book's last PRE-GAME snapshot, run through the
same `average_odds` that produced `odds_at_pick`.
"""
import datetime

import pytest

from backend.analysis.line_snapshots import closing_consensus, record_snapshot
from backend.database import get_engine, get_session
from backend.models import Base, Game, Team

UTC = datetime.timezone.utc


@pytest.fixture()
def session():
    s = get_session(get_engine(":memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026-27",
               date=datetime.date(2026, 9, 20), status="final",
               home_team_id=1, away_team_id=2,
               start_time=datetime.datetime(2026, 9, 20, 17, 0)))
    s.add(Game(id=2, sport="nfl", season="2026-27",
               date=datetime.date(2026, 9, 20), status="final",
               home_team_id=1, away_team_id=2))          # no start_time
    s.commit()
    return s


def _obs(session, book, hour, ml_home=-150, ml_away=130, spread=-3.5,
         ou=44.5, game_id=1):
    record_snapshot(session, game_id, book,
                    dict(moneyline_home=ml_home, moneyline_away=ml_away,
                         spread_home=spread, spread_away=-spread,
                         over_under=ou, spread_home_price=-110,
                         spread_away_price=-110, over_price=-110,
                         under_price=-110),
                    now=datetime.datetime(2026, 9, 20, hour, tzinfo=UTC))


def test_a_game_with_no_snapshots_has_no_close(session):
    assert closing_consensus(session, 1) is None


def test_one_book_is_its_own_consensus(session):
    _obs(session, "dk", 9, ml_home=-150, ml_away=130)

    close = closing_consensus(session, 1)
    assert close["moneyline_home"] == -150
    assert close["books"] == 1


def test_every_book_contributes(session):
    """The bug: one arbitrary book was the whole close."""
    _obs(session, "dk", 9, ml_home=-200, ml_away=170)
    _obs(session, "fd", 9, ml_home=-100, ml_away=-100)

    close = closing_consensus(session, 1)
    assert close["books"] == 2
    assert -200 < close["moneyline_home"] < -100, close


def test_it_uses_the_same_consensus_as_odds_at_pick(session):
    """Derived, not re-implemented: a second averager would make CLV measure
    the gap between two definitions of consensus."""
    from backend.analysis.strategy import average_odds
    from backend.data_types import OddsSnapshot

    _obs(session, "dk", 9, ml_home=-150, ml_away=130, spread=-3.5, ou=44.5)
    _obs(session, "fd", 9, ml_home=-140, ml_away=120, spread=-3.0, ou=45.0)

    expected = average_odds([
        OddsSnapshot("dk", -150, 130, -3.5, 3.5, 44.5, -110, -110, -110, -110),
        OddsSnapshot("fd", -140, 120, -3.0, 3.0, 45.0, -110, -110, -110, -110)])

    close = closing_consensus(session, 1)
    for field in ("moneyline_home", "moneyline_away", "spread_home",
                  "over_under"):
        assert close[field] == expected[field], field


def test_only_each_books_LAST_pre_game_price_counts(session):
    """A book's opening quote must not be averaged in with its close."""
    _obs(session, "dk", 9, ml_home=-400, ml_away=320)
    _obs(session, "dk", 16, ml_home=-150, ml_away=130)

    close = closing_consensus(session, 1)
    assert close["moneyline_home"] == -150
    assert close["books"] == 1


def test_an_in_play_price_is_not_part_of_the_close(session):
    """Game 1 starts at 17:00. The 18:00 quote prices a game in progress."""
    _obs(session, "dk", 16, ml_home=-150, ml_away=130)
    _obs(session, "dk", 18, ml_home=-2000, ml_away=1100)

    assert closing_consensus(session, 1)["moneyline_home"] == -150


def test_a_book_that_only_quoted_in_play_does_not_join_the_close(session):
    """Otherwise one late book's in-play number drags the consensus."""
    _obs(session, "dk", 16, ml_home=-150, ml_away=130)
    _obs(session, "fd", 18, ml_home=-2000, ml_away=1100)

    close = closing_consensus(session, 1)
    assert close["books"] == 1
    assert close["moneyline_home"] == -150


def test_a_game_with_only_in_play_prices_has_no_close(session):
    """None, not a fabricated number. An absent close must stay absent or it
    silently enters the CLV figure it was supposed to support."""
    _obs(session, "dk", 18)

    assert closing_consensus(session, 1) is None


def test_without_a_start_time_the_latest_price_is_the_close(session):
    """Unknown is not past. Game 2 has no start_time."""
    _obs(session, "dk", 9, ml_home=-400, ml_away=320, game_id=2)
    _obs(session, "dk", 18, ml_home=-150, ml_away=130, game_id=2)

    assert closing_consensus(session, 2)["moneyline_home"] == -150


def test_the_close_reports_when_it_was_taken(session):
    """A close struck four hours before kickoff is a different measurement
    from one struck four minutes before, and CLV depends on which."""
    _obs(session, "dk", 9)
    _obs(session, "fd", 16)

    close = closing_consensus(session, 1)
    assert close["captured_at"] == datetime.datetime(2026, 9, 20, 16)


def test_lines_are_averaged_as_points_not_prices(session):
    _obs(session, "dk", 9, spread=-3.0, ou=44.0)
    _obs(session, "fd", 9, spread=-4.0, ou=46.0)

    close = closing_consensus(session, 1)
    assert close["spread_home"] == pytest.approx(-3.5)
    assert close["over_under"] == pytest.approx(45.0)
