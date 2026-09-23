"""Grading must record a real closing consensus, not one arbitrary book.

`capture_closing_odds` took `Odds` ordered by timestamp and used `.first()`.
`Odds` is one row per (game, BOOKMAKER), so that is whichever book was
written last in the final upsert pass. `odds_at_pick`, the number it gets
subtracted from, is a CONSENSUS across every book -- so the stored CLV
measured the gap between one arbitrary book and the field, plus whatever
price movement actually happened.

It now reads the last pre-kickoff snapshot from every book and consensuses
them with `average_odds`, the same function that produced `odds_at_pick`.

Spread and total picks keep `odds_at_close = odds_at_pick` on purpose. The
juice on those markets barely moves and was not captured historically; the
meaningful CLV is in the line number. Setting a real closing price against
an `odds_at_pick` that fell back to STANDARD_JUICE would fabricate price
movement in the other direction.
"""
import datetime

import pytest

from backend.analysis.line_snapshots import record_snapshot
from backend.database import get_engine, get_session
from backend.models import Base, Game, Odds, PickResult, Team
from backend.pipeline.grader import capture_closing_odds

UTC = datetime.timezone.utc
KICKOFF = datetime.datetime(2026, 9, 20, 17, 0)


@pytest.fixture()
def session():
    s = get_session(get_engine(":memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026-27",
               date=datetime.date(2026, 9, 20), status="final",
               home_team_id=1, away_team_id=2, start_time=KICKOFF))
    s.commit()
    return s


def _obs(session, book, hour, ml_home=-150, ml_away=130, spread=-3.5, ou=44.5):
    record_snapshot(session, 1, book,
                    dict(moneyline_home=ml_home, moneyline_away=ml_away,
                         spread_home=spread, spread_away=-spread,
                         over_under=ou, spread_home_price=-110,
                         spread_away_price=-110, over_price=-110,
                         under_price=-110),
                    now=datetime.datetime(2026, 9, 20, hour, tzinfo=UTC))


def _capture(session, pick_type, pick_value, odds_at_pick=-140):
    pr = PickResult(pick_id=1, result="win", payout=1.0)
    capture_closing_odds(session, pr, 1, pick_type, pick_value, odds_at_pick)
    return pr


# --- the regression -------------------------------------------------------

def test_the_close_is_a_consensus_of_every_book(session):
    """Two books quoting -200 and -100 close near -135, not at whichever
    row the database happened to return first."""
    _obs(session, "dk", 16, ml_home=-200, ml_away=170)
    _obs(session, "fd", 16, ml_home=-100, ml_away=-100)

    pr = _capture(session, "moneyline", "HOME")

    assert -200 < pr.odds_at_close < -100, pr.odds_at_close


def test_a_stale_odds_row_is_not_used_as_the_close(session):
    """The old implementation read this row and nothing else."""
    session.add(Odds(game_id=1, bookmaker="zz_written_last",
                     moneyline_home=-900, moneyline_away=600))
    session.commit()
    _obs(session, "dk", 16, ml_home=-150, ml_away=130)

    pr = _capture(session, "moneyline", "HOME")

    assert pr.odds_at_close == -150, "fell back to the Odds table"


def test_an_in_play_price_is_never_the_close(session):
    _obs(session, "dk", 16, ml_home=-150, ml_away=130)
    _obs(session, "dk", 19, ml_home=-3000, ml_away=1400)

    assert _capture(session, "moneyline", "HOME").odds_at_close == -150


# --- each side and market -------------------------------------------------

def test_the_away_side_takes_the_away_price(session):
    _obs(session, "dk", 16, ml_home=-150, ml_away=130)

    assert _capture(session, "moneyline", "AWAY").odds_at_close == 130


def test_a_home_spread_takes_the_home_line(session):
    _obs(session, "dk", 16, spread=-6.5)

    pr = _capture(session, "spread", "HOME -3.5")
    assert pr.line_at_close == pytest.approx(-6.5)


def test_an_away_spread_takes_the_away_line(session):
    _obs(session, "dk", 16, spread=-6.5)

    pr = _capture(session, "spread", "AWAY +3.5")
    assert pr.line_at_close == pytest.approx(6.5)


def test_a_total_takes_the_closing_total(session):
    _obs(session, "dk", 16, ou=47.5)

    assert _capture(session, "over_under", "Over 44.5").line_at_close == \
        pytest.approx(47.5)


def test_a_spread_pick_keeps_its_own_price_as_the_close(session):
    """Deliberate: line CLV is the signal on these markets, and a real
    closing price against a STANDARD_JUICE odds_at_pick would invent price
    movement that never happened."""
    _obs(session, "dk", 16, spread=-6.5)

    assert _capture(session, "spread", "HOME -3.5",
                    odds_at_pick=-115).odds_at_close == -115


# --- refusing to invent ---------------------------------------------------

def test_a_game_with_no_pre_game_snapshot_records_no_close(session):
    """None, not a guess. A fabricated close is worse than a missing one --
    it enters the CLV average silently."""
    _obs(session, "dk", 19)          # in-play only

    pr = _capture(session, "moneyline", "HOME")
    assert pr.odds_at_close is None


def test_a_game_with_no_snapshots_at_all_records_no_close(session):
    pr = _capture(session, "moneyline", "HOME")

    assert pr.odds_at_close is None and pr.line_at_close is None


def test_a_spread_pick_with_no_price_of_its_own_records_no_price(session):
    """`odds_at_close` mirrors `odds_at_pick` on these markets, so a pick
    with no recorded price leaves it NULL rather than inventing -110."""
    _obs(session, "dk", 16, spread=-6.5)

    pr = _capture(session, "spread", "HOME -3.5", odds_at_pick=None)
    assert pr.odds_at_close is None
    assert pr.line_at_close == pytest.approx(-6.5), "the LINE is still captured"
