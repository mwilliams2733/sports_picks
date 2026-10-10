"""UFCStats history CSVs (github.com/Greco1899/scrape_ufc_stats) -> bouts."""
from datetime import date

from backend.collectors.ufcstats_history import (HistoricalBout, name_key, read_bouts,
                                                 read_event_dates)

EVENTS = (
    "﻿EVENT,URL,DATE,LOCATION\n"
    'UFC 332: Silva vs. Wang,http://x/e1,"October 03, 2026","Salt Lake City, Utah, USA"\n'
    'UFC Fight Night: A vs. B,http://x/e2,"September 26, 2026","Las Vegas, Nevada, USA"\n'
    'UFC Fight Night: Twice,http://x/e3,"May 01, 2010","X"\n'
    'UFC Fight Night: Twice,http://x/e4,"June 05, 2012","Y"\n'
)
RESULTS = (
    "EVENT,BOUT,OUTCOME,WEIGHTCLASS,METHOD,ROUND,TIME,TIME FORMAT,REFEREE,DETAILS,URL\n"
    "UFC 332: Silva vs. Wang,Natalia Silva vs. Wang Cong,W/L,Flyweight,Decision,5,5:00,f,r,d,u1\n"
    "UFC 332: Silva vs. Wang,Deiveson Figueiredo vs. Payton Talbott,L/W,Bantam,KO/TKO,1,2:09,f,r,d,u2\n"
    "UFC Fight Night: A vs. B,Draw One vs. Draw Two,D/D,Light,Decision,3,5:00,f,r,d,u3\n"
    "UFC Fight Night: A vs. B,Nc One vs. Nc Two,NC/NC,Light,Overturned,3,5:00,f,r,d,u4\n"
    "UFC Fight Night: Twice,Old One vs. Old Two,W/L,Light,Decision,3,5:00,f,r,d,u5\n"
    "UFC 999: Missing,Gone One vs. Gone Two,W/L,Light,Decision,3,5:00,f,r,d,u6\n"
    "UFC 332: Silva vs. Wang,No Separator Here,W/L,Light,Decision,3,5:00,f,r,d,u7\n"
)


def _files(tmp_path):
    e, r = tmp_path / "events.csv", tmp_path / "results.csv"
    e.write_text(EVENTS, encoding="utf-8")
    r.write_text(RESULTS, encoding="utf-8")
    return e, r


def test_name_key_ignores_order_accents_punctuation_and_suffixes():   # Review Focus 2
    assert name_key("Wang Cong") == name_key("Cong Wang")
    assert name_key("José Aldo") == name_key("Jose Aldo")
    assert name_key("Alexander Hernandez Jr.") == name_key("alexander hernandez")
    assert name_key("Abdul-Kareem Al-Selwady") == name_key("Abdul Kareem Al Selwady")
    assert name_key("Jon Jones") != name_key("Jon Jones Smith")


def test_event_dates_and_an_ambiguous_event_name(tmp_path):
    events, _ = _files(tmp_path)
    dates = read_event_dates(events)
    assert dates["UFC 332: Silva vs. Wang"] == date(2026, 10, 3)
    assert dates["UFC Fight Night: Twice"] is None          # two dates: never guessed


def test_bouts_wins_losses_draws_and_skips(tmp_path):              # Review Focus 3
    events, results = _files(tmp_path)
    bouts, skipped = read_bouts(results, read_event_dates(events))
    assert bouts == [
        HistoricalBout(date(2026, 10, 3), "UFC 332: Silva vs. Wang", "Natalia Silva", "Wang Cong", 1, 0),
        HistoricalBout(date(2026, 10, 3), "UFC 332: Silva vs. Wang", "Deiveson Figueiredo", "Payton Talbott", 0, 1),
        HistoricalBout(date(2026, 9, 26), "UFC Fight Night: A vs. B", "Draw One", "Draw Two", 1, 1),
    ]
    assert skipped == {"no_result": 1, "event_date_unknown": 2, "bad_bout": 1}


def test_verified_spelling_variants_are_one_fighter():               # Review Focus 2
    # Measured 2026-10-10: the odds feed and UFCStats spell these four
    # fighters differently in ways name_key cannot unify (given name vs
    # nickname, spacing). Each pair was checked to be the same person.
    for feed, ufcstats in [("Alex Volkanovski", "Alexander Volkanovski"),
                           ("Lupita Godinez", "Loopy Godinez"),
                           ("Su Young You", "SuYoung You"),
                           ("Timothy Cuamba", "Timmy Cuamba")]:
        assert name_key(feed) == name_key(ufcstats), feed
    assert name_key("Alex Perez") != name_key("Alexander Volkanovski")
