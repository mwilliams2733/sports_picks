"""One-off (2026-10-09): put Claude's written reasoning onto its paper bets.

Claude's NFL picks were written with reasoning at pick time and saved to
logs-archive/claude-picks-<date>-<slate>.json; before paper_picks.note
existed it never reached the app. This matches each saved pick to Claude's
(user 3) straight bet on that game and day and writes the note -- only where
a bet has none, so it never overwrites. Matching is by team nickname (the
last word of each team's full name) against the file's "Away @ Home".

    python -m backend.scripts.backfill_claude_notes                # dry run
    python -m backend.scripts.backfill_claude_notes --apply
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.orm import aliased

from backend.config import load_config
from backend.database import get_engine, get_session
from backend.models import Game, PaperPick, Team

CLAUDE_USER_ID = 3
FILES = Path("logs-archive")
DATE_IN_NAME = re.compile(r"claude-picks-(\d{4}-\d{2}-\d{2})-")


def note_for(pick: dict) -> str:
    """The reasoning, confidence and agree/against-the-model. Files before
    2026-10-08 have no ``vs_model`` field; that clause is then left out."""
    note = f"{pick['why'].strip()} Confidence {pick['confidence']}"
    if pick.get("vs_model"):
        note += f" · Model: {pick['vs_model']}"
    return note + "."


def _nickname(name: str) -> str:
    return name.strip().split()[-1].lower()


def match_bets(session, picks: list[dict], day: date) -> list[tuple[PaperPick, str]]:
    Home, Away = aliased(Team), aliased(Team)
    rows = (session.query(PaperPick, Home, Away)
            .join(Game, Game.id == PaperPick.game_id)
            .join(Home, Home.id == Game.home_team_id)
            .join(Away, Away.id == Game.away_team_id)
            .filter(PaperPick.user_id == CLAUDE_USER_ID, PaperPick.parlay_id.is_(None),
                    PaperPick.note.is_(None), Game.date == day)
            .order_by(PaperPick.id).all())
    out = []
    for pick in picks:
        away, _, home = pick["game"].partition(" @ ")
        hits = [b for b, h, a in rows
                if _nickname(h.name) == _nickname(home) and _nickname(a.name) == _nickname(away)]
        if len(hits) == 1:
            out.append((hits[0], note_for(pick)))
        else:
            print(f"skip {pick['game']}: {len(hits)} matching bets")
    return out


def main(argv: list[str]) -> int:
    config = load_config("config.yaml")
    session = get_session(get_engine(config["database_path"]))
    try:
        matched = []
        for path in sorted(FILES.glob("claude-picks-*.json")):
            m = DATE_IN_NAME.search(path.name)
            if not m:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            matched += match_bets(session, data["picks"], date.fromisoformat(m.group(1)))
        for bet, note in matched:
            print(f"bet {bet.id} game {bet.game_id} {bet.pick_value}: {note[:80]}...")
        print(f"{len(matched)} bet(s) to annotate")
        if "--apply" in argv and matched:
            for bet, note in matched:
                bet.note = note
            session.commit()
            print("applied")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
