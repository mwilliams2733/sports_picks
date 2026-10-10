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


from backend.collectors.wikipedia_boxing import WikiClient  # noqa: E402


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
    client = WikiClient(tmp_path / "a", getter=fake, sleep=lambda s: None)
    assert client.record_page("Craig Lewis")[0] == "Craig Lewis (American boxer)"
    assert WikiClient(tmp_path / "b", getter=FakeWiki({}), sleep=lambda s: None).record_page("Nobody") is None


def test_cached_titles_are_never_fetched_again_and_live_calls_are_spaced(tmp_path):   # Review Focus 5
    fake = FakeWiki({"A (boxer)": PAGE})
    slept = []
    c1 = WikiClient(tmp_path, getter=fake, sleep=slept.append, min_interval=1.0)
    c1.wikitext("A (boxer)")
    c1.wikitext("Missing Page")
    n = len(fake.calls)
    c2 = WikiClient(tmp_path, getter=fake, sleep=slept.append)
    assert c2.wikitext("A (boxer)") == PAGE and c2.wikitext("Missing Page") is None
    assert len(fake.calls) == n and c2.live_requests == 0      # both answered from the cache
    assert all(s <= 1.0 for s in slept) and len(slept) >= 1      # spaced, never more than the interval


def test_iso_and_wrapped_dates_found_in_the_real_fetch():
    # 2026-10-10 live fetch: ~3,500 rows were skipped as bad dates, nearly all
    # ISO dates or dates wrapped in {{small|...}} (the wrapper strip ate them).
    assert parse_date("1997-07-04") == date(1997, 7, 4)
    assert parse_date("2025-04-5") == date(2025, 4, 5)
    assert parse_date("2016–06–25") == date(2016, 6, 25)          # en dashes
    assert parse_date("{{small|1994-04-25}}") == date(1994, 4, 25)
    assert parse_date("{{small|30 May 2015}}") == date(2015, 5, 30)
    assert parse_date("{{nowrap|Nov 8, 2014}}") == date(2014, 11, 8)
    assert parse_date("[[Fury vs. Usyk|2024-05-18]]") == date(2024, 5, 18)
    assert parse_date("Dec 17 2022") == date(2022, 12, 17)
    assert parse_date("04/05/2019") is None        # day/month order unknowable: stays skipped
    assert parse_date("2019-13-40") is None


def test_a_search_hit_about_another_boxer_is_never_our_fighter(tmp_path):
    # 2026-10-10 live fetch: "Jordan Orozco" resolved through search to Terence
    # Crawford's page and "Ivan Rosado" to Canelo's -- unknowns priced as champions.
    crawford = "'''Terence Crawford''' is a boxer.\n" + PAGE
    fake = FakeWiki({"Terence Crawford": crawford}, search_hits=["Terence Crawford"])
    assert WikiClient(tmp_path / "a", getter=fake, sleep=lambda s: None).record_page("Jordan Orozco") is None
    # A page under another title is still accepted when its lead names our fighter.
    canelo = "'''Santos Saúl Álvarez Barragán''' (born 1990), known as Canelo, is a boxer.\n" + PAGE
    fake = FakeWiki({"Canelo Álvarez": canelo}, search_hits=["Canelo Álvarez"])
    assert WikiClient(tmp_path / "b", getter=fake, sleep=lambda s: None).record_page("Saul Alvarez")[0] == "Canelo Álvarez"


def test_a_linked_opponent_is_named_by_the_page_title_not_the_label():
    # [[Floyd Mayweather Jr.|Floyd Mayweather]] -- the label drops the "Jr."
    # that tells him from his father.
    page = PAGE.replace("[[Fabio Wardley]]", "[[Floyd Mayweather Jr.|Floyd Mayweather]]")
    bouts, _, _ = parse_record(page, subject="Daniel Dubois")
    assert bouts[0].fighter_b == "Floyd Mayweather Jr."
