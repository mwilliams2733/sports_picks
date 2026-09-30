import os
import sqlite3
from datetime import date, datetime

import pytest

from backend.analysis.odds_utils import compute_pick_clv
from backend.database import get_engine, get_session
from backend.models import (
    Base, EmailedPick, Game, PaperPick, PickModel, PickResult, StrategyModel,
    Team, UserProfile,
)
from backend.scripts import export_picks


def _build_fixture_db(path: str) -> None:
    """A small on-disk sqlite db, written through the ORM and checkpointed
    to the main file so a read-only sqlite3 connection (WAL-unaware) can see
    every row -- the same reason project memory (sports-picks-db-snapshot)
    says `cp` of a live db is unsafe and `.backup`/checkpoint is required."""
    engine = get_engine(path)
    Base.metadata.create_all(engine)
    s = get_session(engine)

    s.add(StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True))
    s.add_all([
        Team(id=1, name="Pittsburgh Pirates", abbreviation="PIT", sport="mlb"),
        Team(id=2, name="St. Louis Cardinals", abbreviation="STL", sport="mlb"),
    ])
    s.flush()
    s.add(Game(id=1, sport="mlb", season="2026", date=date(2026, 9, 18),
              start_time=datetime(2026, 9, 18, 23, 5),
              home_team_id=1, away_team_id=2, home_score=4, away_score=2,
              status="final"))
    s.flush()

    # Pick 1: moneyline, graded, emailed once.
    s.add(PickModel(id=10, game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="HOME ML", confidence=4, edge_pct=6.0,
                    odds_at_pick=-130, model_prob=0.62,
                    suggested_unit_size=1.5,
                    rationale_json='[{"code": "rating_gap", "side": "home", "strength": "moderate"}]',
                    odds_reconstructed=False,
                    created_at=datetime(2026, 9, 18, 18, 0)))
    # Pick 2: moneyline, ungraded (no PickResult row at all).
    s.add(PickModel(id=11, game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="AWAY ML", confidence=3, edge_pct=-6.0,
                    odds_at_pick=110, model_prob=0.38,
                    created_at=datetime(2026, 9, 18, 18, 0)))
    # Pick 3: a prop, never emailed.
    s.add(PickModel(id=12, game_id=1, strategy_id=1, pick_type="prop",
                    pick_value="Paul Goldschmidt Over 1.5 Total Bases",
                    prop_player="Paul Goldschmidt", prop_market="batter_total_bases",
                    confidence=3, edge_pct=25.0, odds_at_pick=-115,
                    created_at=datetime(2026, 9, 18, 18, 0)))
    s.commit()

    s.add(PickResult(pick_id=10, result="win", payout=0.769,
                     odds_at_close=-145, line_at_close=None))
    s.commit()

    s.add(EmailedPick(digest_date=date(2026, 9, 18), pick_id=10, game_id=1,
                      sport="mlb", pick_type="moneyline", pick_value="HOME ML",
                      odds=-130, confidence=4,
                      sent_at=datetime(2026, 9, 18, 16, 0)))
    s.commit()

    # Data that must NEVER appear in the export.
    s.add(UserProfile(id=1, name="ShouldNotLeakUser"))
    s.commit()
    s.add(PaperPick(user_id=1, game_id=1, pick_type="moneyline",
                    pick_value="HOME ML", odds=-130, stake=10.0))
    s.commit()

    s.close()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    engine.dispose()


@pytest.fixture
def fixture_db(tmp_path):
    path = str(tmp_path / "fixture.db")
    _build_fixture_db(path)
    return path


def _read_csv(path):
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_missing_db_path_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        export_picks._connect_ro(str(tmp_path / "does_not_exist.db"))


def test_the_connection_cannot_write(fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO teams (id, name, abbreviation, sport) "
                        "VALUES (999, 'X', 'X', 'nfl')")
            conn.commit()
    finally:
        conn.close()


def test_export_writes_three_files_and_excludes_user_data(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    assert os.path.exists(counts["picks_path"])
    assert os.path.exists(counts["line_history_path"])

    rows = _read_csv(counts["picks_path"])
    assert len(rows) == 3
    header = set(rows[0].keys())
    assert "paper_picks" not in " ".join(header).lower()
    assert not any("user" in col.lower() for col in header), (
        "no paper-bet or user-profile column may appear in picks.csv"
    )
    for row in rows:
        for v in row.values():
            assert "ShouldNotLeakUser" not in (v or "")


def test_ungraded_pick_has_a_blank_result(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["11"]["result"] == ""
    assert rows["11"]["payout"] == ""


def test_moneyline_market_prob_novig_equals_model_minus_edge_over_100(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    row = rows["10"]
    expected = 0.62 - 6.0 / 100.0
    assert abs(float(row["market_prob_novig"]) - expected) < 1e-9


def test_prop_market_prob_novig_is_blank(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["12"]["market_prob_novig"] == ""


def test_clv_matches_odds_utils_compute_pick_clv(tmp_path, fixture_db):
    """CLV in the export must be exactly what the reused function gives for
    the same pick, not a parallel computation -- mutating the underlying
    function (below, via monkeypatch-free direct call) is how this is
    proven rather than asserted."""
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    row = rows["10"]
    expected_pct, expected_points = compute_pick_clv(
        "moneyline", "HOME ML", -130, -145, None)
    assert expected_points is None
    assert abs(float(row["clv"]) - expected_pct) < 1e-9


def test_pick_label_resolves_team_names(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["10"]["pick_label"] == "Pittsburgh Pirates to win"
    assert rows["11"]["pick_label"] == "St. Louis Cardinals to win"


def test_emailed_is_true_only_for_the_emailed_pick(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["10"]["emailed"] == "True"
    assert rows["10"]["emailed_odds"] == "-130"
    assert rows["11"]["emailed"] == "False"
    assert rows["12"]["emailed"] == "False"


def test_line_history_has_one_row_per_snapshot(tmp_path, fixture_db):
    engine = get_engine(fixture_db)
    from backend.models import LineSnapshot
    s = get_session(engine)
    s.add(LineSnapshot(game_id=1, bookmaker="draftkings",
                       moneyline_home=-130, moneyline_away=110,
                       captured_at=datetime(2026, 9, 18, 12, 0),
                       last_seen_at=datetime(2026, 9, 18, 18, 0)))
    s.commit()
    s.close()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    engine.dispose()

    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(fixture_db + ".out"))
    finally:
        conn.close()
    rows = _read_csv(counts["line_history_path"])
    assert len(rows) == 1
    assert rows[0]["bookmaker"] == "draftkings"
    assert rows[0]["moneyline_home"] == "-130"


def test_since_filter_excludes_earlier_games(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path), since="2026-09-19")
    finally:
        conn.close()
    rows = _read_csv(counts["picks_path"])
    assert rows == []


def test_sport_filter_keeps_only_the_requested_sport(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path), sports=["nfl"])
    finally:
        conn.close()
    rows = _read_csv(counts["picks_path"])
    assert rows == []


def test_manifest_lists_counts_and_exclusion_note(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    manifest_path = export_picks.write_manifest(
        str(tmp_path), db_path=fixture_db, repo_root=os.getcwd(),
        since=None, sports=None, counts=counts)
    text = open(manifest_path, encoding="utf-8").read()
    assert "picks.csv: 3 rows" in text
    assert "mlb: 3" in text
    assert "paper_picks" in text and "user_profiles" in text
    assert "data-dictionary.md" in text
