"""Tests for the Wikidata-based boxing seed script."""
import re

import pytest


@pytest.mark.asyncio
async def test_seed_creates_team_and_elo_rows_for_each_fighter(httpx_mock, tmp_path):
    """Mocked SPARQL response with 2 boxers → 2 Team rows + 2 EloRating rows
    seeded at 1500 each."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, EloRating
    from backend.scripts.seed_boxing_fighters import seed

    httpx_mock.add_response(
        url=re.compile(r"https://query\.wikidata\.org/sparql.*"),
        json={"results": {"bindings": [
            {"fighter": {"value": "http://www.wikidata.org/entity/Q3027894"},
             "fighterLabel": {"value": "Mike Tyson"},
             "wins": {"value": "50"}, "losses": {"value": "6"}, "draws": {"value": "0"}},
            {"fighter": {"value": "http://www.wikidata.org/entity/Q83149"},
             "fighterLabel": {"value": "Muhammad Ali"},
             "wins": {"value": "56"}, "losses": {"value": "5"}, "draws": {"value": "0"}},
        ]}},
    )

    db_path = str(tmp_path / "boxing_seed.db")
    summary = await seed(db_path=db_path)
    assert summary["fighters_seeded"] == 2

    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        names = {t.name for t in session.query(Team).filter(Team.sport == "boxing").all()}
        assert names == {"Mike Tyson", "Muhammad Ali"}
        elos = session.query(EloRating).filter(EloRating.sport == "boxing").all()
        assert len(elos) == 2
        assert all(er.rating == 1500.0 for er in elos)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_seed_is_idempotent_skips_existing_fighters(httpx_mock, tmp_path):
    """Re-running the seed must not duplicate Team rows or reset Elo ratings
    (a re-seed mid-season would otherwise erase real fight Elo updates)."""
    from backend.database import get_engine, get_session
    from backend.models import Base, Team, EloRating
    from backend.scripts.seed_boxing_fighters import seed

    payload = {"results": {"bindings": [
        {"fighter": {"value": "http://www.wikidata.org/entity/Q3027894"},
         "fighterLabel": {"value": "Mike Tyson"},
         "wins": {"value": "50"}, "losses": {"value": "6"}, "draws": {"value": "0"}},
    ]}}
    httpx_mock.add_response(
        url=re.compile(r"https://query\.wikidata\.org/sparql.*"), json=payload,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://query\.wikidata\.org/sparql.*"), json=payload,
    )

    db_path = str(tmp_path / "boxing_idempotent.db")
    await seed(db_path=db_path)

    # Simulate fight outcome between seed runs: Tyson's Elo gets bumped.
    engine = get_engine(db_path)
    session = get_session(engine)
    tyson = session.query(Team).filter(Team.sport == "boxing", Team.name == "Mike Tyson").first()
    er = session.query(EloRating).filter(EloRating.team_id == tyson.id).first()
    er.rating = 1620.0  # post-fight rating
    session.commit()
    session.close()

    summary = await seed(db_path=db_path)
    assert summary["fighters_seeded"] == 0  # nothing new

    session = get_session(engine)
    try:
        teams = session.query(Team).filter(Team.sport == "boxing").all()
        assert len(teams) == 1  # not duplicated
        elo_rows = session.query(EloRating).filter(EloRating.sport == "boxing").all()
        assert len(elo_rows) == 1
        # Critical: the post-fight rating MUST survive a re-seed.
        assert elo_rows[0].rating == 1620.0
    finally:
        session.close()


@pytest.mark.asyncio
async def test_seed_skips_fighters_with_qid_fallback_label(httpx_mock, tmp_path):
    """When Wikidata has no English label, fighterLabel falls back to the QID
    (e.g. 'Q98765'). Seeding those is noise — skip them."""
    from backend.database import get_engine, get_session
    from backend.models import Team
    from backend.scripts.seed_boxing_fighters import seed

    httpx_mock.add_response(
        url=re.compile(r"https://query\.wikidata\.org/sparql.*"),
        json={"results": {"bindings": [
            {"fighter": {"value": "http://www.wikidata.org/entity/Q98765"},
             "fighterLabel": {"value": "Q98765"},  # QID fallback — no real label
             "wins": {"value": "10"}, "losses": {"value": "5"}, "draws": {"value": "0"}},
            {"fighter": {"value": "http://www.wikidata.org/entity/Q3027894"},
             "fighterLabel": {"value": "Mike Tyson"},
             "wins": {"value": "50"}, "losses": {"value": "6"}, "draws": {"value": "0"}},
        ]}},
    )

    db_path = str(tmp_path / "boxing_qid_skip.db")
    summary = await seed(db_path=db_path)
    assert summary["fighters_seeded"] == 1  # only Tyson, not the Q-fallback row

    engine = get_engine(db_path)
    session = get_session(engine)
    try:
        names = {t.name for t in session.query(Team).filter(Team.sport == "boxing").all()}
        assert names == {"Mike Tyson"}
    finally:
        session.close()
