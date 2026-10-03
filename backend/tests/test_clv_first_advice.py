"""CLV is measured from the price at FIRST ADVICE, not the last refresh.

A pick is refreshed in place until shortly before kickoff, so
``odds_at_pick`` is the last refresh -- a median of 1.7 hours before the
game on 2026-10-03, within the hour for 49 of 117 measurable picks. That
refresh is usually the same observation the close is built from, and 92%
of the 133 measurable moneylines read CLV of exactly zero. A pick could
neither beat nor lose to the close; the measurement compared a price with
itself.

The price that answers "did our advice beat the close" is the one the
advice was first given at:

1. ``pick_versions`` version 1 with source "insert" -- recorded live, from
   2026-09-30. A "backfill" version 1 is the final state reconstructed after
   the fact, so it is no first price at all.
2. Otherwise the as-sent ``emailed_picks`` price.
3. Otherwise no sample. Absent is not zero.

A pick whose side changed after first advice is excluded and counted: the
close on record belongs to the final side, not the side first advised.
The last-refresh CLV is kept as a labelled diagnostic.
"""
import datetime

import pytest

from backend.analysis import clv_report as cr
from backend.database import get_engine, get_session
from backend.models import (Base, EmailedPick, Game, PickModel, PickResult,
                            PickVersion, StrategyModel, Team)

D = datetime.date(2026, 10, 2)
NOW = datetime.datetime(2026, 10, 2, 12, 0)


@pytest.fixture()
def session(tmp_path):
    s = get_session(get_engine(str(tmp_path / "c.db")))
    Base.metadata.create_all(s.get_bind())
    s.add(StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True))
    s.add_all([Team(id=1, name="H", abbreviation="H", sport="nfl"),
               Team(id=2, name="A", abbreviation="A", sport="nfl")])
    s.flush()
    for gid in range(1, 7):
        s.add(Game(id=gid, sport="nfl", season="2026", date=D, status="final",
                   home_team_id=1, away_team_id=2, home_score=24, away_score=17))
    s.commit()
    return s


def _pick(s, pid, *, final_value="HOME ML", final_odds=-110, close=-110,
          pick_type="moneyline", line_close=None, v1=None, v1_source="insert",
          emailed=None):
    s.add(PickModel(id=pid, game_id=pid, strategy_id=1, pick_type=pick_type,
                    pick_value=final_value, confidence=3, edge_pct=4.0,
                    odds_at_pick=final_odds))
    s.flush()
    s.add(PickResult(pick_id=pid, result="win", payout=0.9,
                     odds_at_close=close, line_at_close=line_close))
    if v1 is not None:
        value, odds = v1
        s.add(PickVersion(pick_id=pid, version=1, recorded_at=NOW, source=v1_source,
                          pick_value=value, odds_at_pick=odds, confidence=3, edge_pct=4.0))
    if emailed is not None:
        value, odds = emailed
        s.add(EmailedPick(pick_id=pid, game_id=pid, digest_date=D, sport="nfl",
                          pick_type=pick_type, pick_value=value, odds=odds,
                          sent_at=NOW))
    s.commit()


def _sample(s, pid):
    return next(x for x in cr.load_samples(s) if x.pick_id == pid)


def test_clv_is_measured_from_the_first_advice_price(session):
    """Advised at +120; refreshed to -110, which is also the close. Old CLV
    read 0. The advice beat the close by 52.4 - 45.5 = 6.9 points."""
    _pick(session, 1, final_odds=-110, close=-110, v1=("HOME ML", 120))

    x = _sample(session, 1)

    assert x.basis == "first_advice"
    assert x.clv == pytest.approx((110 / 210 - 100 / 220) * 100)
    assert x.clv_last_refresh == pytest.approx(0.0)


def test_a_backfilled_first_version_falls_back_to_the_emailed_price(session):
    _pick(session, 2, close=-110, v1=("HOME ML", -110), v1_source="backfill",
          emailed=("HOME ML", -105))

    x = _sample(session, 2)

    assert x.basis == "emailed"
    assert x.clv == pytest.approx((110 / 210 - 105 / 205) * 100)


def test_no_first_advice_price_is_no_sample(session):
    """Before 2026-09-30 and never emailed: nothing records the first price."""
    _pick(session, 3, v1=("HOME ML", -110), v1_source="backfill")

    x = _sample(session, 3)

    assert x.basis is None
    assert x.clv is None
    assert cr.usable([x]) == []


def test_a_side_that_changed_after_first_advice_is_excluded(session):
    """First advised AWAY, finished HOME: the stored close is HOME's."""
    _pick(session, 4, final_value="HOME ML", v1=("AWAY ML", 150))

    x = _sample(session, 4)

    assert x.side_changed is True
    assert x.clv is None


def test_line_clv_uses_the_first_advice_line(session):
    """First advised HOME -3.5, refreshed to HOME -4; closed -4.5.
    The advice beat the close by a full point, not half of one."""
    _pick(session, 5, pick_type="spread", final_value="HOME -4", final_odds=-110,
          close=-110, line_close=-4.5, v1=("HOME -3.5", -110))

    x = _sample(session, 5)

    assert x.basis == "first_advice"
    assert x.clv == pytest.approx(1.0)
    assert x.clv_last_refresh == pytest.approx(0.5)


def test_the_report_says_what_it_measured_and_what_it_dropped(session):
    _pick(session, 1, v1=("HOME ML", 120))
    _pick(session, 3, v1=("HOME ML", -110), v1_source="backfill")
    _pick(session, 4, final_value="HOME ML", v1=("AWAY ML", 150))

    text = cr.format_report(cr.load_samples(session), include_reconstructed=False)

    assert "first-advice price" in text
    assert "no first-advice price on record: 1" in text
    assert "side changed after first advice: 1" in text
    assert "LAST-REFRESH" in text, "the old figure stays, labelled as a diagnostic"
