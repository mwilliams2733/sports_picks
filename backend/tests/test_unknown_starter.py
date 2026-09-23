"""Regression tests for plan 022: an unknown starter is not an average one.

Before this fix, `pitcher_skill_score(None, None)` returned 0.5 -- the same
value it returns for a genuine league-average pitcher -- and
`fetch_pitcher_scores_for_date` substituted that 0.5 for every side with no
announced starter. Every downstream consumer's `is None` guard
(`ensemble.pitcher_logit_shift`, `sport_specific._pitcher_score`,
`strategy._build_factors`, `scheduler._persist_pitcher_scores`) was
therefore dead: a game with ONE announced starter priced the known pitcher
against a phantom average one -- exactly what `pitcher_logit_shift`
documents itself as preventing. The producer is async and network-bound, so
its tests fake the MLB Stats API via httpx_mock, following the shape of
test_mlb_integration.py and test_mlb_stats.py.
"""
from datetime import date

import pytest

from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, TeamStat
from backend.pipeline.scheduler import (
    fetch_pitcher_scores_for_date, _persist_pitcher_scores, PITCHER_STAT_TYPE,
)
from backend.analysis.variants import ensemble
from backend.data_types import GameData, TeamStats, OddsSnapshot
from backend.scripts import drop_unknown_pitcher_rows

MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"


def _schedule_payload(home_id=None, away_id=None):
    home = {"team": {"id": 111, "name": "Boston Red Sox"}}
    if home_id is not None:
        home["probablePitcher"] = {"id": home_id}
    away = {"team": {"id": 147, "name": "New York Yankees"}}
    if away_id is not None:
        away["probablePitcher"] = {"id": away_id}
    return {"dates": [{"games": [{
        "gamePk": 700001,
        "gameDate": "2026-09-23T23:05:00Z",
        "teams": {"home": home, "away": away},
    }]}]}


def _pitcher_stats_url(pitcher_id):
    return (f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
            "?stats=gameLog&group=pitching&season=2026")


def _pitcher_payload(era="2.50"):
    return {"stats": [{"splits": [
        {"stat": {"era": era, "strikeOuts": 8, "inningsPitched": "6.0"}},
    ]}]}


def _no_starts_payload():
    return {"stats": [{"splits": []}]}


def _mock_schedule(httpx_mock, **kw):
    httpx_mock.add_response(
        url=f'{MLB_SCHEDULE}?sportId=1&date=2026-09-23&hydrate=probablePitcher',
        json=_schedule_payload(**kw))


@pytest.mark.asyncio
async def test_a_side_with_no_data_is_none_not_a_half(httpx_mock):
    _mock_schedule(httpx_mock, home_id=5001, away_id=5002)
    httpx_mock.add_response(url=_pitcher_stats_url(5001), json=_pitcher_payload())
    httpx_mock.add_response(url=_pitcher_stats_url(5002), json=_no_starts_payload())

    out = await fetch_pitcher_scores_for_date(date(2026, 9, 23))
    scores = out[("BOS", "NYY")]

    assert scores["away"] is None
    assert scores["away"] != 0.5


@pytest.mark.asyncio
async def test_a_side_with_data_still_scores(httpx_mock):
    _mock_schedule(httpx_mock, home_id=5001, away_id=5002)
    httpx_mock.add_response(url=_pitcher_stats_url(5001), json=_pitcher_payload())
    httpx_mock.add_response(url=_pitcher_stats_url(5002), json=_no_starts_payload())

    out = await fetch_pitcher_scores_for_date(date(2026, 9, 23))
    scores = out[("BOS", "NYY")]

    assert scores["home"] is not None
    assert isinstance(scores["home"], float)
    assert 0.0 < scores["home"] < 1.0


@pytest.mark.asyncio
async def test_both_sides_unknown_are_both_none(httpx_mock):
    _mock_schedule(httpx_mock, home_id=5001, away_id=5002)
    httpx_mock.add_response(url=_pitcher_stats_url(5001), json=_no_starts_payload())
    httpx_mock.add_response(url=_pitcher_stats_url(5002), json=_no_starts_payload())

    out = await fetch_pitcher_scores_for_date(date(2026, 9, 23))
    scores = out[("BOS", "NYY")]

    assert scores["home"] is None
    assert scores["away"] is None


@pytest.mark.asyncio
async def test_a_missing_probable_pitcher_id_is_none(httpx_mock):
    # No probablePitcher on the home side at all -- home_probable_pitcher_id
    # will be None, so fetch_pitcher_recent must not be called for "home".
    # No response is registered for any /people/.../stats URL: if the
    # producer called it anyway, httpx_mock would raise for the unmatched
    # request and this test would fail.
    _mock_schedule(httpx_mock, home_id=None, away_id=5002)
    httpx_mock.add_response(url=_pitcher_stats_url(5002), json=_pitcher_payload())

    out = await fetch_pitcher_scores_for_date(date(2026, 9, 23))
    scores = out[("BOS", "NYY")]

    assert scores["home"] is None


def _stats(**kw):
    defaults = dict(point_diff=0.0, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(5, 5), offensive_rating=100.0, defensive_rating=100.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1500.0, rest_days=2,
        points_for=105.0, points_against=105.0)
    defaults.update(kw)
    return TeamStats(**defaults)


def test_the_shift_declines_when_one_starter_is_unknown():
    """The test that would have caught the bug.

    Before the fix, the producer substituted 0.5 for the away side, and
    `pitcher_logit_shift` would have computed a real shift from
    `home - 0.5` instead of declining. This asserts the shift is exactly
    zero when one side is genuinely unknown (`None`), which only holds if
    the producer stops handing the consumer a phantom 0.5.
    """
    game = GameData(game_id=1, sport="mlb", date=date(2026, 9, 23),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(pitcher_skill_score=0.80),
        away_stats=_stats(pitcher_skill_score=None),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                           spread_home=-1.5, spread_away=1.5, over_under=8.5)])

    assert ensemble.pitcher_logit_shift(game) == 0.0


def _session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    return get_session(engine)


def _seed_game(session, game_id=101, home_id=1, away_id=2):
    session.add_all([
        Team(id=home_id, name="Yankees", abbreviation="NYY", sport="mlb"),
        Team(id=away_id, name="Red Sox", abbreviation="BOS", sport="mlb"),
    ])
    session.flush()
    session.add(Game(id=game_id, sport="mlb", season="2026", date=date(2026, 9, 23),
                     home_team_id=home_id, away_team_id=away_id, status="scheduled"))
    session.commit()
    return home_id, away_id


def test_persistence_skips_an_unknown_side():
    session = _session()
    home_id, away_id = _seed_game(session)

    written = _persist_pitcher_scores(session, {101: {"home": 0.7, "away": None}})

    assert written == 1
    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == PITCHER_STAT_TYPE).all()
    assert len(rows) == 1
    assert rows[0].team_id == home_id


# --- Step 5: the cleanup script ---------------------------------------------

def _seed_cleanup_fixture(session):
    """Two 0.5 rows (one unknown-starter game, one both-unknown game) and one
    real measurement, sharing the schema `_persist_pitcher_scores` writes."""
    session.add_all([
        Team(id=1, name="Yankees", abbreviation="NYY", sport="mlb"),
        Team(id=2, name="Red Sox", abbreviation="BOS", sport="mlb"),
        Team(id=3, name="Mets", abbreviation="NYM", sport="mlb"),
        Team(id=4, name="Phillies", abbreviation="PHI", sport="mlb"),
    ])
    session.flush()
    session.add_all([
        Game(id=201, sport="mlb", season="2026", date=date(2026, 9, 23),
             home_team_id=1, away_team_id=2, status="final"),
        Game(id=202, sport="mlb", season="2026", date=date(2026, 9, 23),
             home_team_id=3, away_team_id=4, status="final"),
    ])
    session.flush()
    session.add_all([
        # game 201: one side unknown (0.5), one real
        TeamStat(team_id=1, game_id=201, stat_type=PITCHER_STAT_TYPE, value=0.5),
        TeamStat(team_id=2, game_id=201, stat_type=PITCHER_STAT_TYPE, value=0.72),
        # game 202: both sides unknown
        TeamStat(team_id=3, game_id=202, stat_type=PITCHER_STAT_TYPE, value=0.5),
        TeamStat(team_id=4, game_id=202, stat_type=PITCHER_STAT_TYPE, value=0.5),
    ])
    session.commit()


def test_cleanup_dry_run_deletes_nothing():
    session = _session()
    _seed_cleanup_fixture(session)

    summary = drop_unknown_pitcher_rows.run_on_session(session, apply=False)

    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == PITCHER_STAT_TYPE).all()
    assert len(rows) == 4
    assert summary["row_count"] == 3
    assert len(summary["games"]) == 2


def test_cleanup_apply_removes_only_the_exact_halves():
    session = _session()
    _seed_cleanup_fixture(session)

    drop_unknown_pitcher_rows.run_on_session(session, apply=True)

    rows = session.query(TeamStat).filter(
        TeamStat.stat_type == PITCHER_STAT_TYPE).all()
    assert len(rows) == 1
    assert rows[0].team_id == 2
    assert rows[0].value == 0.72
