"""Reconstructing odds_at_pick for picks that stored an ungradeable price."""

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models import (
    Base,
    Game,
    Odds,
    PickModel,
    PickResult,
    StrategyModel,
    Team,
)
from backend.scripts.repair_invalid_odds import find_repairable, repair_invalid_odds


@pytest.fixture()
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        s.add_all([
            Team(id=1, name="Home", abbreviation="HOM", sport="ncaab"),
            Team(id=2, name="Away", abbreviation="AWY", sport="ncaab"),
            StrategyModel(id=1, name="ensemble", config_json="{}",
                          strategy_type="game"),
            Game(id=10, sport="ncaab", season="2026", date=date(2026, 3, 20),
                 home_team_id=1, away_team_id=2, home_score=80, away_score=70,
                 status="final"),
        ])
        # Four books priced the moneyline; four posted only a total. The
        # second group is what the old consensus counted in its divisor.
        for bk, mh, ma in (("dk", -142, 120), ("fd", -137, 114),
                           ("br", -150, 114), ("mgm", -145, 118)):
            s.add(Odds(game_id=10, bookmaker=bk, moneyline_home=mh,
                       moneyline_away=ma))
        for bk in ("t1", "t2", "t3", "t4"):
            s.add(Odds(game_id=10, bookmaker=bk, over_under=140.5))
        s.commit()
        yield s


def _pick(session, odds_at_pick, value="HOME ML", result="win", payout=0.0):
    p = PickModel(game_id=10, strategy_id=1, pick_type="moneyline",
                  pick_value=value, confidence=3, edge_pct=2.0,
                  odds_at_pick=odds_at_pick)
    session.add(p)
    session.flush()
    session.add(PickResult(pick_id=p.id, result=result, payout=payout))
    session.commit()
    return p


def test_find_repairable_selects_only_the_invalid_band(session):
    bad = _pick(session, -71)
    _pick(session, -142)
    _pick(session, 150)

    assert [p.id for p in find_repairable(session)] == [bad.id]


def test_repair_recomputes_the_price_from_surviving_books(session):
    p = _pick(session, -71)
    repair_invalid_odds(session, apply=True)
    session.refresh(p)

    # sum of the four real prices / 4, in probability space -- not / 8.
    assert p.odds_at_pick == -143


def test_repair_flags_the_row_as_reconstructed(session):
    p = _pick(session, -71)
    repair_invalid_odds(session, apply=True)
    session.refresh(p)

    # The price is an approximation, and the data must say so.
    assert p.odds_reconstructed is True


def test_untouched_picks_are_not_flagged(session):
    good = _pick(session, -142)
    repair_invalid_odds(session, apply=True)
    session.refresh(good)

    assert good.odds_reconstructed is False
    assert good.odds_at_pick == -142


def test_a_winning_payout_is_recomputed(session):
    """The whole point: a win at an ungradeable price booked 0.0."""
    p = _pick(session, -71, result="win", payout=0.0)
    repair_invalid_odds(session, apply=True)

    r = session.query(PickResult).filter_by(pick_id=p.id).one()
    assert r.payout == pytest.approx(100 / 143)
    assert r.payout > 0


def test_a_losing_payout_stays_minus_one(session):
    """A loss is -1.0 at any price, so these were always correct."""
    p = _pick(session, -71, result="loss", payout=-1.0)
    repair_invalid_odds(session, apply=True)

    r = session.query(PickResult).filter_by(pick_id=p.id).one()
    assert r.payout == -1.0


def test_the_away_side_uses_away_prices(session):
    p = _pick(session, -68, value="AWAY ML")
    repair_invalid_odds(session, apply=True)
    session.refresh(p)

    assert p.odds_at_pick == 116


def test_dry_run_writes_nothing(session):
    p = _pick(session, -71)
    result = repair_invalid_odds(session, apply=False)
    session.refresh(p)

    assert p.odds_at_pick == -71
    assert p.odds_reconstructed is False
    assert result["repairable"] == 1
    assert result["repaired"] == 0


def test_a_game_with_no_moneyline_rows_is_left_alone(session):
    session.query(Odds).delete()
    session.commit()
    p = _pick(session, -71)

    result = repair_invalid_odds(session, apply=True)
    session.refresh(p)

    # Nothing to reconstruct from: refuse rather than invent a price.
    assert p.odds_at_pick == -71
    assert p.odds_reconstructed is False
    assert result["repaired"] == 0
    assert result["unrecoverable"] == 1


def test_repair_is_idempotent(session):
    p = _pick(session, -71)
    repair_invalid_odds(session, apply=True)
    session.refresh(p)
    first = p.odds_at_pick

    second = repair_invalid_odds(session, apply=True)
    session.refresh(p)

    assert p.odds_at_pick == first
    assert second["repairable"] == 0


def test_non_moneyline_picks_are_ignored(session):
    """Spread and total are stored at a hardcoded -110 and are never invalid.

    If one ever is, this repair has no book-level price to rebuild it from,
    so it must not guess.
    """
    p = PickModel(game_id=10, strategy_id=1, pick_type="spread",
                  pick_value="HOME -3.5", confidence=3, edge_pct=2.0,
                  odds_at_pick=-50)
    session.add(p)
    session.commit()

    result = repair_invalid_odds(session, apply=True)
    session.refresh(p)
    assert p.odds_at_pick == -50
    assert result["skipped_not_moneyline"] == 1
