from datetime import datetime, date, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi.testclient import TestClient
from backend.api import pipeline_api
from backend.api.main import create_app
from backend.models import Base, ApiUsage
from backend.database import get_session

_CONFIG = {
    "database_path": ":memory:",
    "seasons": {"nba": {"start": "10-22", "end": "06-20"}},
    "odds_budget": {"monthly_limit": 20000, "daily_target": 600, "reserve": 2000},
}


@pytest.fixture()
def offline_pipeline(monkeypatch):
    """Stop /pipeline/run reaching ESPN.

    These two tests used to run the entire real pipeline -- live requests for
    every in-season sport -- which is why backend/tests/ stalled here for
    minutes at a time. They assert the endpoint's contract, not the
    collectors', so the collectors are mocked.
    """
    monkeypatch.setattr(pipeline_api, "load_config", lambda _p: dict(_CONFIG))
    monkeypatch.setattr(pipeline_api, "fetch_and_store_games",
                        AsyncMock(return_value=4))
    monkeypatch.setattr(pipeline_api, "fetch_and_store_odds",
                        AsyncMock(return_value=0))
    monkeypatch.setattr(pipeline_api, "fetch_and_store_props",
                        AsyncMock(return_value=0))
    monkeypatch.setattr(pipeline_api, "generate_and_store_picks",
                        MagicMock(return_value=0))
    monkeypatch.setattr(pipeline_api, "run_prop_pipeline",
                        AsyncMock(return_value={"props_analyzed": 0,
                                                "picks_generated": 0}))


def test_full_pipeline_with_credits(offline_pipeline):
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["games_stored"] == 4
    assert "credits_used" in data
    assert "credits_remaining_month" in data
    assert "props_analyzed" in data


def test_pipeline_with_sport_filter(offline_pipeline):
    app = create_app(":memory:")
    Base.metadata.create_all(app.state.engine)
    client = TestClient(app)
    resp = client.post("/pipeline/run?sport=nba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_sports"] == ["nba"]


def test_credits_endpoint_after_usage():
    app = create_app(":memory:")
    engine = app.state.engine
    Base.metadata.create_all(engine)
    session = get_session(engine)
    session.add(ApiUsage(
        endpoint="events", sport="nba", credits_used=1,
        requests_remaining=19999, created_at=datetime.now(tz=timezone.utc),
    ))
    session.commit()
    session.close()
    client = TestClient(app)
    resp = client.get("/credits/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["monthly_used"] == 1
    assert data["api_requests_remaining"] == 19999


def test_all_sports_includes_mlb():
    from backend.pipeline.full_pipeline import ALL_SPORTS
    assert "mlb" in ALL_SPORTS



@pytest.mark.asyncio
async def test_run_mlb_window_fetches_pitcher_scores(httpx_mock):
    """An MLB pipeline run should hit MLB Stats API once for schedule and once
    per probable pitcher, then return scores keyed by (home_abbr, away_abbr).

    The team objects below carry `name` and NOT `abbreviation`, which is what
    `hydrate=probablePitcher` actually returns. This fixture used to be the
    other way round -- an `abbreviation` the endpoint never sends -- so it
    passed while production resolved every team to None and priced every MLB
    pick on a neutral starter. Do not add `abbreviation` back here.
    """
    from datetime import date as _date
    from backend.pipeline.scheduler import fetch_pitcher_scores_for_date

    # Schedule response: one game with two probable pitchers.
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-04-29&hydrate=probablePitcher",
        json={"dates": [{"games": [{
            "gamePk": 700001,
            "gameDate": "2026-04-29T23:05:00Z",
            "teams": {
                "home": {"team": {"id": 111, "link": "/api/v1/teams/111",
                                  "name": "Boston Red Sox"},
                         "probablePitcher": {"id": 5001, "fullName": "A. Pitcher"}},
                "away": {"team": {"id": 147, "link": "/api/v1/teams/147",
                                  "name": "New York Yankees"},
                         "probablePitcher": {"id": 5002, "fullName": "B. Pitcher"}},
            },
        }]}]},
    )
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5001/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": [{"stat": {"era": "2.50", "strikeOuts": 8, "inningsPitched": "6.0"}}]}]},
    )
    httpx_mock.add_response(
        url="https://statsapi.mlb.com/api/v1/people/5002/stats?stats=gameLog&group=pitching&season=2026",
        json={"stats": [{"splits": [{"stat": {"era": "5.50", "strikeOuts": 4, "inningsPitched": "5.0"}}]}]},
    )

    scores = await fetch_pitcher_scores_for_date(_date(2026, 4, 29))
    # Keyed by (home_abbr, away_abbr) tuple — sport-agnostic, no internal IDs.
    assert ("BOS", "NYY") in scores
    assert scores[("BOS", "NYY")]["home"] > 0.6  # ace-ish ERA 2.50
    assert scores[("BOS", "NYY")]["away"] < 0.4  # bad outing ERA 5.50


@pytest.mark.asyncio
async def test_ufcstats_weekly_ingest_updates_fight_outcomes(httpx_mock, tmp_path):
    """Weekly UFCStats run: fetches event HTML, parses fights, upserts Game
    rows with binary home_score/away_score, creates Team + EloRating rows for
    new fighters. Grader (separate path) applies Elo updates on next run."""
    from pathlib import Path
    from datetime import date as _date
    from backend.pipeline.scheduler import ingest_recent_ufc_event
    from backend.database import get_engine, get_session
    from backend.models import Team, Game, EloRating

    fixture_html = (Path(__file__).parent / "fixtures" / "ufcstats_event.html").read_text(encoding="utf-8")
    httpx_mock.add_response(
        url="http://ufcstats.com/event-details/abc123",
        text=fixture_html,
    )

    db_path = str(tmp_path / "ufc_ingest.db")
    summary = await ingest_recent_ufc_event(
        event_url="http://ufcstats.com/event-details/abc123",
        event_date=_date(2026, 4, 26),
        db_path=db_path,
    )

    # Fixture has 2 fights, 4 unique fighters.
    assert summary["fights_ingested"] == 2
    assert summary["fighters_created_or_matched"] == 4

    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        fighters = {t.name for t in session.query(Team).filter(Team.sport == "mma").all()}
        assert fighters == {"Alex Pereira", "Jamahal Hill", "Charles Oliveira", "Arman Tsarukyan"}

        # Each new fighter gets an EloRating row at 1500 (grader updates later).
        elo_rows = session.query(EloRating).filter(EloRating.sport == "mma").all()
        assert len(elo_rows) == 4
        assert all(er.rating == 1500.0 for er in elo_rows)

        games = session.query(Game).filter(Game.sport == "mma", Game.date == _date(2026, 4, 26)).all()
        assert len(games) == 2
        # Both finalized with binary 1/0 scores.
        for g in games:
            assert g.status == "final"
            assert {g.home_score, g.away_score} == {0, 1}
    finally:
        session.close()


@pytest.mark.asyncio
async def test_ufcstats_ingest_is_idempotent_on_rerun(httpx_mock, tmp_path):
    """Running the same event twice should not create duplicate Game rows —
    second run updates existing rows instead of inserting."""
    from pathlib import Path
    from datetime import date as _date
    from backend.pipeline.scheduler import ingest_recent_ufc_event
    from backend.database import get_engine, get_session
    from backend.models import Game

    fixture_html = (Path(__file__).parent / "fixtures" / "ufcstats_event.html").read_text(encoding="utf-8")
    # Two responses — one per call.
    httpx_mock.add_response(url="http://ufcstats.com/event-details/abc123", text=fixture_html)
    httpx_mock.add_response(url="http://ufcstats.com/event-details/abc123", text=fixture_html)

    db_path = str(tmp_path / "ufc_idempotent.db")
    await ingest_recent_ufc_event(
        event_url="http://ufcstats.com/event-details/abc123",
        event_date=_date(2026, 4, 26),
        db_path=db_path,
    )
    await ingest_recent_ufc_event(
        event_url="http://ufcstats.com/event-details/abc123",
        event_date=_date(2026, 4, 26),
        db_path=db_path,
    )

    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        games = session.query(Game).filter(Game.sport == "mma").all()
        assert len(games) == 2  # not 4 — second run upserted, didn't duplicate
    finally:
        session.close()
