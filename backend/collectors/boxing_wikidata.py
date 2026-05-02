"""Wikidata SPARQL client for boxing fighter records.

Boxing has no clean public API like UFCStats. Wikidata is the closest free
canonical source for top-tier boxers. Coverage drops sharply outside the top
~2000 fighters, so the strategy gates picks on having seed data for both
fighters (see CombatSportsStrategy boxing path, Task 11).

Properties used: wdt:P1349 (wins), wdt:P1350 (losses), wdt:P1351 (draws).
Occupation filter: wdt:P106 = wd:Q11338576 (boxer).
"""
from __future__ import annotations
import httpx


SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

SPARQL_QUERY = """
SELECT ?fighter ?fighterLabel ?wins ?losses ?draws WHERE {
  ?fighter wdt:P106 wd:Q11338576 .
  OPTIONAL { ?fighter wdt:P1349 ?wins . }
  OPTIONAL { ?fighter wdt:P1350 ?losses . }
  OPTIONAL { ?fighter wdt:P1351 ?draws . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 1000
"""


async def fetch_boxer_records() -> list[dict]:
    """Hit the public Wikidata SPARQL endpoint and return parsed fighter dicts.

    No auth required. Wikidata enforces a 5-second-per-query soft limit and
    ~5K requests/day per IP — far above what this once-monthly seed needs.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            SPARQL_ENDPOINT,
            params={"query": SPARQL_QUERY, "format": "json"},
            headers={"User-Agent": "sports-picks/0.1 (educational)"},
        )
        response.raise_for_status()
        return parse_sparql_results(response.json())


def parse_sparql_results(payload: dict) -> list[dict]:
    """Pure transform: SPARQL JSON binding shape → list of fighter dicts.

    Each binding has the form
      {"fighter": {"value": "..."}, "fighterLabel": {"value": "..."},
       "wins": {"value": "50"}, ...}
    Wikidata OPTIONAL clauses mean any of wins/losses/draws may be absent,
    or present but non-integer (quantity literals with signs/units). We
    coerce silently to 0 in those cases — the caller decides whether 0 is
    actionable signal.
    """
    bindings = payload.get("results", {}).get("bindings", [])
    out: list[dict] = []
    for b in bindings:
        uri = b.get("fighter", {}).get("value", "")
        qid = uri.rsplit("/", 1)[-1] if uri else ""
        out.append({
            "wikidata_qid": qid,
            "name": b.get("fighterLabel", {}).get("value", ""),
            "wins": _coerce_int(b.get("wins", {}).get("value")),
            "losses": _coerce_int(b.get("losses", {}).get("value")),
            "draws": _coerce_int(b.get("draws", {}).get("value")),
        })
    return out


def _coerce_int(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0
