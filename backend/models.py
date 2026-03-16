from datetime import date, datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey, Boolean, Text
)
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    abbreviation = Column(String, nullable=False)
    sport = Column(String, nullable=False)
    conference = Column(String)
    division = Column(String)

class Game(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True)
    sport = Column(String, nullable=False)
    season = Column(String, nullable=False)
    week = Column(Integer, nullable=True)
    date = Column(Date, nullable=False)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="scheduled")
    home_team = relationship("Team", foreign_keys=[home_team_id])
    away_team = relationship("Team", foreign_keys=[away_team_id])

class TeamStat(Base):
    __tablename__ = "team_stats"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    stat_type = Column(String, nullable=False)
    value = Column(Float, nullable=False)

class EloRating(Base):
    __tablename__ = "elo_ratings"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    sport = Column(String, nullable=False)
    rating = Column(Float, nullable=False, default=1500.0)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))

class Odds(Base):
    __tablename__ = "odds"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    bookmaker = Column(String, nullable=False)
    moneyline_home = Column(Integer, nullable=True)
    moneyline_away = Column(Integer, nullable=True)
    spread_home = Column(Float, nullable=True)
    spread_away = Column(Float, nullable=True)
    over_under = Column(Float, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))

class StrategyModel(Base):
    __tablename__ = "strategies"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    config_json = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=False)
    sport = Column(String, nullable=True)
    strategy_type = Column(String, nullable=False, default="game")  # "game" or "prop"

class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

class BacktestPick(Base):
    __tablename__ = "backtest_picks"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    pick_type = Column(String, nullable=False)
    pick_value = Column(String, nullable=False)
    confidence = Column(Integer, nullable=False)
    edge_pct = Column(Float, nullable=False)
    result = Column(String, nullable=True)
    odds_at_pick = Column(Integer, nullable=True)

class PickModel(Base):
    __tablename__ = "picks"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    pick_type = Column(String, nullable=False)
    pick_value = Column(String, nullable=False)
    confidence = Column(Integer, nullable=False)
    edge_pct = Column(Float, nullable=False)
    odds_at_pick = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    game = relationship("Game")

class PickResult(Base):
    __tablename__ = "pick_results"
    id = Column(Integer, primary_key=True)
    pick_id = Column(Integer, ForeignKey("picks.id"), nullable=False)
    result = Column(String, nullable=False)
    payout = Column(Float, nullable=False, default=0.0)
    odds_at_close = Column(Integer, nullable=True)
    pick = relationship("PickModel")

class PlayerProp(Base):
    __tablename__ = "player_props"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    bookmaker = Column(String, nullable=False)
    market = Column(String, nullable=False)
    player_name = Column(String, nullable=False)
    outcome = Column(String, nullable=False)  # "Over" or "Under"
    line = Column(Float, nullable=True)
    odds = Column(Integer, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    game = relationship("Game")


class PlayerStat(Base):
    __tablename__ = "player_stats"
    id = Column(Integer, primary_key=True)
    player_name = Column(String, nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    sport = Column(String, nullable=False)
    stat_type = Column(String, nullable=False)  # "season_avg" or "game_log"
    game_date = Column(Date, nullable=True)      # null for season_avg
    minutes = Column(Float, nullable=True)
    points = Column(Float, nullable=True)
    rebounds = Column(Float, nullable=True)
    assists = Column(Float, nullable=True)
    threes = Column(Float, nullable=True)
    steals = Column(Float, nullable=True)
    blocks = Column(Float, nullable=True)
    turnovers = Column(Float, nullable=True)
    pass_yards = Column(Float, nullable=True)
    rush_yards = Column(Float, nullable=True)
    rec_yards = Column(Float, nullable=True)
    touchdowns = Column(Float, nullable=True)
    source = Column(String, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    is_stale = Column(Boolean, default=False)
    team = relationship("Team")


class ApiUsage(Base):
    __tablename__ = "api_usage"
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    month = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))


class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    starting_balance = Column(Float, nullable=False, default=1000000.0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))


class PaperPick(Base):
    __tablename__ = "paper_picks"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    pick_type = Column(String, nullable=False)  # "moneyline", "spread", "over_under", "prop"
    pick_value = Column(String, nullable=False)  # e.g. "HOME ML", "AWAY +3.5", "Over 220.5", "LeBron Over 25.5 Points"
    odds = Column(Integer, nullable=False)
    stake = Column(Float, nullable=False)  # dollar amount wagered
    result = Column(String, nullable=True)  # "win", "loss", "push", null=pending
    payout = Column(Float, nullable=True)  # net payout (positive for wins, negative for losses)
    prop_market = Column(String, nullable=True)  # e.g. "player_points" — only for prop picks
    prop_player = Column(String, nullable=True)  # player name — only for prop picks
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    user = relationship("UserProfile")
    game = relationship("Game")
