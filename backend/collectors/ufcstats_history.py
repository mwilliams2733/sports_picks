"""UFC fight history from the UFCStats CSVs published by
github.com/Greco1899/scrape_ufc_stats (GPL-3.0, refreshed daily).

`ufc_event_details.csv` (EVENT, URL, DATE "October 03, 2026", LOCATION) gives
each event's date; `ufc_fight_results.csv` (EVENT, BOUT "A vs. B", OUTCOME
"W/L" | "L/W" | "D/D" | "NC/NC", ...) gives each bout. Parsing only: the
importer (backend/scripts/import_ufc_history.py) decides what reaches the db.

Scores follow the grader's combat convention: winner 1, loser 0, draw 1/1.
A no-contest (or any other outcome) is skipped, never guessed; so is a bout
whose event has no date, or two different dates under one name.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from backend.collectors.ufc import normalize_name

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
_OUTCOMES = {"W/L": (1, 0), "L/W": (0, 1), "D/D": (1, 1)}


def name_key(name: str) -> str:
    """A fighter's name as an order-free key: "Wang Cong" == "Cong Wang";
    accents, punctuation, hyphens and Jr./Sr./II-IV are ignored."""
    tokens = [t for t in normalize_name(name).split() if t not in _SUFFIXES]
    return " ".join(sorted(tokens))


@dataclass(frozen=True)
class HistoricalBout:
    date: date
    event: str
    fighter_a: str
    fighter_b: str
    a_score: int
    b_score: int


def read_event_dates(path: str | Path) -> dict[str, date | None]:
    """Event name -> date; None when one name carries two different dates."""
    seen: dict[str, set[date]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = row["EVENT"].strip()
            day = datetime.strptime(row["DATE"].strip(), "%B %d, %Y").date()
            seen.setdefault(name, set()).add(day)
    return {name: (next(iter(days)) if len(days) == 1 else None) for name, days in seen.items()}


def read_bouts(results_path: str | Path,
               event_dates: dict[str, date | None]) -> tuple[list[HistoricalBout], dict[str, int]]:
    skipped = {"no_result": 0, "event_date_unknown": 0, "bad_bout": 0}
    bouts: list[HistoricalBout] = []
    with open(results_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            scores = _OUTCOMES.get(row["OUTCOME"].strip())
            if scores is None:
                skipped["no_result"] += 1
                continue
            event = row["EVENT"].strip()
            day = event_dates.get(event)
            if day is None:
                skipped["event_date_unknown"] += 1
                continue
            a, sep, b = row["BOUT"].partition(" vs. ")
            if not sep or not a.strip() or not b.strip():
                skipped["bad_bout"] += 1
                continue
            bouts.append(HistoricalBout(day, event, a.strip(), b.strip(), *scores))
    return bouts, skipped
