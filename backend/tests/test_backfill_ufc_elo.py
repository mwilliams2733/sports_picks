"""Tests for the UFC Kaggle backfill script."""
from datetime import date as _date


def test_backfill_creates_fighter_teams_and_elo(tmp_path):
    """Three-fight CSV → 3 Team rows, 3 EloRating rows, Cormier highest Elo."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, EloRating
    from backend.scripts.backfill_ufc_elo import backfill

    csv_path = tmp_path / "ufc.csv"
    csv_path.write_text(
        "date,fighter_a,fighter_b,winner\n"
        "2020-01-01,Anderson Silva,Chris Weidman,Anderson Silva\n"
        "2020-06-01,Chris Weidman,Daniel Cormier,Daniel Cormier\n"
        "2021-01-01,Anderson Silva,Daniel Cormier,Daniel Cormier\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "test.db")
    summary = backfill(csv_path=str(csv_path), db_path=db_path)

    assert summary["fights_imported"] == 3
    assert summary["fighters_seeded"] == 3

    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        fighters = session.query(Team).filter(Team.sport == "mma").all()
        assert {t.name for t in fighters} == {"Anderson Silva", "Chris Weidman", "Daniel Cormier"}

        name_to_id = {t.name: t.id for t in fighters}
        elos = {er.team_id: er.rating
                for er in session.query(EloRating).filter(EloRating.sport == "mma").all()}
        assert len(elos) == 3
        # Cormier won twice → highest Elo
        assert elos[name_to_id["Daniel Cormier"]] > elos[name_to_id["Anderson Silva"]]
        assert elos[name_to_id["Daniel Cormier"]] > elos[name_to_id["Chris Weidman"]]
    finally:
        session.close()


def test_backfill_handles_draws(tmp_path):
    """A 'Draw' winner should not change Elo of either fighter."""
    from backend.database import get_engine, get_session
    from backend.models import EloRating
    from backend.scripts.backfill_ufc_elo import backfill

    csv_path = tmp_path / "ufc.csv"
    csv_path.write_text(
        "date,fighter_a,fighter_b,winner\n"
        "2020-01-01,Fighter A,Fighter B,Draw\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "draw.db")
    summary = backfill(csv_path=str(csv_path), db_path=db_path)

    assert summary["fights_imported"] == 1
    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        elos = {er.team_id: er.rating for er in session.query(EloRating).all()}
        # Both fighters start at 1500, draw with equal rating leaves them unchanged.
        assert all(abs(rating - 1500.0) < 0.01 for rating in elos.values())
    finally:
        session.close()


def test_backfill_skips_malformed_winner(tmp_path):
    """Rows where winner is neither fighter nor 'Draw' should be skipped, not crash."""
    from backend.scripts.backfill_ufc_elo import backfill

    csv_path = tmp_path / "ufc.csv"
    csv_path.write_text(
        "date,fighter_a,fighter_b,winner\n"
        "2020-01-01,Alice,Bob,Charlie\n"  # malformed: Charlie isn't in this fight
        "2020-02-01,Alice,Bob,Alice\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "malformed.db")
    summary = backfill(csv_path=str(csv_path), db_path=db_path)

    # Only the 2nd fight should count
    assert summary["fights_imported"] == 1


def test_backfill_replays_chronologically(tmp_path):
    """CSV rows in random order must be sorted by date before Elo replay,
    so a fighter's Elo at fight N reflects their record up to N-1."""
    from backend.database import get_engine, get_session
    from backend.models import EloRating
    from backend.scripts.backfill_ufc_elo import backfill

    # Provide fights out of date order; Cormier wins both.
    csv_path = tmp_path / "ufc.csv"
    csv_path.write_text(
        "date,fighter_a,fighter_b,winner\n"
        "2021-01-01,Cormier,Silva,Cormier\n"
        "2020-01-01,Cormier,Silva,Cormier\n",
        encoding="utf-8",
    )
    db_path = str(tmp_path / "chrono.db")
    summary = backfill(csv_path=str(csv_path), db_path=db_path)

    assert summary["fights_imported"] == 2
    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        elos = {er.team_id: er.rating for er in session.query(EloRating).all()}
        # Two consecutive wins by same fighter → Elo > 1512 (single-win delta);
        # the second win delta is smaller because expected_home is now > 0.5.
        assert max(elos.values()) > 1512.0
        # Cormier's Elo gain in fight 2 is less than 12 (because expected was > 0.5),
        # so total gain is less than 24 (two full deltas).
        assert max(elos.values()) < 1524.0
    finally:
        session.close()
