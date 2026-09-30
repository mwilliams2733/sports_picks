"""void_combat_totals settles every combat spread/total as a push, and
touches nothing else."""
from datetime import date

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Game, PickModel, PickResult, StrategyModel, Team
from backend.scripts.void_combat_totals import run_on_session


def _session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([
        StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True),
        Team(id=1, name="A", abbreviation="A", sport="mma"),
        Team(id=2, name="B", abbreviation="B", sport="mma"),
        Team(id=3, name="H", abbreviation="H", sport="nfl"),
        Team(id=4, name="V", abbreviation="V", sport="nfl"),
        Team(id=5, name="C", abbreviation="C", sport="boxing"),
        Team(id=6, name="D", abbreviation="D", sport="boxing"),
    ])
    s.flush()
    s.add_all([
        Game(id=1, sport="mma", season="2026", date=date(2026, 3, 21),
             home_team_id=1, away_team_id=2, home_score=1, away_score=0, status="final"),
        Game(id=2, sport="nfl", season="2026", date=date(2026, 9, 20),
             home_team_id=3, away_team_id=4, home_score=24, away_score=20, status="final"),
        Game(id=3, sport="boxing", season="2026", date=date(2026, 5, 10),
             home_team_id=5, away_team_id=6, home_score=1, away_score=0, status="final"),
    ])
    s.flush()
    picks = [
        (1, 1, "over_under", "Over 0", ("win", 100 / 110)),    # phantom win -> push
        (2, 1, "over_under", "Over 0", None),                   # ungraded -> push
        (3, 1, "over_under", "Under 0", ("push", 0.0)),         # already void -> left alone
        (4, 1, "moneyline", "HOME ML", ("win", 0.5)),           # combat ML -> untouched
        (5, 2, "over_under", "Over 40.5", ("win", 100 / 110)),  # nfl total -> untouched
        (6, 3, "over_under", "Over 0", ("win", 100 / 110)),     # boxing total -> push
        (7, 1, "spread", "HOME -1.5", ("win", 100 / 110)),      # mma spread -> push
        # A "loss" whose stored payout is NOT the default -1.0 -- proves
        # units_removed reads the stored payout rather than assuming -1.0.
        (8, 1, "over_under", "Under 0", ("loss", -0.5)),
    ]
    for pid, gid, ptype, pval, res in picks:
        s.add(PickModel(id=pid, game_id=gid, strategy_id=1, pick_type=ptype,
                        pick_value=pval, confidence=1, edge_pct=50.0, odds_at_pick=-110))
        s.flush()
        if res:
            s.add(PickResult(pick_id=pid, result=res[0], payout=res[1]))
    s.commit()
    return s


def _results(s):
    return {r.pick_id: (r.result, round(r.payout, 4)) for r in s.query(PickResult)}


def test_apply_voids_combat_totals_and_nothing_else():
    s = _session()
    summary = run_on_session(s, apply=True)
    r = _results(s)
    assert r[1] == ("push", 0.0), "a phantom combat win becomes a void"
    assert r[2] == ("push", 0.0), "an ungraded combat total is settled as a void"
    assert r[3] == ("push", 0.0)
    assert r[4] == ("win", 0.5), "a combat moneyline is not touched"
    assert r[5][0] == "win", "a team-sport total is not touched"
    assert r[6] == ("push", 0.0), "a boxing total is voided too, not just mma"
    assert r[7] == ("push", 0.0), "an mma spread is voided"
    assert r[8] == ("push", 0.0), "a graded LOSS on a combat total is voided"
    assert summary["pick_ids"] == [1, 2, 6, 7, 8]
    assert summary["regraded"] == 4 and summary["settled"] == 1
    # 1, 6 and 7 are wins at 100/110; 8 is a loss whose STORED payout is
    # -0.5, not the -1.0 a naive "every loss costs one unit" sum would use.
    # If units_removed fell back to that assumption this would be
    # 3*(100/110) - 1.0 instead.
    expected = 3 * (100 / 110) - 0.5
    assert round(summary["units_removed"], 4) == round(expected, 4)
    assert summary["by_result"] == {"win": 3, "loss": 1}


def test_dry_run_writes_nothing():
    s = _session()
    before = _results(s)
    summary = run_on_session(s, apply=False)
    s.expire_all()
    assert _results(s) == before
    assert summary["pick_ids"] == [1, 2, 6, 7, 8]


def test_a_second_apply_finds_nothing_to_do():
    s = _session()
    run_on_session(s, apply=True)
    assert run_on_session(s, apply=True)["pick_ids"] == []


def test_units_removed_uses_the_stored_loss_payout_not_a_flat_one_unit():
    """Isolates the payout_for-sign-convention fix: a lone LOSS with a
    non-default stored payout must contribute exactly that payout, not
    -1.0."""
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([
        StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True),
        Team(id=1, name="A", abbreviation="A", sport="mma"),
        Team(id=2, name="B", abbreviation="B", sport="mma"),
    ])
    s.flush()
    s.add(Game(id=1, sport="mma", season="2026", date=date(2026, 3, 21),
              home_team_id=1, away_team_id=2, home_score=1, away_score=0,
              status="final"))
    s.flush()
    s.add(PickModel(id=1, game_id=1, strategy_id=1, pick_type="over_under",
                    pick_value="Over 0", confidence=1, edge_pct=50.0,
                    odds_at_pick=-110))
    s.flush()
    s.add(PickResult(pick_id=1, result="loss", payout=-0.5))
    s.commit()

    summary = run_on_session(s, apply=True)

    assert summary["units_removed"] == pytest.approx(-0.5)
