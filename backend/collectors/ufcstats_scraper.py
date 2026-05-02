"""UFCStats.com HTML parser.

Parses event detail pages (URL pattern: ufcstats.com/event-details/{event_id}).
Each event page lists ~10-12 fights as a table; this module extracts fighter
names, winner, method, round, and time per fight.

Live fetching is in `fetch_event_html`; parsing is in `parse_event_fights` so
tests use checked-in HTML fixtures rather than live HTTP.
"""
from __future__ import annotations
import httpx
from bs4 import BeautifulSoup


BASE_URL = "http://ufcstats.com"


async def fetch_event_html(event_url: str) -> str:
    """Live HTTP fetch — not exercised in tests."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(event_url, headers={"User-Agent": "sports-picks/0.1"})
        response.raise_for_status()
        return response.text


def parse_event_fights(html: str) -> list[dict]:
    """Extract fight rows from a UFCStats event-details page.

    Returns a list of dicts: {fighter_a_name, fighter_b_name, winner, method}.
    `winner` is one of "fighter_a", "fighter_b", or "draw".
    Returns [] if no fight table is found.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="b-fight-details__table")
    if not table:
        return []
    out: list[dict] = []
    rows = table.find_all("tr", class_="b-fight-details__table-row")
    for row in rows:
        # Skip the header row.
        classes = row.get("class") or []
        if "b-fight-details__table-row_type_head" in classes:
            continue
        cols = row.find_all("td")
        if len(cols) < 8:
            continue

        # Column 1: fighter names (two <p> tags inside)
        fighter_paragraphs = cols[1].find_all("p", class_="b-fight-details__table-text")
        if len(fighter_paragraphs) < 2:
            continue
        a_name = fighter_paragraphs[0].get_text(strip=True)
        b_name = fighter_paragraphs[1].get_text(strip=True)

        # Column 0: W/L badges. The first <p> describes fighter_a, the second fighter_b.
        win_badges = cols[0].find_all("p")
        winner = "draw"
        if len(win_badges) >= 2:
            a_classes = " ".join(win_badges[0].get("class") or [])
            b_classes = " ".join(win_badges[1].get("class") or [])
            if "b-flag-text-style_win" in a_classes:
                winner = "fighter_a"
            elif "b-flag-text-style_win" in b_classes:
                winner = "fighter_b"
            # else: both losses or draw markup — leave as "draw"

        # Column 7: Method
        method_paragraph = cols[7].find("p")
        method = method_paragraph.get_text(strip=True) if method_paragraph else ""

        out.append({
            "fighter_a_name": a_name,
            "fighter_b_name": b_name,
            "winner": winner,
            "method": method,
        })
    return out
