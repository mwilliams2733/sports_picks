"""Football season_avg rows are per-game averages for the current season.

The v3 football categories are season TOTALS, and they were stored as
``season_avg`` and read as per-game by the prop analyzer: Josh Allen
"averaged" 786 pass yards after 3 games, so every football prop projection
was inflated, and 451 graded props predicted 0.818 and hit 0.494.

ALLEN is Josh Allen's v3 payload as ESPN returned it on 2026-10-04 (the
categories the parser reads, plus kicking, whose newest row is 2021).
COLLEGE is the shape of a college-football payload: no GP label anywhere.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from backend.collectors.player_stats import espn_stats_source as mod
from backend.collectors.player_stats.espn_stats_source import (EspnStatsSource,
                                                               football_season_averages)

ALLEN = {"categories": [
    {"name": "passing",
     "labels": ["GP", "CMP", "ATT", "CMP%", "YDS", "AVG", "TD", "INT", "LNG", "SACK", "RTG", "QBR"],
     "statistics": [{"season": {"year": 2025, "displayName": "2025"},
                     "stats": ["17", "325", "460", "70.7", "3,668", "8.0", "28", "6", "62", "40", "102.2", "75.0"]},
                    {"season": {"year": 2026, "displayName": "2026"},
                     "stats": ["3", "56", "86", "65.1", "786", "9.1", "5", "2", "43", "8", "104.1", "77.2"]}]},
    {"name": "rushing", "labels": ["GP", "CAR", "YDS", "AVG", "TD", "LNG", "FD", "FUM", "LST"],
     "statistics": [{"season": {"year": 2026}, "stats": ["3", "28", "114", "4.1", "6", "21", "13", "0", "0"]}]},
    {"name": "receiving", "labels": ["GP", "REC", "TGTS", "YDS", "AVG", "TD", "LNG", "FD", "FUM", "LST"],
     "statistics": [{"season": {"year": 2026}, "stats": ["3", "1", "1", "1", "1.0", "0", "1", "0", "0", "0"]}]},
    {"name": "scoring", "labels": ["GP", "PASS", "RUSH", "REC", "RET", "TD", "2PT", "PAT", "FG", "PTS"],
     "statistics": [{"season": {"year": 2026}, "stats": ["3", "5", "6", "0", "0", "6", "0", "0", "0", "36"]}]},
    {"name": "kicking", "labels": ["GP", "FG", "FG%", "LNG", "XPM", "XPA", "PTS"],
     "statistics": [{"season": {"year": 2021}, "stats": ["17", "0-0", "0.0", "0", "0", "0", "0"]}]},
]}

COLLEGE = {"categories": [
    {"name": "passing", "labels": ["CMP", "ATT", "CMP%", "YDS", "AVG", "TD", "INT", "LNG", "SACK", "RTG"],
     "statistics": [{"season": {"year": 2026}, "stats": ["70", "105", "66.7", "912", "8.7", "8", "1", "61", "4", "160.2"]}]},
]}


def test_totals_become_per_game_averages():
    out = football_season_averages(ALLEN, 2026)

    assert out["pass_yards"] == pytest.approx(786 / 3)
    assert out["rush_yards"] == pytest.approx(114 / 3)
    assert out["rec_yards"] == pytest.approx(1 / 3)
    assert out["touchdowns"] == pytest.approx(6 / 3)


def test_only_the_current_seasons_row_counts():
    """Last season's 3,668 yards over 17 games must not stand in for 2026."""
    assert football_season_averages(ALLEN, 2026)["pass_yards"] == pytest.approx(262.0)
    assert football_season_averages(ALLEN, 2025)["pass_yards"] == pytest.approx(3668 / 17)


def test_a_category_with_no_row_this_season_is_none():
    """His newest rushing row is 2026; in 2027 there is none yet."""
    out = football_season_averages(ALLEN, 2027)

    assert out["rush_yards"] is None and out["pass_yards"] is None


def test_college_football_has_no_games_played_so_no_average():
    """No GP means no per-game figure; the analyzer projects from recent games."""
    out = football_season_averages(COLLEGE, 2026)

    assert out["pass_yards"] is None


def test_fetch_season_averages_stores_per_game_for_the_current_season(monkeypatch):
    monkeypatch.setattr(mod.team_identity, "espn_team_id", lambda sport, abbr: "2")
    monkeypatch.setattr("backend.time_utils.et_today",
                        lambda: datetime(2026, 10, 4, tzinfo=timezone.utc).date())
    src = EspnStatsSource()

    class _Resp:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class _Client:
        async def get(self, url, **kwargs):
            if url.endswith("/roster"):
                return _Resp({"athletes": [{"items": [{"id": "3918298", "fullName": "Josh Allen"}]}]})
            return _Resp(ALLEN)

    src._client = _Client()
    rows = asyncio.run(src.fetch_season_averages("nfl", "BUF"))

    assert rows[0]["player_name"] == "Josh Allen"
    assert rows[0]["pass_yards"] == pytest.approx(262.0)


def test_a_refetch_clears_a_season_average_the_source_no_longer_reports():
    """The inflated totals already stored must not survive a refetch that
    returns None for them (college football, which has no GP)."""
    from backend.collectors.player_stats.collector import PlayerStatsCollector
    from backend.database import get_session
    from backend.models import Base, PlayerStat, Team
    s = get_session(create_engine("sqlite:///:memory:"))
    Base.metadata.create_all(s.get_bind())
    s.add(Team(id=1, name="T", abbreviation="T", sport="ncaaf"))
    s.commit()
    collector = PlayerStatsCollector.__new__(PlayerStatsCollector)

    collector.store_stats(s, [{"player_name": "QB", "pass_yards": 912.0}], "season_avg", 1, "ncaaf", "espn")
    collector.store_stats(s, [{"player_name": "QB", "pass_yards": None}], "season_avg", 1, "ncaaf", "espn")

    assert s.query(PlayerStat).one().pass_yards is None
