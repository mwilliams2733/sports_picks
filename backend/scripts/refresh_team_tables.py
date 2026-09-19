"""Regenerate backend/data/<sport>_teams.json from ESPN.

Run by hand when ESPN adds or renames a team. NOT run by tests: the snapshots
are committed so the resolver stays pure and offline.

    python -m backend.scripts.refresh_team_tables --sport ncaab
    python -m backend.scripts.refresh_team_tables --sport nba
"""

import argparse
import json
import pathlib

import httpx

#: ESPN's path segment per sport. Only sports with a snapshot here can be
#: repaired by fix_team_identity -- team_identity.canonical_abbr returns None
#: for a sport with no table, and the odds pipeline then skips the game
#: rather than inventing a row.
PATHS = {
    "ncaab": "basketball/mens-college-basketball",
    "nba": "basketball/nba",
    "nfl": "football/nfl",
    "ncaaf": "football/college-football",
    "mlb": "baseball/mlb",
}

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "data"


#: Abbreviation collisions ESPN genuinely ships, and the espn_id that wins.
#:
#: Reviewed by hand, one entry per collision. A collision NOT listed here is
#: refused rather than guessed at, because picking silently would map a real
#: program onto another school's ESPN id and break every game match for it.
#:
#: The losing row is dropped from the snapshot entirely, so the school it
#: names stops resolving. That is the intended trade for the ones below:
#: they are regional campuses that never appear in a betting market, and
#: leaving them in would make `espn_id_for` depend on ESPN's ordering.
RESOLVED_COLLISIONS = {
    # Ohio State Newark Titans (3161) is a D3 regional campus.
    ("ncaaf", "OSU"): "194",   # Ohio State Buckeyes
}


def _pages(sport: str):
    """Yield each page of ESPN's team list until one adds nothing new.

    ``limit`` caps at 500 and the endpoint exposes no page count, so the only
    way to know the list is exhausted is to ask for the next page. College
    football returns 762 teams across two pages; stopping at the first cost
    us Sam Houston and Southern Miss, whose games then carried odds that
    could never be matched to a fixture.
    """
    base = f"https://site.api.espn.com/apis/site/v2/sports/{PATHS[sport]}/teams"
    seen: set[str] = set()
    for page in range(1, 11):
        payload = httpx.get(base, params={"limit": 500, "page": page},
                            timeout=30).json()
        teams = [t["team"] for t in payload["sports"][0]["leagues"][0]["teams"]]
        fresh = [t for t in teams if t["id"] not in seen]
        if not fresh:
            return
        seen.update(t["id"] for t in fresh)
        yield fresh


def _collapse_espn_duplicates(rows: list[dict]) -> list[dict]:
    """Drop records identical but for their id, keeping the lowest.

    ESPN lists some schools twice -- Roosevelt Lakers appears as both 599 and
    127991, matching on every other field. That is one school with two
    records, not two schools, so collapsing it is not a judgement call.
    """
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r["abbreviation"], r["display_name"], r["location"])
        kept = best.get(key)
        if kept is None or int(r["espn_id"]) < int(kept["espn_id"]):
            best[key] = r
    return list(best.values())


def fetch(sport: str) -> list[dict]:
    teams = [t for page in _pages(sport) for t in page]
    rows = [
        {
            "espn_id": t["id"],
            "abbreviation": t["abbreviation"],
            "display_name": t["displayName"],
            "location": t.get("location", ""),
        }
        for t in teams
        if t.get("abbreviation") and t.get("displayName")
    ]
    rows = _collapse_espn_duplicates(rows)
    rows.sort(key=lambda r: r["abbreviation"])
    return rows


def apply_resolved_collisions(sport: str, rows: list[dict]) -> list[dict]:
    """Drop the losing row of each reviewed abbreviation collision."""
    out = []
    for r in rows:
        winner = RESOLVED_COLLISIONS.get((sport, r["abbreviation"]))
        if winner is not None and r["espn_id"] != winner:
            continue
        out.append(r)
    return out


def colliding_abbreviations(rows: list[dict]) -> dict[str, list[str]]:
    """Abbreviations held by more than one team, with the names involved."""
    by_abbr: dict[str, list[str]] = {}
    for r in rows:
        by_abbr.setdefault(r["abbreviation"], []).append(r["display_name"])
    return {a: n for a, n in by_abbr.items() if len(n) > 1}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(PATHS))
    args = ap.parse_args(argv)

    rows = apply_resolved_collisions(args.sport, fetch(args.sport))
    collisions = colliding_abbreviations(rows)
    if collisions:
        # The resolver's whole point is that an abbreviation identifies one
        # team. If ESPN ships two and the pair has not been reviewed into
        # RESOLVED_COLLISIONS, say which ones rather than writing a file that
        # quietly maps two of them onto one key.
        detail = "; ".join(f"{a}: {', '.join(n)}" for a, n in sorted(collisions.items()))
        raise SystemExit(
            f"ESPN returned {len(collisions)} unreviewed duplicate "
            f"abbreviation(s) for {args.sport}; refusing to write. {detail}\n"
            f"Add an entry to RESOLVED_COLLISIONS naming the winning espn_id.")

    out = OUT_DIR / f"{args.sport}_teams.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, sort_keys=True) + chr(10),
                   encoding="utf-8")
    print(f"wrote {len(rows)} {args.sport} teams to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
