"""Boxing fight history from Wikipedia "Professional boxing record" tables.

Parsing here; fetching (WikiClient) below. A row is either parsed exactly or
skipped and counted -- never guessed: no-contests and bouts listed before
they happen have no result; an unreadable date is skipped.
Scores follow the grader's combat convention: winner 1, loser 0, draw 1/1.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date
from pathlib import Path

from backend.collectors.ufc import normalize_name
from backend.collectors.ufcstats_history import HistoricalBout

_SECTION = re.compile(r"^==+\s*Professional boxing record\s*==+\s*$", re.M | re.I)
_NEXT_L2 = re.compile(r"^==[^=]", re.M)
_TABLE = re.compile(r"^\{\|.*?^\|\}", re.M | re.S)
_REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_ABBR = re.compile(r"\{\{\s*abbr\s*\|([^|}]*)\|[^}]*\}\}", re.I)
_DTS = re.compile(r"\{\{\s*dts\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", re.I)
#: Formatting wrappers whose argument IS the cell text ({{small|2015-05-30}});
#: unwrapped before the general template strip, which would delete the date.
_WRAPPER = re.compile(r"\{\{\s*(?:small|nowrap|nobr)\s*\|([^{}|]*)\}\}", re.I)
_ISO = re.compile(r"(\d{4})\s*[-–]\s*(\d{1,2})\s*[-–]\s*(\d{1,2})")
_DMY = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})")
_MDY = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s*(\d{4})")
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")
_LEAD_NAME = re.compile(r"'''(.+?)'''")
_OPPONENT_HEADER = re.compile(r"^!.*Opponent", re.M | re.I)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def record_table(wikitext: str) -> str | None:
    """The record table: the first table under the record heading whose
    header names an Opponent column (a summary table may come first)."""
    m = _SECTION.search(wikitext)
    if not m:
        return None
    rest = wikitext[m.end():]
    nxt = _NEXT_L2.search(rest)
    section = rest[:nxt.start()] if nxt else rest
    for table in _TABLE.findall(section):
        if _OPPONENT_HEADER.search(table):
            return table
    return None


def _strip_attrs(cell: str) -> str:
    """'style="x"|value' -> 'value' (the last '|' outside [[...]] and {{...}})."""
    depth_link = depth_tpl = 0
    cut = -1
    i = 0
    while i < len(cell):
        two = cell[i:i + 2]
        if two == "[[":
            depth_link += 1
            i += 2
            continue
        if two == "]]":
            depth_link = max(0, depth_link - 1)
            i += 2
            continue
        if two == "{{":
            depth_tpl += 1
            i += 2
            continue
        if two == "}}":
            depth_tpl = max(0, depth_tpl - 1)
            i += 2
            continue
        if cell[i] == "|" and depth_link == 0 and depth_tpl == 0:
            cut = i
        i += 1
    return cell[cut + 1:] if cut >= 0 else cell


def _cells(row: str) -> list[str]:
    out = []
    for line in row.split("\n"):
        if not line.startswith("|") or line.startswith(("|}", "|+", "|-")):
            continue
        for part in line[1:].split("||"):
            out.append(_REF.sub("", _strip_attrs(part)).strip())
    return out


def _header(table: str) -> list[str]:
    names = []
    for line in table.split("\n"):
        if line.startswith("!"):
            for part in line[1:].split("!!"):
                text = _ABBR.sub(r"\1", _strip_attrs(part))
                names.append(_TEMPLATE.sub("", text).strip().lower())
    return names


def _safe(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_date(cell: str) -> date | None:
    m = _DTS.search(cell)
    if m:
        return _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    link = _LINK.search(cell)
    text = (link.group(2) or link.group(1)) if link else cell
    text = _TEMPLATE.sub("", _WRAPPER.sub(r"\1", text))
    m = _ISO.search(text)
    if m:
        return _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _DMY.search(text)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    m = _MDY.search(text)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    return None


def page_name(title: str) -> str:
    """The person a page title names: "Joe Bloggs (boxer)" -> "Joe Bloggs"."""
    return _PAREN.sub("", title).strip()


def _opponent(cell: str) -> tuple[str, str | None]:
    """(name, page title or None) of the opponent cell. A linked opponent is
    named by the page title, not the label: [[Floyd Mayweather Jr.|Floyd
    Mayweather]] drops the "Jr." that tells him from his father."""
    link = _LINK.search(cell)
    if link:
        title = link.group(1).strip()
        return page_name(title), title
    return _TEMPLATE.sub("", cell).strip(), None


def _result(cell: str) -> tuple[int, int] | None:
    text = _TEMPLATE.sub("", cell).strip().lower()
    if text.startswith("win"):
        return 1, 0
    if text.startswith("loss"):
        return 0, 1
    if text.startswith("draw"):
        return 1, 1
    return None


def parse_record(wikitext: str, subject: str) -> tuple[list[HistoricalBout], dict[str, int], list[str]]:
    skipped = {"no_result": 0, "bad_date": 0, "no_opponent": 0}
    table = record_table(wikitext)
    if table is None:
        return [], skipped, []
    header = _header(table)
    try:
        i_res, i_opp, i_date = header.index("result"), header.index("opponent"), header.index("date")
    except ValueError:
        return [], skipped, []
    bouts: list[HistoricalBout] = []
    opponents: list[str] = []
    for row in table.split("\n|-")[1:]:
        cells = _cells(row)
        if len(cells) <= max(i_res, i_opp, i_date):
            continue
        name, title = _opponent(cells[i_opp])
        if title:
            opponents.append(title)
        if not name:
            skipped["no_opponent"] += 1
            continue
        scores = _result(cells[i_res])
        if scores is None:
            skipped["no_result"] += 1
            continue
        day = parse_date(cells[i_date])
        # A result dated after today is a typo ("2916" for 2016); stored
        # final, it would replay last and move current ratings.
        if day is None or day > date.today():
            skipped["bad_date"] += 1
            continue
        bouts.append(HistoricalBout(day, f"wikipedia:{subject}", subject, name, *scores))
    return bouts, skipped, opponents


API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "MetricEdgePicks/1.0 (https://www.metricedgepicks.com)"


def _http_get(params: dict) -> dict:
    import httpx
    r = httpx.get(API, params={**params, "format": "json", "formatversion": 2},
                  headers={"User-Agent": USER_AGENT}, timeout=30.0)
    r.raise_for_status()
    return r.json()


def names_fighter(title: str, wikitext: str, name: str) -> bool:
    """Every word of ``name`` is in the page title (minus its "(boxer)"
    qualifier) or in the lead's bold name: "Saul Alvarez" is on the page
    "Canelo Álvarez", whose lead names Santos Saúl Álvarez Barragán."""
    words = set(normalize_name(name).split())
    lead = _LEAD_NAME.search(wikitext)
    candidates = [page_name(title)] + ([_TEMPLATE.sub("", lead.group(1))] if lead else [])
    return bool(words) and any(words <= set(normalize_name(c).split()) for c in candidates)


class WikiClient:
    """MediaWiki API reads, cached on disk, at most one live request per
    `min_interval` seconds. A cached answer -- including "no such page" -- is
    never fetched again, so dry runs and the merge reuse one download."""

    def __init__(self, cache_dir, getter=None, sleep=time.sleep, min_interval: float = 1.0):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.getter = getter or _http_get
        self.sleep = sleep
        self.min_interval = min_interval
        self.live_requests = 0
        self._last = 0.0

    def _cached(self, key: str, params: dict) -> dict:
        path = self.cache / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        wait = self.min_interval - (time.monotonic() - self._last)
        if self.live_requests and wait > 0:
            self.sleep(wait)
        data = self.getter(params)
        self._last = time.monotonic()
        self.live_requests += 1
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def page(self, title: str) -> tuple[str, str | None]:
        """(the title the API resolved ``title`` to, its wikitext or None)."""
        data = self._cached(f"page:{title}", {"action": "parse", "page": title,
                                              "prop": "wikitext", "redirects": 1})
        parsed = data.get("parse") or {}
        return parsed.get("title") or title, parsed.get("wikitext")

    def wikitext(self, title: str) -> str | None:
        return self.page(title)[1]

    def search(self, query: str) -> list[str]:
        data = self._cached(f"search:{query}", {"action": "query", "list": "search",
                                                "srsearch": query, "srlimit": 3})
        return [hit["title"] for hit in (data.get("query") or {}).get("search", [])]

    def record_page(self, name: str) -> tuple[str, str] | None:
        """The page of OUR fighter: '<name> (boxer)', then '<name>', then the
        top search hits -- the first that has a record table AND names our
        fighter (`names_fighter`). A namesake page (no record table) and
        another boxer's page (a search hit that only mentions ours: the
        2026-10-10 fetch gave "Jordan Orozco" Terence Crawford's record) are
        never accepted."""
        tried = set()
        for title in [f"{name} (boxer)", name] + self.search(f"{name} boxer"):
            if title in tried:
                continue
            tried.add(title)
            resolved, wt = self.page(title)
            if wt and record_table(wt) and names_fighter(resolved, wt, name):
                return title, wt
        return None
