import pytest
from datetime import date, datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.models import Base, Team, Game, PlayerStat, PlayerProp
from backend.analysis.prop_analyzer import PropAnalyzer


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _make_team(session: Session) -> Team:
    team = Team(name="Celtics", abbreviation="BOS", sport="nba")
    session.add(team)
    session.flush()
    return team


def _make_game(session: Session, team: Team) -> Game:
    game = Game(
        sport="nba",
        season="2025-26",
        date=date(2026, 3, 14),
        home_team_id=team.id,
        away_team_id=team.id,
        status="scheduled",
    )
    session.add(game)
    session.flush()
    return game


def _make_season_avg(session: Session, team: Team, pts=27.0, reb=8.0, ast=5.0) -> PlayerStat:
    stat = PlayerStat(
        player_name="Jayson Tatum",
        team_id=team.id,
        sport="nba",
        stat_type="season_avg",
        points=pts,
        rebounds=reb,
        assists=ast,
        source="test",
        fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.flush()
    return stat


def _make_game_log(session: Session, team: Team, points: float, game_date: date) -> PlayerStat:
    stat = PlayerStat(
        player_name="Jayson Tatum",
        team_id=team.id,
        sport="nba",
        stat_type="game_log",
        game_date=game_date,
        points=points,
        rebounds=8.0,
        assists=5.0,
        source="test",
        fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(stat)
    session.flush()
    return stat


def _make_prop(session: Session, game: Game, line=25.5, outcome="Over", market="player_points") -> PlayerProp:
    prop = PlayerProp(
        game_id=game.id,
        bookmaker="draftkings",
        market=market,
        player_name="Jayson Tatum",
        outcome=outcome,
        line=line,
        odds=-110,
        fetched_at=datetime.now(tz=timezone.utc),
    )
    session.add(prop)
    session.flush()
    return prop


def test_analyze_finds_edge(session):
    """Hot streak player with recent avg ~31 vs line 25.5 should return positive Over edge."""
    team = _make_team(session)
    game = _make_game(session, team)
    season_avg = _make_season_avg(session, team, pts=27.0)
    hot_points = [32.0, 30.0, 35.0, 28.0, 31.0]
    recent_games = [
        _make_game_log(session, team, pts, date(2026, 3, i + 1))
        for i, pts in enumerate(hot_points)
    ]
    prop = _make_prop(session, game, line=25.5, outcome="Over")

    analyzer = PropAnalyzer()
    result = analyzer.analyze(prop, season_avg, recent_games)

    assert result is not None
    assert result.outcome == "Over"
    assert result.projection > 25.5
    assert result.edge_pct > 0
    assert result.confidence >= 1


def test_analyze_season_avg_only_is_refused(session):
    """Season averages alone are no longer analysed -- INVERTED by plan 012.

    This test used to assert the season-only branch produced a pick. That
    branch computed edge as abs(diff / line) * 100, which is not a probability
    and inflates small lines, while the distribution branch returns
    (prob - 0.5) * 200. Both fed the same confidence thresholds and the digest
    ranked them together, so one scale had to go. Without three game-by-game
    values there is no variance estimate and therefore no probability, so the
    prop is refused.
    """
    team = _make_team(session)
    game = _make_game(session, team)
    season_avg = _make_season_avg(session, team, pts=27.0)
    prop = _make_prop(session, game, line=25.5, outcome="Over")

    analyzer = PropAnalyzer()

    assert analyzer.analyze(prop, season_avg, []) is None


def test_analyze_under_pick(session):
    """Line 40.5, projection ~30 → positive Under edge."""
    team = _make_team(session)
    game = _make_game(session, team)
    # Season avg points=27, reb=8, ast=5 → PRA = 40 when using combo market
    # But for simple points market with line 40.5 and pts=27, should be Under
    season_avg = _make_season_avg(session, team, pts=27.0)
    prop = _make_prop(session, game, line=40.5, outcome="Under")

    # Three game logs, because a probability needs a variance estimate. The
    # behaviour under test -- that an Under clears on a projection well below
    # the line -- is unchanged; only the input requirement is.
    logs = [_make_game_log(session, team, pts, date(2026, 3, d))
            for pts, d in ((26.0, 1), (28.0, 2), (27.0, 3))]

    analyzer = PropAnalyzer()
    result = analyzer.analyze(prop, season_avg, logs)

    assert result is not None
    assert result.outcome == "Under"
    assert result.projection < 40.5
    assert result.edge_pct > 0


def test_analyze_no_stats_returns_none(session):
    """None season avg and empty recent games → None."""
    team = _make_team(session)
    game = _make_game(session, team)
    prop = _make_prop(session, game, line=25.5, outcome="Over")

    analyzer = PropAnalyzer()
    result = analyzer.analyze(prop, None, [])

    assert result is None


def test_analyze_combination_market(session):
    """PRA (points+rebounds+assists) combination market prop."""
    team = _make_team(session)
    game = _make_game(session, team)
    # Season avg: pts=27, reb=8, ast=5 → PRA season_avg = 40
    season_avg = _make_season_avg(session, team, pts=27.0, reb=8.0, ast=5.0)
    # Hot streak recent games: pts=32, reb=9, ast=6 → PRA recent_avg = 47
    hot_logs = [
        _make_game_log(session, team, 32.0, date(2026, 3, i + 1))
        for i in range(5)
    ]
    # Manually set rebounds and assists on the hot logs
    for log in hot_logs:
        log.rebounds = 9.0
        log.assists = 6.0
    session.flush()

    # PRA line below recent projection → Over edge
    prop = _make_prop(session, game, line=38.5, outcome="Over", market="player_points_rebounds_assists")

    analyzer = PropAnalyzer()
    result = analyzer.analyze(prop, season_avg, hot_logs)

    assert result is not None
    assert result.market == "player_points_rebounds_assists"
    assert result.outcome == "Over"
    assert result.projection > 38.5
    # season_avg for PRA: 27+8+5=40, recent_avg for PRA: 32+9+6=47
    expected_projection = 0.4 * 40.0 + 0.6 * 47.0
    assert result.projection == pytest.approx(expected_projection, rel=1e-3)


def test_receptions_uses_receptions_not_rec_yards():
    """player_receptions market should use receptions field, not rec_yards."""
    from backend.analysis.prop_analyzer import MARKET_TO_STAT
    assert MARKET_TO_STAT["player_receptions"] == ["receptions"]
