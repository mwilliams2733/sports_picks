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


def fetch(sport: str) -> list[dict]:
    url = (f"https://site.api.espn.com/apis/site/v2/sports/{PATHS[sport]}"
           "/teams?limit=500")
    payload = httpx.get(url, timeout=30).json()
    teams = [t["team"] for t in payload["sports"][0]["leagues"][0]["teams"]]
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
    rows.sort(key=lambda r: r["abbreviation"])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(PATHS))
    args = ap.parse_args()

    rows = fetch(args.sport)
    duplicates = len(rows) - len({r["abbreviation"] for r in rows})
    if duplicates:
        # The resolver's whole point is that an abbreviation identifies one
        # team. If ESPN ever ships two, say so rather than writing a file that
        # quietly maps two of them onto one key.
        raise SystemExit(
            f"ESPN returned {duplicates} duplicate abbreviations for "
            f"{args.sport}; refusing to write")

    out = OUT_DIR / f"{args.sport}_teams.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, sort_keys=True) + chr(10),
                   encoding="utf-8")
    print(f"wrote {len(rows)} {args.sport} teams to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
