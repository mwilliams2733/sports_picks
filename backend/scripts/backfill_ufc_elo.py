"""One-shot backfill of UFC fighter Elo from a Kaggle CSV.

Expected CSV columns: date (ISO), fighter_a, fighter_b, winner.
winner is one of: the name of fighter_a, the name of fighter_b, or "Draw"
(case-insensitive).

Usage:
    python -m backend.scripts.backfill_ufc_elo path/to/ufc.csv [--db sports_picks.db]

Replays fights chronologically, updating Elo with K=24 after each.
"""
from __future__ import annotations
import csv
import sys

from backend.analysis.elo import get_k_factor
from backend.database import get_engine, get_session
from backend.models import Base, Team, EloRating


def _expected(h: float, a: float) -> float:
    return 1.0 / (1.0 + 10 ** ((a - h) / 400))


def backfill(csv_path: str, db_path: str = "sports_picks.db") -> dict:
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    K = get_k_factor("mma")

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r["date"])

    fighter_id: dict[str, int] = {}
    elo: dict[int, float] = {}

    def _ensure_fighter(name: str) -> int:
        if name in fighter_id:
            return fighter_id[name]
        existing = session.query(Team).filter(Team.sport == "mma", Team.name == name).first()
        if existing:
            fighter_id[name] = existing.id
            er = (session.query(EloRating)
                  .filter(EloRating.team_id == existing.id, EloRating.sport == "mma").first())
            elo[existing.id] = er.rating if er else 1500.0
            return existing.id
        team = Team(name=name, abbreviation=name[:32], sport="mma")
        session.add(team)
        session.flush()
        fighter_id[name] = team.id
        elo[team.id] = 1500.0
        session.add(EloRating(team_id=team.id, sport="mma", rating=1500.0))
        return team.id

    fights_imported = 0
    for row in rows:
        a_name = row["fighter_a"]
        b_name = row["fighter_b"]
        winner = (row.get("winner") or "").strip()
        if winner.lower() == "draw":
            actual_a = 0.5
        elif winner == a_name:
            actual_a = 1.0
        elif winner == b_name:
            actual_a = 0.0
        else:
            continue  # malformed row — winner is not a participant or "Draw"

        a_id = _ensure_fighter(a_name)
        b_id = _ensure_fighter(b_name)

        ea = _expected(elo[a_id], elo[b_id])
        delta = K * (actual_a - ea)
        elo[a_id] += delta
        elo[b_id] -= delta
        fights_imported += 1

    # Flush all in-memory updates to EloRating rows.
    for er in session.query(EloRating).filter(EloRating.sport == "mma").all():
        if er.team_id in elo:
            er.rating = elo[er.team_id]
    session.commit()
    fighters_seeded = len(fighter_id)
    session.close()
    return {"fights_imported": fights_imported, "fighters_seeded": fighters_seeded}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: python -m backend.scripts.backfill_ufc_elo path/to/ufc.csv")
        sys.exit(2)
    csv_path = args[0]
    db_path = "sports_picks.db"
    for i, arg in enumerate(sys.argv):
        if arg == "--db" and i + 1 < len(sys.argv):
            db_path = sys.argv[i + 1]
    print(backfill(csv_path, db_path))
