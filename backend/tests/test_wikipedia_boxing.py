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
