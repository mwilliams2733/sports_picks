"""Seed boxing fighters from Wikidata.

Run once on first deploy, rerun monthly to pick up new entries. Idempotent:
existing fighters are skipped, so post-seed Elo updates from real fights are
preserved across re-runs.

Wikidata labels can fall back to the bare QID when no English label exists
(shape: 'Q12345'). Those rows are skipped — they're not actionable signal.

Usage:
    python -m backend.scripts.seed_boxing_fighters [--db sports_picks.db]
"""
from __future__ import annotations
import asyncio
import re
import sys

from backend.collectors.boxing_wikidata import fetch_boxer_records
from backend.database import get_engine, get_session
from backend.models import Base, Team, EloRating


_QID_FALLBACK = re.compile(r"^Q\d+$")


async def seed(db_path: str = "sports_picks.db") -> dict:
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    session = get_session(engine)
    try:
        fighters = await fetch_boxer_records()
        new_count = 0
        for f in fighters:
            name = f["name"]
            if not name or _QID_FALLBACK.match(name):
                continue  # missing English label — skip rather than seed noise
            existing = (
                session.query(Team)
                .filter(Team.sport == "boxing", Team.name == name)
                .first()
            )
            if existing:
                continue
            t = Team(name=name, abbreviation=name[:32], sport="boxing")
            session.add(t)
            session.flush()
            session.add(EloRating(team_id=t.id, sport="boxing", rating=1500.0))
            new_count += 1
        session.commit()
        return {"fighters_seeded": new_count}
    finally:
        session.close()


if __name__ == "__main__":
    db_path = "sports_picks.db"
    for i, arg in enumerate(sys.argv):
        if arg == "--db" and i + 1 < len(sys.argv):
            db_path = sys.argv[i + 1]
    print(asyncio.run(seed(db_path)))
