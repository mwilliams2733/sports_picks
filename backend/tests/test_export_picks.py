import os
import sqlite3
from datetime import date, datetime

import pytest

from backend.analysis.odds_utils import compute_pick_clv
from backend.database import get_engine, get_session
from backend.models import (
    Base, EmailedPick, Game, PaperPick, PickModel, PickResult, PickVersion,
    StrategyModel, Team, UserProfile,
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

    # Pick 10: moneyline, graded, emailed TWICE (two digest dates below) --
    # the export must still emit one row, carrying the latest emailing.
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
    # Pick 13: a spread pick, graded, with a real line_at_close -- exercises
    # clv_line_pts (not clv_price_pp).
    s.add(PickModel(id=13, game_id=1, strategy_id=1, pick_type="spread",
                    pick_value="HOME -1.5", confidence=3, edge_pct=4.0,
                    odds_at_pick=-110, model_prob=0.54,
                    created_at=datetime(2026, 9, 18, 18, 0)))
    # Pick 14: moneyline, graded, but no close ever captured on either
    # column -- both clv columns must be blank, not a fabricated 0.
    s.add(PickModel(id=14, game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="HOME ML", confidence=3, edge_pct=3.0,
                    odds_at_pick=-120, model_prob=0.56,
                    created_at=datetime(2026, 9, 18, 18, 0)))
    # Pick 15: a legacy spread pick with no model_prob at all (predates the
    # column being populated) -- market_prob_novig must be blank, not 0.5.
    s.add(PickModel(id=15, game_id=1, strategy_id=1, pick_type="spread",
                    pick_value="AWAY +1.5", confidence=3, edge_pct=62.0,
                    odds_at_pick=-110, model_prob=None,
                    created_at=datetime(2026, 3, 14, 12, 0)))
    s.commit()

    # Pick 10 has two recorded versions -- an insert and a refresh that
    # flipped the side, so it exercises n_versions, first_seen_at AND
    # flipped_side all at once. Pick 11 has exactly one version (no flip).
    # Pick 13 has two versions on the SAME side with a moved line/price --
    # regression for flipped_side comparing raw pick_value (a line move
    # reads as a "flip") instead of the parsed side. Picks 12, 14, 15 have
    # no version history at all, exercising the blank/zero/False defaults.
    s.add(PickVersion(pick_id=10, version=1, recorded_at=datetime(2026, 9, 18, 16, 0),
                      source="insert", pick_value="AWAY ML", confidence=3,
                      edge_pct=-4.0, odds_at_pick=100, model_prob=0.4,
                      rationale_json=None))
    s.add(PickVersion(pick_id=10, version=2, recorded_at=datetime(2026, 9, 18, 18, 0),
                      source="refresh", pick_value="HOME ML", confidence=4,
                      edge_pct=6.0, odds_at_pick=-130, model_prob=0.62,
                      rationale_json='[{"code": "rating_gap", "side": "home", "strength": "moderate"}]'))
    s.add(PickVersion(pick_id=11, version=1, recorded_at=datetime(2026, 9, 18, 18, 0),
                      source="insert", pick_value="AWAY ML", confidence=3,
                      edge_pct=-6.0, odds_at_pick=110, model_prob=0.38,
                      rationale_json=None))
    s.add(PickVersion(pick_id=13, version=1, recorded_at=datetime(2026, 9, 18, 16, 0),
                      source="insert", pick_value="HOME -1.5", confidence=3,
                      edge_pct=3.0, odds_at_pick=-105, model_prob=0.52,
                      rationale_json=None))
    s.add(PickVersion(pick_id=13, version=2, recorded_at=datetime(2026, 9, 18, 18, 0),
                      source="refresh", pick_value="HOME -2.5", confidence=3,
                      edge_pct=4.0, odds_at_pick=-110, model_prob=0.54,
                      rationale_json=None))
    s.commit()

    s.add(PickResult(pick_id=10, result="win", payout=0.769,
                     odds_at_close=-145, line_at_close=None))
    s.add(PickResult(pick_id=13, result="win", payout=0.909,
                     odds_at_close=-110, line_at_close=-2.5))
    s.add(PickResult(pick_id=14, result="win", payout=0.833,
                     odds_at_close=None, line_at_close=None))
    s.commit()

    # Pick 10 is emailed on two different digest dates. The export must
    # still yield exactly one row for it, carrying the LATER emailing's
    # values (odds/pick_value/confidence), not the first.
    s.add(EmailedPick(digest_date=date(2026, 9, 18), pick_id=10, game_id=1,
                      sport="mlb", pick_type="moneyline", pick_value="HOME ML",
                      odds=-130, confidence=4,
                      sent_at=datetime(2026, 9, 18, 16, 0)))
    s.add(EmailedPick(digest_date=date(2026, 9, 19), pick_id=10, game_id=1,
                      sport="mlb", pick_type="moneyline", pick_value="HOME ML",
                      odds=-135, confidence=5, best_book="fanduel", best_odds=-128,
                      sent_at=datetime(2026, 9, 19, 16, 0)))
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


def test_export_writes_four_files_and_excludes_user_data(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    assert os.path.exists(counts["picks_path"])
    assert os.path.exists(counts["line_history_path"])
    assert os.path.exists(counts["pick_versions_path"])

    rows = _read_csv(counts["picks_path"])
    assert len(rows) == 6
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


def test_spread_market_prob_novig_is_05_when_model_prob_present(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["13"]["market_prob_novig"] == "0.5"


def test_legacy_spread_with_no_model_prob_gets_blank_market_prob_novig(tmp_path, fixture_db):
    """Pick 15 predates model_prob being populated on spread/over_under
    picks. Writing 0.5 anyway would claim its edge was measured against a
    fair coin flip when nothing says it was."""
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["15"]["model_prob"] == ""
    assert rows["15"]["market_prob_novig"] == ""


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
    assert abs(float(row["clv_price_pp"]) - expected_pct) < 1e-9
    assert row["clv_line_pts"] == "", "a moneyline pick must never carry a line-points CLV"


def test_spread_clv_is_line_points_not_price(tmp_path, fixture_db):
    """Pick 13 is a spread pick with a real line_at_close. Its CLV belongs
    in clv_line_pts, in line points, and clv_price_pp (a different unit)
    must stay blank -- the two must never land in the same column."""
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    row = rows["13"]
    expected_pct, expected_points = compute_pick_clv(
        "spread", "HOME -1.5", -110, -110, -2.5)
    assert expected_pct is None
    assert row["clv_price_pp"] == ""
    assert abs(float(row["clv_line_pts"]) - expected_points) < 1e-9


def test_no_close_gives_blank_clv_in_both_columns(tmp_path, fixture_db):
    """Pick 14 is graded but has no odds_at_close or line_at_close on
    record. Absent must stay absent in both clv columns, not become a
    fabricated 0 -- the same rule clv_report.py applies (`usable` drops a
    None clv rather than averaging it in as 0)."""
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    row = rows["14"]
    assert row["clv_price_pp"] == ""
    assert row["clv_line_pts"] == ""


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
    assert rows["11"]["emailed"] == "False"
    assert rows["12"]["emailed"] == "False"


def test_pick_emailed_on_two_dates_gives_one_row_with_latest_values(tmp_path, fixture_db):
    """Pick 10 was emailed on 2026-09-18 (odds -130, confidence 4) and again
    on 2026-09-19 (odds -135, confidence 5). The export must emit exactly
    one row for pick_id 10, carrying the LATER emailing's odds/confidence/
    digest_date, not the first -- per the owner's ruling, not an arbitrary
    "first seen" choice."""
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = [r for r in _read_csv(counts["picks_path"]) if r["pick_id"] == "10"]
    assert len(rows) == 1
    row = rows[0]
    assert row["emailed_odds"] == "-135"
    assert row["emailed_confidence"] == "5"
    assert row["emailed_digest_date"] == "2026-09-19"
    assert row["emailed_pick_value"] == "HOME ML"
    assert (row["emailed_best_book"], row["emailed_best_odds"]) == ("fanduel", "-128")


def test_a_snapshot_from_before_the_best_price_columns_still_exports(tmp_path, fixture_db):
    import sqlite3
    with sqlite3.connect(fixture_db) as c:     # rebuild emailed_picks without the columns
        c.execute("CREATE TABLE ep_old AS SELECT id, digest_date, pick_id, game_id, sport, "
                  "pick_type, pick_value, odds, prop_player, prop_market, confidence, sent_at "
                  "FROM emailed_picks")
        c.execute("DROP TABLE emailed_picks")
        c.execute("ALTER TABLE ep_old RENAME TO emailed_picks")
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    row = [r for r in _read_csv(counts["picks_path"]) if r["pick_id"] == "10"][0]
    assert (row["emailed_odds"], row["emailed_best_book"]) == ("-135", "")


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


def test_line_history_carries_start_time_and_series_depth(tmp_path, fixture_db):
    """Without start_time_utc, the pre-kickoff close cannot be located for a
    game with no pick at all. series_depth (the max per-bookmaker
    observation count for the game) lets a reader reproduce clv_report's
    `measurable()` population -- a depth-1 series never moved."""
    engine = get_engine(fixture_db)
    from backend.models import LineSnapshot
    s = get_session(engine)
    s.add_all([
        LineSnapshot(game_id=1, bookmaker="draftkings",
                    moneyline_home=-130, moneyline_away=110,
                    captured_at=datetime(2026, 9, 18, 12, 0),
                    last_seen_at=datetime(2026, 9, 18, 14, 0)),
        LineSnapshot(game_id=1, bookmaker="draftkings",
                    moneyline_home=-135, moneyline_away=115,
                    captured_at=datetime(2026, 9, 18, 18, 0),
                    last_seen_at=datetime(2026, 9, 18, 20, 0)),
        # A second book seen once: depth is the deepest single book (2),
        # not the game's total snapshot count (3).
        LineSnapshot(game_id=1, bookmaker="fanduel",
                    moneyline_home=-128, moneyline_away=108,
                    captured_at=datetime(2026, 9, 18, 12, 0),
                    last_seen_at=datetime(2026, 9, 18, 20, 0)),
    ])
    s.commit()
    s.close()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    engine.dispose()

    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(fixture_db + ".out2"))
    finally:
        conn.close()
    rows = _read_csv(counts["line_history_path"])
    assert len(rows) == 3
    assert all(r["start_time_utc"] == "2026-09-18T23:05:00Z" for r in rows)
    assert all(r["series_depth"] == "2" for r in rows)
    picks = _read_csv(counts["picks_path"])
    assert {r["series_depth"] for r in picks if r["game_id"] == "1"} == {"2"}


def test_timestamps_are_iso8601_with_z_suffix(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    row = rows["10"]
    assert row["created_at"] == "2026-09-18T18:00:00Z"
    assert row["start_time_utc"] == "2026-09-18T23:05:00Z"
    assert row["emailed_at"] == "2026-09-19T16:00:00Z"
    for col in ("created_at", "start_time_utc", "emailed_at"):
        assert row[col].endswith("Z")
        assert "+" not in row[col]


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
    assert "picks.csv: 6 rows" in text
    assert "mlb: 6" in text
    assert "paper_picks" in text and "user_profiles" in text
    assert "data-dictionary.md" in text


def test_pick_versions_csv_has_one_row_per_version(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    assert counts["pick_versions_rows"] == 5
    rows = _read_csv(counts["pick_versions_path"])
    assert len(rows) == 5
    p10 = sorted([r for r in rows if r["pick_id"] == "10"],
                key=lambda r: r["version"])
    assert [r["version"] for r in p10] == ["1", "2"]
    assert p10[0]["source"] == "insert"
    assert p10[1]["source"] == "refresh"
    assert p10[1]["pick_value"] == "HOME ML"
    assert p10[1]["factors"] == "rating_gap:home:moderate"


def test_picks_csv_n_versions_and_first_seen_at(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["10"]["n_versions"] == "2"
    assert rows["10"]["first_seen_at"] == "2026-09-18T16:00:00Z"
    assert rows["11"]["n_versions"] == "1"
    assert rows["11"]["first_seen_at"] == "2026-09-18T18:00:00Z"
    # Pick 12 has no recorded version history at all.
    assert rows["12"]["n_versions"] == "0"
    assert rows["12"]["first_seen_at"] == ""


def test_flipped_side_is_true_only_when_the_pick_value_changed(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    # Pick 10's two versions are AWAY ML then HOME ML -- a real flip.
    assert rows["10"]["flipped_side"] == "True"
    # Pick 11 has exactly one version -- nothing to flip against.
    assert rows["11"]["flipped_side"] == "False"
    # Pick 13's two versions are both HOME, just at a different line/price
    # (-1.5 -> -2.5) -- a line move, not a side flip. Regression for
    # comparing raw pick_value instead of the parsed side.
    assert rows["13"]["flipped_side"] == "False"
    # Pick 12 has no version history at all.
    assert rows["12"]["flipped_side"] == "False"


def test_manifest_lists_pick_versions_row_count(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    manifest_path = export_picks.write_manifest(
        str(tmp_path), db_path=fixture_db, repo_root=os.getcwd(),
        since=None, sports=None, counts=counts)
    text = open(manifest_path, encoding="utf-8").read()
    assert "pick_versions.csv: 5 rows" in text


def test_manifest_timestamp_is_iso8601_with_z_suffix(tmp_path, fixture_db):
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    manifest_path = export_picks.write_manifest(
        str(tmp_path), db_path=fixture_db, repo_root=os.getcwd(),
        since=None, sports=None, counts=counts)
    text = open(manifest_path, encoding="utf-8").read()
    first_line = text.splitlines()[0]
    assert first_line.endswith("Z")
    assert "+00:00" not in first_line


# --- a db that predates pick_versions (finding #6) --------------------------

@pytest.fixture
def pre_merge_fixture_db(tmp_path):
    """A db built WITHOUT the `pick_versions` table at all, the same way a
    `pre-*.db` snapshot or a live db that has not yet restarted onto
    `feat/pick-versions` would look. `export()` must degrade, not crash."""
    path = str(tmp_path / "pre_merge.db")
    engine = get_engine(path)
    tables = [t for t in Base.metadata.sorted_tables if t.name != "pick_versions"]
    Base.metadata.create_all(engine, tables=tables)
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="ensemble", config_json="{}", is_active=True))
    s.add_all([
        Team(id=1, name="Home Team", abbreviation="H", sport="nfl"),
        Team(id=2, name="Away Team", abbreviation="A", sport="nfl"),
    ])
    s.flush()
    s.add(Game(id=1, sport="nfl", season="2026", date=date(2026, 9, 20),
              start_time=datetime(2026, 9, 20, 18, 0),
              home_team_id=1, away_team_id=2, status="scheduled"))
    s.flush()
    s.add(PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="HOME ML", confidence=3, edge_pct=5.0,
                    odds_at_pick=-110, created_at=datetime(2026, 9, 20, 12, 0)))
    s.commit()
    s.close()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA wal_checkpoint(FULL)")
    engine.dispose()
    return path


def test_export_degrades_gracefully_with_no_pick_versions_table(
        tmp_path, pre_merge_fixture_db):
    conn = export_picks._connect_ro(pre_merge_fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()

    assert counts["pick_versions_table_present"] is False
    assert counts["pick_versions_rows"] == 0
    assert os.path.exists(counts["pick_versions_path"])
    version_rows = _read_csv(counts["pick_versions_path"])
    assert version_rows == []
    # The file is still header-only, not missing entirely.
    with open(counts["pick_versions_path"], encoding="utf-8") as fh:
        header = fh.readline().strip()
    assert header == ",".join(export_picks.PICK_VERSIONS_FIELDS)

    rows = {r["pick_id"]: r for r in _read_csv(counts["picks_path"])}
    assert rows["1"]["first_seen_at"] == ""
    assert rows["1"]["n_versions"] == "0"
    assert rows["1"]["flipped_side"] == "False"


def test_manifest_notes_when_pick_versions_table_is_absent(
        tmp_path, pre_merge_fixture_db):
    conn = export_picks._connect_ro(pre_merge_fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    manifest_path = export_picks.write_manifest(
        str(tmp_path), db_path=pre_merge_fixture_db, repo_root=os.getcwd(),
        since=None, sports=None, counts=counts)
    text = open(manifest_path, encoding="utf-8").read()
    assert "pick_versions.csv: 0 rows" in text
    assert "NOTE" in text and "no pick_versions table" in text


def test_manifest_has_no_absence_note_when_the_table_is_present(tmp_path, fixture_db):
    """The note must be specific to a genuinely missing table, not printed
    unconditionally."""
    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path))
    finally:
        conn.close()
    manifest_path = export_picks.write_manifest(
        str(tmp_path), db_path=fixture_db, repo_root=os.getcwd(),
        since=None, sports=None, counts=counts)
    text = open(manifest_path, encoding="utf-8").read()
    assert "no pick_versions table" not in text


def test_a_db_predating_the_newer_pick_columns_still_exports(tmp_path, fixture_db):
    """tracking_only, market_prob_novig and withdrawn_at were added on
    2026-10-03. A `pre-*.db` snapshot has none of them, and the export is
    read-only, so it cannot migrate: each must read as the value an older
    row truly has, not crash with "no such column"."""
    import sqlite3
    con = sqlite3.connect(fixture_db)
    con.execute("ALTER TABLE picks DROP COLUMN tracking_only")
    con.execute("ALTER TABLE picks DROP COLUMN market_prob_novig")
    con.execute("ALTER TABLE picks DROP COLUMN withdrawn_at")
    con.commit()
    con.close()

    conn = export_picks._connect_ro(fixture_db)
    try:
        counts = export_picks.export(conn, str(tmp_path / "out"))
    finally:
        conn.close()

    rows = _read_csv(counts["picks_path"])
    assert rows
    assert {r["tracking_only"] for r in rows} == {"False"}
    assert {r["withdrawn_at"] for r in rows} == {""}
