"""The combat grading audit flags each known way a combat pick goes wrong."""
from datetime import date, datetime

from backend.database import get_engine, get_session
from backend.models import (Base, Game, LineSnapshot, Odds, PickModel, PickResult,
                            StrategyModel, Team)
from backend.scripts.audit_combat_grading import _connect, audit, summarize


def _db(tmp_path):
    path = str(tmp_path / "audit.db")
    engine = get_engine(path)
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add_all([
        StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True),
        StrategyModel(id=2, name="combat_sports", config_json="{}", is_active=True),
        Team(id=1, name="Alpha", abbreviation="Alpha", sport="mma"),
        Team(id=2, name="Bravo", abbreviation="Bravo", sport="mma"),
    ])
    s.flush()
    # One bout, Alpha (home) beat Bravo. Market: Alpha -200, Bravo +170.
    s.add(Game(id=1, sport="mma", season="2026", date=date(2026, 3, 21),
               home_team_id=1, away_team_id=2, home_score=1, away_score=0, status="final"))
    s.flush()
    s.add(Odds(game_id=1, bookmaker="bk", moneyline_home=-200, moneyline_away=170))
    s.add(LineSnapshot(game_id=1, bookmaker="bk", moneyline_home=-210, moneyline_away=175,
                       captured_at=datetime(2026, 3, 20)))
    picks = [
        # 1: clean -- combat model, Alpha at a price the market showed, won.
        (1, 2, "moneyline", "HOME ML", -200, "win", 0.5),
        # 2: the "Over 0" total the ensemble emitted; a 0/1 score always beats it.
        (2, 1, "over_under", "Over 0", -110, "win", 100 / 110),
        # 3: backed Bravo at -200, a price only the OPPONENT (Alpha) ever
        #    showed -- the side/price swap signature.
        (3, 2, "moneyline", "AWAY ML", -200, "loss", -1.0),
        # 4: stored as a win although Bravo lost -- grading disagrees.
        (4, 2, "moneyline", "AWAY ML", 170, "win", 1.7),
        # 5: price nobody quoted, and a payout that doesn't match it.
        (5, 2, "moneyline", "HOME ML", -400, "win", 0.9),
        # 6: priced at a cross-book consensus (-205) that no single book
        #    quoted but that sits inside the -210..-200 range -- clean.
        (6, 2, "moneyline", "HOME ML", -205, "win", 100 / 205),
    ]
    for pid, strat, ptype, pval, odds, res, pay in picks:
        s.add(PickModel(id=pid, game_id=1, strategy_id=strat, pick_type=ptype,
                        pick_value=pval, confidence=3, edge_pct=5.0, odds_at_pick=odds))
        s.flush()
        s.add(PickResult(pick_id=pid, result=res, payout=pay))
    s.commit()
    s.close()
    return path


def _flags(rows):
    return {r.values["pick_id"]: r.flags for r in rows}


def test_each_failure_mode_is_flagged_and_a_clean_pick_is_not(tmp_path):
    conn = _connect(_db(tmp_path))
    try:
        rows = audit(conn, "mma")
    finally:
        conn.close()
    f = _flags(rows)
    assert f[1] == []
    assert "OU_ON_COMBAT" in f[2] and "NON_COMBAT_STRATEGY" in f[2]
    assert "PRICE_OUTSIDE_RANGE" in f[3] and "PRICE_MATCHES_OPPONENT" in f[3]
    assert "RESULT_DISAGREES" in f[4]
    assert "PRICE_OUTSIDE_RANGE" in f[5] and "PAYOUT_DISAGREES" in f[5]
    assert "PRICE_MATCHES_OPPONENT" not in f[5]
    assert f[6] == [], "a consensus price inside the quoted range is not a defect"


def test_the_row_names_the_fighter_picked_and_the_winner(tmp_path):
    conn = _connect(_db(tmp_path))
    try:
        rows = {r.values["pick_id"]: r.values for r in audit(conn, "mma")}
    finally:
        conn.close()
    assert rows[3]["picked"] == "Bravo"
    assert rows[3]["winner"] == "Alpha"
    assert rows[1]["own_prices"] == "-210 -200"
    assert rows[1]["opp_prices"] == "170 175"


def test_the_database_is_opened_read_only(tmp_path):
    conn = _connect(_db(tmp_path))
    try:
        try:
            conn.execute("DELETE FROM picks")
        except Exception as e:  # sqlite3.OperationalError: attempt to write a readonly database
            assert "readonly" in str(e)
        else:
            raise AssertionError("the audit connection must not be able to write")
    finally:
        conn.close()


def test_summary_separates_totals_from_moneylines(tmp_path):
    conn = _connect(_db(tmp_path))
    try:
        lines = summarize(audit(conn, "mma"))
    finally:
        conn.close()
    text = "\n".join(lines)
    assert "over_under  1-0-0" in text
    assert "moneyline   4-1-0" in text
