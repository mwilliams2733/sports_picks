# Boxing Fight History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give boxing fighters real fight histories from the "Professional boxing record" tables on their Wikipedia pages, so boxing ratings exist (there are none today) and the 110 March-July boxing games stuck as `canceled` get their results — while keeping every boxing pick tracking-only.

**Architecture:** A pure parser (`backend/collectors/wikipedia_boxing.py`) turns a boxer page's wikitext into `HistoricalBout`s (the UFC importer's type). A small client fetches wikitext through Wikipedia's official MediaWiki API — one request per second, a descriptive User-Agent, every response cached to disk so dry runs and the merge never re-fetch. An importer script (`backend/scripts/import_boxing_history.py`) resolves the page of every fighter in our boxing games, follows one hop to their listed opponents' pages, and hands all bouts to the existing `import_ufc_history.import_bouts(sport="boxing", finalize_unfinished=...)` (name-key matching, no double counts, stuck games completed only when no pick or paper bet is attached), then replays boxing Elo with `rebuild_combat_elo("boxing")`. `TRACKING_ONLY_SPORTS` gains `"boxing"`.

**Tech Stack:** Python 3.11, httpx (already a dependency), SQLAlchemy/SQLite, pytest.

**Spec:** No separate spec. Owner decisions of 2026-10-10 (below) plus docs/FINDINGS.md (MMA sections: the same blend backed every MMA underdog). Binding.

## Owner decisions (2026-10-10)

- Boxing picks become **tracking-only** (`TRACKING_ONLY_SPORTS += ("boxing",)`), like MMA, until measured.
- Fetching boxer pages from **Wikipedia's official API** (CC BY-SA content) at build and merge time is **approved**: polite (≤ 1 request/second, descriptive User-Agent), pages read as data only.
- Scope: fighters in our boxing games **plus one hop** — the past opponents linked on their record tables.
- The 110 boxing games stuck `canceled` (2026-03..07) are **finalized** from Wikipedia results where found and no pick or paper bet is attached (`--finalize-unfinished`, the MMA rule).

## Facts measured 2026-10-10 (live db, read-only)

- Boxing: 68 `scheduled` games (2026-09-23..12-19), 110 `canceled` (2026-03-20..07-05), 0 `final`; 333 fighter rows; **0** boxing Elo rows; 36 published boxing picks. 42 fighters on cards in the next 60 days.
- Wikipedia's record table format (Daniel Dubois, via the API): section `==Professional boxing record==`, a `{| class="wikitable"` table with header `No. | Result | Record | Opponent | Type | Round, time | Date | Location | Notes`; rows like `|{{yes2}}Win` / `|{{no2}}Loss`, opponent `|style="text-align:left;"|[[Fabio Wardley]]`, date `|9 May 2026` or `|[[Oleksandr Usyk vs. Daniel Dubois II|19 Jul 2025]]`.

## Global Constraints

- Network: only `https://en.wikipedia.org/w/api.php`, ≤ 1 request/second, header `User-Agent: MetricEdgePicks/1.0 (https://www.metricedgepicks.com)`. No personal data in requests. Responses are cached under a directory the caller passes; a cached title (including a cached "missing") is never fetched again.
- Tests never touch the network: the client takes an injectable `getter` and `sleep`.
- Reuse, don't copy: bouts go through `import_ufc_history.import_bouts` and Elo through `rebuild_combat_elo`; fighter matching is `ufcstats_history.name_key`.
- Existing games, picks and results are never modified, except stuck non-final games completed under `--finalize-unfinished` with no pick/paper bet attached.
- Live-db writes only at merge, scheduler and uvicorn stopped, after `sqlite3.backup`.
- Write regex/backslash code with the Write tool, never bash heredocs; grep the written file for `\d` afterwards. Backend tests from the worktree with the MAIN venv: `/c/Users/mwill/Documents/mwilliams2733/sports_picks/.venv/Scripts/python -m pytest <file> -q -p no:cacheprovider`. Delete `__pycache__` after each mutation write and restore.
- Commit only named files; never `config.yaml`. Trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Wikitext quirks:** inline `||` cells, `style=...|value` attributes, `[[Target|Display]]` links, `<ref>`s, `{{dts|YYYY|MM|DD}}` dates, draws, no-contests, and future bouts listed with no result — a row is parsed exactly or skipped and counted, never guessed. Pinned in Task 1.
2. **The wrong page:** a namesake (a politician, an actor, another boxer) — only a page with a Professional boxing record table is accepted; the subject is OUR fighter's name. Pinned in Task 2; residual namesake risk reported in Task 5.
3. **The same bout from both fighters' pages** (one day apart across time zones): one game. Pinned in Task 3.
4. **A stuck canceled game:** completed only with no pick/paper bet; otherwise reported. Pinned in Task 3.
5. **Politeness and repeat runs:** cached titles never re-fetched; ≤ 1 live request/second. Pinned in Task 2.

---

### Task 1: Parse a boxer page's record table

**Files:**
- Create: `backend/collectors/wikipedia_boxing.py` (parsing part)
- Create: `backend/tests/test_wikipedia_boxing.py`

**Interfaces:**
- Consumes: `backend.collectors.ufcstats_history.HistoricalBout`.
- Produces: `record_table(wikitext: str) -> str | None`; `parse_date(cell: str) -> date | None`; `parse_record(wikitext: str, subject: str) -> tuple[list[HistoricalBout], dict[str, int], list[str]]` (bouts, skip counts keyed `no_result` / `bad_date` / `no_opponent`, opponent page titles in table order).

- [ ] **Step 1: Failing tests** — `backend/tests/test_wikipedia_boxing.py`:

```python
from datetime import date

from backend.collectors.ufcstats_history import HistoricalBout
from backend.collectors.wikipedia_boxing import parse_date, parse_record, record_table

PAGE = """
'''Daniel Dubois''' is a boxer.
==Professional boxing record==
{| class="wikitable" style="text-align:center"
| colspan="8" |{{BoxingRecordSummary|...}}
|}
{| class="wikitable" style="text-align:center"
|-
!{{abbr|No.|Number}}
!Result
!Record
!Opponent
!Type
!Round, time
!Date
!Location
!Notes
|-
|5
|{{yes2}}Win
|4–1
|style="text-align:left;"|[[Fabio Wardley]]
|TKO
|11 (12), {{small|0:28}}
|9 May 2026
|Wembley
|
|-
|4
|{{no2}}Loss
|3–1
|style="text-align:left;"|[[Oleksandr Usyk]]<ref>x</ref>
|KO
|5 (12)
|[[Oleksandr Usyk vs. Daniel Dubois II|19 Jul 2025]]
|London
|
|-
| 3 || {{draw2}}Draw || 3–0–1 || style="text-align:left;" | [[Joe Bloggs (boxer)|Joe Bloggs]] || SD || 10 || {{dts|2024|03|02}} || X ||
|-
|2
|{{n/a}}NC
|3–0
|style="text-align:left;"|[[Some Guy]]
|NC
|2 (10)
|1 Jan 2024
|X
|
|-
|1
|{{yes2}}Win
|1–0
|style="text-align:left;"|Unlinked Opponent
|KO
|1 (6)
|April 4, 2017
|X
|
|-
|6
|–
|–
|style="text-align:left;"|[[Future Foe]]
|–
|–
|17 Oct 2026
|X
|Scheduled
|-
|0
|{{yes2}}Win
|0–0
|style="text-align:left;"|[[Mystery Man]]
|KO
|1
|sometime
|X
|
|}

==Personal life==
"""


def test_finds_the_record_table_not_the_summary_table():
    t = record_table(PAGE)
    assert t is not None and "Opponent" in t and "BoxingRecordSummary" not in t
    assert record_table("no record here") is None


def test_dates_in_every_form_we_meet():
    assert parse_date("9 May 2026") == date(2026, 5, 9)
    assert parse_date("[[Usyk vs. Dubois II|19 Jul 2025]]") == date(2025, 7, 19)
    assert parse_date("{{dts|2024|03|02}}") == date(2024, 3, 2)
    assert parse_date("April 4, 2017") == date(2017, 4, 4)
    assert parse_date("sometime") is None


def test_rows_become_bouts_and_the_rest_are_counted():           # Review Focus 1
    bouts, skipped, opponents = parse_record(PAGE, subject="Daniel Dubois")
    ev = "wikipedia:Daniel Dubois"
    assert bouts == [
        HistoricalBout(date(2026, 5, 9), ev, "Daniel Dubois", "Fabio Wardley", 1, 0),
        HistoricalBout(date(2025, 7, 19), ev, "Daniel Dubois", "Oleksandr Usyk", 0, 1),
        HistoricalBout(date(2024, 3, 2), ev, "Daniel Dubois", "Joe Bloggs", 1, 1),
        HistoricalBout(date(2017, 4, 4), ev, "Daniel Dubois", "Unlinked Opponent", 1, 0),
    ]
    assert skipped == {"no_result": 2, "bad_date": 1, "no_opponent": 0}   # NC + scheduled; "sometime"
    assert opponents == ["Fabio Wardley", "Oleksandr Usyk", "Joe Bloggs (boxer)", "Some Guy",
                         "Future Foe", "Mystery Man"]
```

Run → FAIL (`ModuleNotFoundError`).

- [ ] **Step 2: Implement** — `backend/collectors/wikipedia_boxing.py` (Write tool; this file is regex-heavy):

```python
"""Boxing fight history from Wikipedia "Professional boxing record" tables.

Parsing here; fetching (WikiClient) below. A row is either parsed exactly or
skipped and counted -- never guessed: no-contests and bouts listed before
they happen have no result; an unreadable date is skipped.
Scores follow the grader's combat convention: winner 1, loser 0, draw 1/1.
"""
from __future__ import annotations

import re
from datetime import date

from backend.collectors.ufcstats_history import HistoricalBout

_SECTION = re.compile(r"^==+\s*Professional boxing record\s*==+\s*$", re.M | re.I)
_NEXT_L2 = re.compile(r"^==[^=]", re.M)
_TABLE = re.compile(r"^\{\|.*?^\|\}", re.M | re.S)
_REF = re.compile(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", re.S | re.I)
_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_DTS = re.compile(r"\{\{\s*dts\s*\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", re.I)
_DMY = re.compile(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})")
_MDY = re.compile(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})")
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
        if re.search(r"^!.*Opponent", table, re.M | re.I):
            return table
    return None


def _strip_attrs(cell: str) -> str:
    """'style="x"|value' -> 'value' (a '|' outside [[...]] and {{...}})."""
    depth_link = depth_tpl = 0
    cut = -1
    i = 0
    while i < len(cell):
        two = cell[i:i + 2]
        if two == "[[":
            depth_link += 1; i += 2; continue
        if two == "]]":
            depth_link = max(0, depth_link - 1); i += 2; continue
        if two == "{{":
            depth_tpl += 1; i += 2; continue
        if two == "}}":
            depth_tpl = max(0, depth_tpl - 1); i += 2; continue
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
                text = _strip_attrs(part)
                text = re.sub(r"\{\{\s*abbr\s*\|([^|}]*)\|[^}]*\}\}", r"\1", text, flags=re.I)
                names.append(_TEMPLATE.sub("", text).strip().lower())
    return names


def parse_date(cell: str) -> date | None:
    m = _DTS.search(cell)
    if m:
        return _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    link = _LINK.search(cell)
    text = (link.group(2) or link.group(1)) if link else cell
    text = _TEMPLATE.sub("", text)
    m = _DMY.search(text)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    m = _MDY.search(text)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _safe(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    return None


def _safe(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _opponent(cell: str) -> tuple[str, str | None]:
    """(display name, page title or None) of the opponent cell."""
    link = _LINK.search(cell)
    if link:
        title = link.group(1).strip()
        return (link.group(2) or title).strip(), title
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
        if day is None:
            skipped["bad_date"] += 1
            continue
        bouts.append(HistoricalBout(day, f"wikipedia:{subject}", subject, name, *scores))
    return bouts, skipped, opponents
```

After writing: `grep -n 'd{4}' backend/collectors/wikipedia_boxing.py` must show the regexes intact (backslash trap).

- [ ] **Step 3: Run** → PASS (3). A mismatch on the fixture is a parser bug: fix the parser, not the expected values, unless the fixture itself is wrong (ledger a ruling).
- [ ] **Step 4: Mutations** — (a) `_strip_attrs` returning `cell` unchanged → opponent/result tests FAIL; (b) drop the `_DTS` branch → the dates test FAILS. Restore.
- [ ] **Step 5: Commit** `feat(boxing): parse Wikipedia professional boxing record tables`.

---

### Task 2: A polite, cached Wikipedia client and page resolution

**Files:**
- Modify: `backend/collectors/wikipedia_boxing.py` (append `WikiClient`)
- Modify: `backend/tests/test_wikipedia_boxing.py`

**Interfaces:**
- Produces: `API = "https://en.wikipedia.org/w/api.php"`; `USER_AGENT`; `class WikiClient(cache_dir: Path, getter=None, sleep=time.sleep, min_interval: float = 1.0)` with `wikitext(title) -> str | None`, `search(query) -> list[str]`, `record_page(name) -> tuple[str, str] | None`, attribute `live_requests: int`.

- [ ] **Step 1: Failing tests** (append):

```python
import json

from backend.collectors.wikipedia_boxing import WikiClient


class FakeWiki:
    def __init__(self, pages, search_hits=()):
        self.pages, self.hits, self.calls = pages, list(search_hits), []

    def __call__(self, params):
        self.calls.append(dict(params))
        if params.get("action") == "parse":
            wt = self.pages.get(params["page"])
            return {"parse": {"wikitext": wt}} if wt is not None else {"error": {"code": "missingtitle"}}
        return {"query": {"search": [{"title": t} for t in self.hits]}}


def test_resolves_the_boxer_page_and_rejects_a_namesake(tmp_path):     # Review Focus 2
    # No "(boxer)" page; the bare title is a politician; search finds the boxer.
    fake = FakeWiki({"Joshua Edwards": "'''Joshua Edwards''' is a politician.",
                     "Joshua Edwards (American boxer)": PAGE},
                    search_hits=["Joshua Edwards", "Joshua Edwards (American boxer)"])
    client = WikiClient(tmp_path, getter=fake, sleep=lambda s: None)
    title, wt = client.record_page("Joshua Edwards")
    assert title == "Joshua Edwards (American boxer)" and record_table(wt)


def test_falls_back_to_search_and_returns_none_when_nothing_fits(tmp_path):
    fake = FakeWiki({"Craig Lewis (American boxer)": PAGE}, search_hits=["Craig Lewis (American boxer)"])
    client = WikiClient(tmp_path, getter=fake, sleep=lambda s: None)
    assert client.record_page("Craig Lewis")[0] == "Craig Lewis (American boxer)"
    assert WikiClient(tmp_path, getter=FakeWiki({}), sleep=lambda s: None).record_page("Nobody") is None


def test_cached_titles_are_never_fetched_again_and_live_calls_are_spaced(tmp_path):   # Review Focus 5
    fake = FakeWiki({"A (boxer)": PAGE})
    slept = []
    c1 = WikiClient(tmp_path, getter=fake, sleep=slept.append, min_interval=1.0)
    c1.wikitext("A (boxer)"); c1.wikitext("Missing Page")
    n = len(fake.calls)
    c2 = WikiClient(tmp_path, getter=fake, sleep=slept.append)
    assert c2.wikitext("A (boxer)") == PAGE and c2.wikitext("Missing Page") is None
    assert len(fake.calls) == n and c2.live_requests == 0      # both answered from the cache
    assert all(s <= 1.0 for s in slept) and len(slept) >= 1      # spaced, never more than the interval
```

Run → FAIL (`ImportError: WikiClient`).

- [ ] **Step 2: Implement** (append to `wikipedia_boxing.py`, Write/Edit tool):

```python
import hashlib
import json
import time
from pathlib import Path

API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "MetricEdgePicks/1.0 (https://www.metricedgepicks.com)"


def _http_get(params: dict) -> dict:
    import httpx
    r = httpx.get(API, params={**params, "format": "json", "formatversion": 2},
                  headers={"User-Agent": USER_AGENT}, timeout=30.0)
    r.raise_for_status()
    return r.json()


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

    def wikitext(self, title: str) -> str | None:
        data = self._cached(f"page:{title}", {"action": "parse", "page": title,
                                              "prop": "wikitext", "redirects": 1})
        return (data.get("parse") or {}).get("wikitext")

    def search(self, query: str) -> list[str]:
        data = self._cached(f"search:{query}", {"action": "query", "list": "search",
                                                "srsearch": query, "srlimit": 3})
        return [hit["title"] for hit in (data.get("query") or {}).get("search", [])]

    def record_page(self, name: str) -> tuple[str, str] | None:
        """The page of OUR fighter: '<name> (boxer)', then '<name>', then the
        top search hits -- the first that has a record table. A namesake page
        (no record table) is never accepted."""
        tried = set()
        for title in [f"{name} (boxer)", name] + self.search(f"{name} boxer"):
            if title in tried:
                continue
            tried.add(title)
            wt = self.wikitext(title)
            if wt and record_table(wt):
                return title, wt
        return None
```

(The first live request is not delayed; every later live request waits until `min_interval` has passed since the previous one. The test's `slept` values are therefore ≤ the interval.)

- [ ] **Step 3: Run** → PASS (6). **Step 4: Mutation** — remove the `if path.exists()` read → the cache test FAILS. Restore.
- [ ] **Step 5: Commit** `feat(boxing): polite cached Wikipedia client and boxer page resolution`.

---

### Task 3: Import boxing history (one hop) and replay boxing Elo

**Files:**
- Create: `backend/scripts/import_boxing_history.py`
- Create: `backend/tests/test_import_boxing_history.py`
- Modify: `backend/scripts/import_ufc_history.py` (report each matched/finalized game id once)

**Interfaces:**
- Consumes: `WikiClient`, `parse_record`, `record_table`; `import_ufc_history.import_bouts(session, bouts, sport, finalize_unfinished)`, `import_ufc_history.coverage(session, today, days, sport)`; `rebuild_combat_elo(session, "boxing")`.
- Produces: `fighters_to_fetch(session, since: date) -> list[str]` (names of boxing fighters in games not `final` dated ≥ `since`); `collect_bouts(client, names, hops=1) -> tuple[list[HistoricalBout], dict]`; `main(argv)` with `--cache-dir`, `--db`, `--since` (default 2026-01-01), `--hops` (default 1), `--finalize-unfinished`, `--apply`.

- [ ] **Step 1: Failing tests** — `backend/tests/test_import_boxing_history.py`:

```python
from datetime import date

from backend.models import Base, Game, PickModel, StrategyModel, Team
from backend.scripts.import_boxing_history import collect_bouts, fighters_to_fetch
from backend.scripts.import_ufc_history import coverage, import_bouts

HEAD = """==Professional boxing record==
{| class="wikitable"
|-
!No.
!Result
!Opponent
!Date
"""


def page(rows):
    body = "".join(f"|-\n|{n}\n|{res}\n|[[{opp}]]\n|{d}\n" for n, res, opp, d in rows)
    return HEAD + body + "|}\n"


class Fake:
    def __init__(self, pages):
        self.pages = pages

    def __call__(self, params):
        if params.get("action") == "parse":
            wt = self.pages.get(params["page"])
            return {"parse": {"wikitext": wt}} if wt else {"error": {}}
        return {"query": {"search": []}}


PAGES = {
    "Ann Able (boxer)": page([(2, "{{yes2}}Win", "Bea Bold", "1 Jun 2026"),
                              (1, "{{yes2}}Win", "Cat Cole", "1 Jan 2025")]),
    "Bea Bold": page([(2, "{{no2}}Loss", "Ann Able (boxer)|Ann Able", "2 Jun 2026"),   # same bout, a day off
                      (1, "{{yes2}}Win", "Dee Dunn", "1 Mar 2025")]),
    "Cat Cole": page([(1, "{{no2}}Loss", "Ann Able (boxer)|Ann Able", "1 Jan 2025")]),
}


def _db(session):
    Base.metadata.create_all(session.get_bind())
    for tid, name in [(1, "Ann Able"), (2, "Bea Bold")]:
        session.add(Team(id=tid, name=name, abbreviation=name, sport="boxing"))
    session.add(StrategyModel(id=1, name="x", config_json="{}"))
    session.flush()
    session.add(Game(id=10, sport="boxing", season="2026", date=date(2026, 6, 1), status="canceled",
                     home_team_id=1, away_team_id=2))
    session.add(Game(id=11, sport="boxing", season="2026", date=date(2026, 11, 7), status="scheduled",
                     home_team_id=2, away_team_id=1))
    session.commit()


def test_one_hop_collects_both_sides_and_the_opponents_records(db_engine, db_session, tmp_path):
    from backend.collectors.wikipedia_boxing import WikiClient
    _db(db_session)
    names = fighters_to_fetch(db_session, since=date(2026, 1, 1))
    assert sorted(names) == ["Ann Able", "Bea Bold"]
    client = WikiClient(tmp_path, getter=Fake(PAGES), sleep=lambda s: None)
    bouts, stats = collect_bouts(client, names, hops=1)
    assert stats["pages_found"] == 2 and stats["pages_missing"] == [] and stats["opponent_pages"] == 1
    assert len(bouts) == 5          # Ann x2, Bea x2, Cat x1 (duplicates collapse at import)


def test_the_same_bout_on_two_pages_is_one_game_and_the_stuck_one_is_finalized(db_engine, db_session, tmp_path):   # Review Focus 3, 4
    from backend.collectors.wikipedia_boxing import WikiClient
    _db(db_session)
    client = WikiClient(tmp_path, getter=Fake(PAGES), sleep=lambda s: None)
    bouts, _ = collect_bouts(client, fighters_to_fetch(db_session, date(2026, 1, 1)), hops=1)
    summary = import_bouts(db_session, bouts, sport="boxing", finalize_unfinished=True)
    stuck = db_session.get(Game, 10)
    assert (stuck.status, stuck.home_score, stuck.away_score) == ("final", 1, 0)     # Ann (home) won
    assert summary["finalized"] == [10]
    assert db_session.query(Game).filter(Game.sport == "boxing", Game.status == "final").count() == 3  # 10, Ann-Cat, Bea-Dee
    assert coverage(db_session, date(2026, 10, 10), days=60, sport="boxing")["games_both_known"] == 1


def test_a_stuck_game_with_a_pick_is_reported_not_finalized(db_engine, db_session, tmp_path):   # Review Focus 4
    from backend.collectors.wikipedia_boxing import WikiClient
    _db(db_session)
    db_session.add(PickModel(game_id=10, strategy_id=1, pick_type="moneyline", pick_value="HOME ML",
                             confidence=3, edge_pct=5.0, odds_at_pick=100))
    db_session.commit()
    client = WikiClient(tmp_path, getter=Fake(PAGES), sleep=lambda s: None)
    bouts, _ = collect_bouts(client, fighters_to_fetch(db_session, date(2026, 1, 1)), hops=1)
    summary = import_bouts(db_session, bouts, sport="boxing", finalize_unfinished=True)
    assert db_session.get(Game, 10).status == "canceled" and summary["matched_non_final"] == [10]
```

Run → FAIL (`ModuleNotFoundError`).

- [ ] **Step 2: Implement** — `backend/scripts/import_boxing_history.py`:

```python
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
import re
import sys
from collections import Counter
from datetime import date

from backend.collectors.wikipedia_boxing import WikiClient, parse_record, record_table
from backend.models import Game, Team
from backend.scripts.dedupe_combat_games import rebuild_combat_elo
from backend.scripts.import_ufc_history import coverage, import_bouts
from backend.time_utils import et_today

_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


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
            rows, skipped, _ = parse_record(wikitext, subject=_PAREN.sub("", title))
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
```

Note `import_bouts` reads `Team.name` keys for the sport and creates rows for new opponents (`abbreviation=name`), exactly as for MMA.

Also, in `backend/scripts/import_ufc_history.py`, report each game once: Wikipedia lists a bout on BOTH fighters' pages, so one stuck game is matched twice. Change the return to `"matched_non_final": sorted(set(non_final)), "finalized": sorted(set(finalized))` (the pick test above expects `[10]`, not `[10, 10]`); `test_import_ufc_history.py` must still pass.

- [ ] **Step 3: Run** the three tests + `test_import_ufc_history.py` → PASS.
- [ ] **Step 4: Mutation** — `hops >= 1` → `hops >= 2` → the one-hop test FAILS (`opponent_pages` 0). Restore.
- [ ] **Step 5: Commit** `feat(boxing): import Wikipedia boxing history (one hop) and replay boxing Elo`.

---

### Task 4: Boxing picks are tracking-only (owner decision)

**Files:**
- Modify: `backend/pipeline/pick_generator.py` (`TRACKING_ONLY_SPORTS`)
- Modify: `backend/tests/test_no_information_picks.py`

- [ ] **Step 1: Failing test** (append; mirrors `test_every_mma_pick_is_tracking_only_for_now`):

```python
def test_every_boxing_pick_is_tracking_only_for_now(db_engine, db_session, monkeypatch):
    # Owner, 2026-10-10: boxing uses the uncalibrated blend that backed every
    # MMA underdog; its picks are recorded, not published, until measured.
    Base.metadata.create_all(db_engine)
    db_session.add_all([Team(id=1, name="B One", abbreviation="B One", sport="boxing"),
                        Team(id=2, name="B Two", abbreviation="B Two", sport="boxing")])
    db_session.flush()
    db_session.add(Game(id=1, sport="boxing", season="2026", date=DAY, home_team_id=1,
                        away_team_id=2, status="scheduled"))
    db_session.flush()
    db_session.add_all([
        Odds(game_id=1, bookmaker="dk", moneyline_home=-150, moneyline_away=130,
             spread_home=0.0, spread_away=0.0, over_under=0.0,
             timestamp=datetime(2026, 3, 1, 18, 0)),
        StrategyModel(id=1, name="combat_sports", config_json="{}", is_active=True),
    ])
    db_session.commit()
    monkeypatch.setattr(pg, "CombatSportsStrategy", _fake_strategy(0.6))
    pg.generate_and_store_picks(db_session, strategy_id=1, target_date=DAY)
    assert db_session.query(PickModel).one().tracking_only is True
```

Run → FAIL. **Step 2:** `TRACKING_ONLY_SPORTS = ("mma", "boxing")` with the comment extended ("boxing: owner, 2026-10-10, same blend, unmeasured"). Run → PASS; the demote script picks boxing up automatically (it reads the constant) — confirm with its test file. **Step 3: Mutation** — remove `"boxing"` → FAIL. **Step 4: Commit** `fix(boxing): boxing picks tracking-only until measured (owner decision)`; full backend suite → all pass (record count).

---

### Task 5: Dry run on real data, measure, document

**Files:** `docs/FINDINGS.md`, `docs/data-dictionary.md`

- [ ] **Step 1:** Snapshot the live db (`sqlite3.backup` → `<scratchpad>/bx.db`). Run with a NEW cache dir `<scratchpad>/wiki-cache-<date>` (it becomes the merge's cache): `python -m backend.scripts.import_boxing_history --cache-dir <cache> --db <scratchpad>/bx.db --finalize-unfinished --apply`. Ledger: fighters, pages found / missing (+ the missing names), opponent pages, rows skipped by reason, live requests, import summary (inserted, duplicates, teams_created, finalized count of the 110, matched_non_final), Elo bouts replayed, coverage before -> after (next 60 days).
- [ ] **Step 2: Sanity** (read-only on the snapshot): top-10 boxing Elo names (recognisable champions?); five random finalized games — check winner against the Wikipedia row by hand; near-duplicate boxing bouts (same pair ≤ 2 days) = 0 new; for five missing pages, say why (no article / no record table / namesake). Regenerate picks for the next two boxing card dates as the scheduler does (`generate_and_store_picks(session, <active game strategy id>, day, sports=("boxing",))`), roll back: picks, tracking-only count (all), underdog share, mean model vs market no-vig. Namesake check: any fetched page whose infobox birth year makes a 2026 bout impossible (skip if not readily available; say so).
- [ ] **Step 3:** FINDINGS section `## 2026-10-10 — Boxing history from Wikipedia` with all numbers, the CC BY-SA source note, the namesake/coverage limitations, and the underdog result. Data dictionary: boxing `games` now hold Wikipedia bouts (no espn_id/odds_api_id/start_time/odds), finalized stuck games listed by count and rule, boxing Elo replayed, boxing picks tracking-only from the merge.
- [ ] **Step 4:** Commit `docs: boxing history measured on a snapshot`.

---

## Merge notes

1. Check ET and that no boxing card is in progress; save `scheduler.log`.
2. Stop the scheduler and uvicorn; `sqlite3.backup` -> `sports_picks.backup-<stamp>-pre-boxing-history.db`.
3. `git merge --no-ff feat/boxing-history`.
4. Dry run on the live db with the Task 5 cache (`--cache-dir <same dir> --finalize-unfinished`, no `--apply`): compare with the snapshot; `live requests` should be ~0 (cache). Then `--apply`.
5. `python -m backend.scripts.demote_no_information_picks` (dry run, then apply): moves published, un-emailed boxing picks on unstarted bouts to tracking.
6. Restart uvicorn and the scheduler detached; verify endpoints, boxing Elo count and top names.
