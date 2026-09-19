"""Parsing ESPN's v3 athlete stats payload.

The collector fetched `{site_v2_base}/athletes/{id}/statistics`, which 404s
for every athlete, and parsed a shape (`statistics[].splits[].categories[]`)
that endpoint no longer returns. The live endpoint is
`site.web.api.espn.com/apis/common/v3/.../athletes/{id}/stats`, and its shape
is parallel arrays: `categories[].labels` alongside
`categories[].statistics[].stats`.

Payloads below are trimmed copies of real responses.
"""

from backend.collectors.player_stats.espn_stats_source import EspnStatsSource

BASKETBALL = {
    "categories": [
        {
            "name": "averages",
            "labels": ["GP", "GS", "MIN", "FG", "FG%", "3PT", "3P%", "FT",
                       "FT%", "OR", "DR", "REB", "AST", "BLK", "STL", "PF",
                       "TO", "PTS"],
            "statistics": [
                {   # an old season -- must NOT be the one used
                    "season": 2004, "teamSlug": "old",
                    "stats": ["79", "79", "39.5", "7.9-18.9", "41.7",
                              "0.8-2.7", "29.0", "4.4-5.8", "75.4", "1.3",
                              "4.2", "5.5", "5.9", "0.7", "1.6", "1.9",
                              "3.5", "20.9"],
                },
                {   # the most recent season -- this one
                    "season": 2026, "teamSlug": "current",
                    "stats": ["70", "70", "35.0", "9.0-18.0", "50.0",
                              "2.5-6.0", "41.7", "5.0-6.0", "83.3", "1.0",
                              "6.0", "7.0", "8.0", "0.6", "1.2", "1.8",
                              "3.1", "25.5"],
                },
            ],
        },
        {"name": "totals", "labels": ["GP", "PTS"],
         "statistics": [{"season": 2026, "stats": ["70", "1785"]}]},
    ]
}

FOOTBALL = {
    "categories": [
        {"name": "passing",
         "labels": ["CMP", "ATT", "CMP%", "YDS", "AVG", "TD", "INT"],
         "statistics": [{"stats": ["136", "211", "64.5", "1,912", "9.1",
                                   "21", "4"]}]},
        {"name": "rushing", "labels": ["CAR", "YDS", "AVG", "TD", "LNG"],
         "statistics": [{"stats": ["79", "442", "5.6", "6", "74"]}]},
        {"name": "receiving", "labels": ["REC", "YDS", "AVG", "TD", "LNG"],
         "statistics": [{"stats": ["3", "-2", "0.0", "1", "0"]}]},
        {"name": "scoring",
         "labels": ["PASS", "RUSH", "REC", "RET", "TD", "2PT", "PAT", "FG",
                    "PTS"],
         "statistics": [{"stats": ["3", "0", "0", "0", "27", "0", "0", "0",
                                   "162"]}]},
    ]
}


def _src():
    return EspnStatsSource()


def test_basketball_reads_the_averages_category():
    out = _src()._parse_basketball_stats(BASKETBALL)
    assert out["points"] == 25.5
    assert out["rebounds"] == 7.0
    assert out["assists"] == 8.0


def test_basketball_uses_the_most_recent_season_not_the_first():
    """Rows run oldest to newest; taking [0] silently reports a rookie year."""
    out = _src()._parse_basketball_stats(BASKETBALL)
    assert out["points"] == 25.5, "took the 2004 row (20.9) instead of 2026"


def test_a_made_attempted_pair_yields_the_made_half():
    """ESPN reports 3PT as "2.5-6.0" -- made-attempted, not a single number."""
    out = _src()._parse_basketball_stats(BASKETBALL)
    assert out["threes"] == 2.5


def test_basketball_minutes_steals_blocks_turnovers():
    out = _src()._parse_basketball_stats(BASKETBALL)
    assert out["minutes"] == 35.0
    assert out["steals"] == 1.2
    assert out["blocks"] == 0.6
    assert out["turnovers"] == 3.1


def test_football_disambiguates_yds_by_category():
    """'YDS' appears in passing, rushing AND receiving.

    A flat label map picks whichever category comes last and reports it as
    all three. The values here are deliberately distinct so that failure is
    visible.
    """
    out = _src()._parse_football_stats(FOOTBALL)
    assert out["pass_yards"] == 1912.0
    assert out["rush_yards"] == 442.0
    assert out["rec_yards"] == -2.0


def test_thousands_separators_are_parsed():
    out = _src()._parse_football_stats(FOOTBALL)
    assert out["pass_yards"] == 1912.0, "the comma in '1,912' was not handled"


def test_football_touchdowns_come_from_the_scoring_category():
    """passing.TD is 21 and rushing.TD is 6; the total is scoring.TD."""
    out = _src()._parse_football_stats(FOOTBALL)
    assert out["touchdowns"] == 27.0


def test_a_missing_category_leaves_its_fields_none():
    partial = {"categories": [FOOTBALL["categories"][0]]}  # passing only
    out = _src()._parse_football_stats(partial)
    assert out["pass_yards"] == 1912.0
    assert out["rush_yards"] is None
    assert out["rec_yards"] is None


def test_an_empty_payload_returns_all_none_without_raising():
    for payload in ({}, {"categories": []}, {"categories": [{"name": "x"}]}):
        out = _src()._parse_basketball_stats(payload)
        assert out["points"] is None
        assert set(out) >= {"points", "rebounds", "assists", "minutes"}


def test_a_non_numeric_value_becomes_none_rather_than_raising():
    broken = {
        "categories": [{
            "name": "averages", "labels": ["PTS", "REB"],
            "statistics": [{"stats": ["--", "7.0"]}],
        }]
    }
    out = _src()._parse_basketball_stats(broken)
    assert out["points"] is None
    assert out["rebounds"] == 7.0


def test_labels_and_stats_of_differing_length_do_not_misalign():
    """A short stats row must not shift every later label onto the wrong value."""
    ragged = {
        "categories": [{
            "name": "averages", "labels": ["MIN", "PTS", "REB", "AST"],
            "statistics": [{"stats": ["30.0", "20.0"]}],
        }]
    }
    out = _src()._parse_basketball_stats(ragged)
    assert out["minutes"] == 30.0
    assert out["points"] == 20.0
    assert out["rebounds"] is None
    assert out["assists"] is None
