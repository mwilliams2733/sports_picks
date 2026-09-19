"""Regenerate backend/data/ncaab_teams.json from ESPN.

Run by hand when ESPN adds or renames a school. NOT run by tests: the
snapshot is committed so the resolver stays pure and offline.

    python -m backend.scripts.refresh_ncaab_teams
"""

import json
import pathlib

import httpx

URL = ("https://site.api.espn.com/apis/site/v2/sports/basketball/"
       "mens-college-basketball/teams?limit=500")
OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "ncaab_teams.json"


def main() -> int:
    payload = httpx.get(URL, timeout=30).json()
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

    duplicates = len(rows) - len({r["abbreviation"] for r in rows})
    if duplicates:
        # The resolver's whole point is that an abbreviation identifies one
        # school. If ESPN ever ships two, say so rather than writing a file
        # that quietly maps two programmes onto one key.
        raise SystemExit(f"ESPN returned {duplicates} duplicate abbreviations; refusing to write")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} teams to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
