"""Load boxing history from Wikipedia and replay boxing Elo.

Owner, 2026-10-10: boxing had no completed fights at all (no source we use
reports boxing results) -- 0 Elo rows, and 110 March-July games stuck as
`canceled`. This resolves the Wikipedia page of every boxer in our boxing
games, follows ONE hop to the opponents linked on their record tables, and
loads every parsed bout through `import_ufc_history.import_bouts` (name-key
matching, no double counts, stuck games completed only with no pick or paper
bet under --finalize-unfinished), then replays boxing Elo from seed.

Network: Wikipedia's API only, <= 1 request/second, cached in --cache-dir
(a cached title is never fetched again). Dry run by default; --apply commits.
On the live db: stop the scheduler and uvicorn and take a sqlite3.backup first.

    python -m backend.scripts.import_boxing_history --cache-dir <dir> [--finalize-unfinished] [--apply]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date

from backend.collectors.wikipedia_boxing import WikiClient, page_name, parse_record, record_table
from backend.models import Game, Team
from backend.scripts.dedupe_combat_games import rebuild_combat_elo
from backend.scripts.import_ufc_history import coverage, import_bouts
from backend.time_utils import et_today

def fighters_to_fetch(session, since: date) -> list[str]:
    ids = set()
    for g in (session.query(Game).filter(Game.sport == "boxing", Game.status != "final",
                                         Game.date >= since)):
        ids.update((g.home_team_id, g.away_team_id))
    return sorted(t.name for t in session.query(Team).filter(Team.id.in_(ids)))


def collect_bouts(client: WikiClient, names: list[str], hops: int = 1):
    stats = {"fighters": len(names), "pages_found": 0, "pages_missing": [],
             "opponent_pages": 0, "rows_skipped": Counter()}
    bouts, frontier, seen = [], [], set()
    for name in names:
        found = client.record_page(name)
        if found is None:
            stats["pages_missing"].append(name)
            continue
        title, wikitext = found
        seen.add(title)
        stats["pages_found"] += 1
        rows, skipped, opponents = parse_record(wikitext, subject=name)
        bouts += rows
        stats["rows_skipped"].update(skipped)
        frontier += opponents
    if hops >= 1:
        for title in dict.fromkeys(frontier):
            if title in seen:
                continue
            seen.add(title)
            wikitext = client.wikitext(title)
            if not wikitext or record_table(wikitext) is None:
                continue
            stats["opponent_pages"] += 1
            rows, skipped, _ = parse_record(wikitext, subject=page_name(title))
            bouts += rows
            stats["rows_skipped"].update(skipped)
    stats["rows_skipped"] = dict(stats["rows_skipped"])
    return bouts, stats


def main(argv: list[str]) -> int:
    from backend.config import load_config
    from backend.database import get_engine, get_session
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--db")
    parser.add_argument("--since", default="2026-01-01")
    parser.add_argument("--hops", type=int, default=1)
    parser.add_argument("--finalize-unfinished", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    db = args.db or load_config("config.yaml")["database_path"]
    session = get_session(get_engine(db))
    try:
        client = WikiClient(args.cache_dir)
        names = fighters_to_fetch(session, date.fromisoformat(args.since))
        bouts, stats = collect_bouts(client, names, hops=args.hops)
        print("wikipedia:", {**stats, "pages_missing": len(stats["pages_missing"])},
              "live requests:", client.live_requests)
        print("missing pages:", stats["pages_missing"])
        today = et_today()
        print("coverage before:", coverage(session, today, days=60, sport="boxing"))
        print("import:", import_bouts(session, bouts, sport="boxing",
                                      finalize_unfinished=args.finalize_unfinished))
        print("elo:", rebuild_combat_elo(session, "boxing"))
        print("coverage after:", coverage(session, today, days=60, sport="boxing"))
        if args.apply:
            session.commit()
            print("applied")
        else:
            session.rollback()
            print("dry run: rolled back (use --apply)")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
