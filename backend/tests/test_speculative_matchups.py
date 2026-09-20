"""Futures markets are not fixtures.

The Odds API offers speculative matchups -- "who will X fight next" -- as
ordinary events, structurally identical to a real bout. They all carry the
same far-future commence_time, which the API uses for an undated event, and
`_ensure_game_from_odds` created a Game row for each. On 2026-09-19 that was
25 boxing and 10 mma rows on 2026-12-31: eleven different "Moses Itauma vs
X", seven "Daniel Dubois vs X". They would have been picked on that date.

Nothing in the payload distinguishes them, so the detector is an invariant
instead: a competitor cannot face two different opponents at the same time.
That passes a doubleheader, which is the same pair twice, and catches a
futures market, which is one name against many.
"""
import pytest

from backend.pipeline.full_pipeline import speculative_competitors


def _ev(home, away, when="2026-12-31T22:57:00Z"):
    return {"home_team": home, "away_team": away, "commence_time": when}


def test_one_name_against_many_is_speculative():
    events = [_ev("Moses Itauma", o) for o in ("A", "B", "C")]
    assert speculative_competitors(events) == {("2026-12-31", "Moses Itauma")}


def test_a_single_bout_is_not_speculative():
    assert speculative_competitors([_ev("Itauma", "Kabayel")]) == set()


def test_a_doubleheader_is_not_speculative():
    """The same pair twice in a day is real; mlb does it routinely."""
    events = [_ev("CIN", "STL", "2026-05-23T17:10:00Z"),
              _ev("CIN", "STL", "2026-05-23T23:15:00Z")]
    assert speculative_competitors(events) == set()


def test_the_side_of_the_matchup_does_not_matter():
    """A fighter listed home in one and away in another is the same person."""
    events = [_ev("Itauma", "A"), _ev("B", "Itauma")]
    assert speculative_competitors(events) == {("2026-12-31", "Itauma")}


def test_different_dates_are_judged_separately():
    events = [_ev("Itauma", "A", "2026-10-01T20:00:00Z"),
              _ev("Itauma", "B", "2026-11-01T20:00:00Z")]
    assert speculative_competitors(events) == set()


def test_both_sides_of_a_speculative_pairing_are_flagged():
    """If Joshua and Fury each face many, both names are unusable."""
    events = [_ev("Joshua", "Fury"), _ev("Joshua", "Dubois"),
              _ev("Wardley", "Fury")]
    got = speculative_competitors(events)
    assert ("2026-12-31", "Joshua") in got
    assert ("2026-12-31", "Fury") in got
    assert ("2026-12-31", "Wardley") not in got


def test_an_event_without_a_time_is_ignored_rather_than_crashing():
    assert speculative_competitors([{"home_team": "A", "away_team": "B"}]) == set()


def test_the_real_boxing_shape():
    """Eleven Itauma pairings plus an unrelated bout on the same date."""
    events = [_ev("Moses Itauma", f"Opponent {i}") for i in range(11)]
    events.append(_ev("Kameda", "Hernandez", "2026-09-23T10:00:00Z"))
    got = speculative_competitors(events)
    assert ("2026-12-31", "Moses Itauma") in got
    assert not any(name in ("Kameda", "Hernandez") for _, name in got)
