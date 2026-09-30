from datetime import date, datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Float, Date, DateTime, ForeignKey, Boolean, Text,
    Index, UniqueConstraint
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
    #: ESPN's stable event id. The collector has always returned this
    #: (collectors/espn.py:40) and it was discarded for want of a column, so
    #: rows were identified by (date, teams) instead -- which cannot connect
    #: the same game arriving under two date conventions.
    espn_id = Column(String, nullable=True, index=True)
    #: The Odds API's stable event id. Captured by the collector since the
    #: beginning and discarded for want of a column, so a game was identified
    #: by (sport, date, teams) instead -- which cannot recognise the same
    #: event after the feed moves its date. The API drifts the placeholder
    #: date it uses for undated events, so one Makhachev-Usman future
    #: accumulated three rows.
    odds_api_id = Column(String, nullable=True, index=True)
    date = Column(Date, nullable=False)
    start_time = Column(DateTime, nullable=True)
    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    status = Column(String, nullable=False, default="scheduled")
    #: True when neither side is hosting -- a tournament bracket, a neutral
    #: showcase. `home_team_id` is then a seed or bracket designation, not a
    #: host, so home advantage does not apply and the model must not learn
    #: one from it. ESPN supplies this as `competitions[0].neutralSite`.
    #: server_default matters: raw sqlite3 INSERTs bypass the Python-side
    #: `default=`, and create_all would otherwise build a NOT NULL column
    #: with no database default, drifting from what the migration writes.
    neutral_site = Column(Boolean, nullable=False, default=False,
                          server_default="0")
    #: ESPN's season phase: "regular", "postseason", "preseason", "allstar".
    #: Read from the EVENT's season block, not the league's -- the league
    #: block still reports type 2 on a playoff date.
    #:
    #: Postseason basketball scores far less: the 21 postseason games here
    #: average 210.5 against a regular-season 231.1, and the totals model,
    #: fitted on regular-season rates, misses them by -17.09 on average
    #: (t = -3.80). "unknown" for rows that predate the column.
    season_type = Column(String, nullable=False, default="unknown",
                         server_default="unknown")
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

class EloHistory(Base):
    __tablename__ = "elo_history"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    sport = Column(String, nullable=False)
    rating = Column(Float, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class TeamBoxScore(Base):
    """One team's totals FROM a finished game -- a post-game fact.

    Deliberately not `TeamStat`, which holds **pre-game features** computed
    strictly before the game they hang off. A box score is a measurement OF
    that game, and mixing the two is how a lookahead gets written by
    someone reading `TeamStat(game_id=G)` and assuming it predates G. Same
    separation `player_stats.stat_type='game_log'` already uses.

    `possessions` is NULL unless every term of
    ``FGA - OREB + TOV + 0.44*FTA`` was present; `minutes` is NULL when the
    player block did not supply it. Neither is defaulted -- pace is per-48,
    so an assumed regulation length would misstate every overtime game.
    """
    __tablename__ = "team_box_scores"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    sport = Column(String, nullable=False)
    points = Column(Integer, nullable=True)
    fga = Column(Integer, nullable=True)
    oreb = Column(Integer, nullable=True)
    turnovers = Column(Integer, nullable=True)
    fta = Column(Integer, nullable=True)
    possessions = Column(Float, nullable=True)
    minutes = Column(Float, nullable=True)
    fetched_at = Column(DateTime, nullable=False,
                        default=lambda: datetime.now(tz=timezone.utc))


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
    #: American prices for the spread and total sides. The line is the point;
    #: these are what the bet actually pays. NULL on every row written before
    #: the collector stopped discarding them, which is why `STANDARD_JUICE`
    #: still exists as a named fallback.
    spread_home_price = Column(Integer, nullable=True)
    spread_away_price = Column(Integer, nullable=True)
    over_price = Column(Integer, nullable=True)
    under_price = Column(Integer, nullable=True)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))

class LineSnapshot(Base):
    """One observed price for one bookmaker on one game, never overwritten.

    `Odds` holds the CURRENT price and is upserted in place, so every quote
    this project has seen except the latest is discarded. This table is the
    series that upsert destroys: opening lines, line movement, and any
    closing-line-value measurement all need the prices that came before.

    Not named ``OddsSnapshot`` because `backend.data_types.OddsSnapshot` is
    the in-memory dataclass handed to strategies, and two different things
    under one name in one codebase is how the wrong one gets imported.

    A row is appended only when a price DIFFERS from the latest row for the
    same (game, bookmaker). An unchanged re-observation extends
    ``last_seen_at`` instead, so a line that held for six hours is one row
    that says so rather than six identical ones -- while still being
    distinguishable from a line nobody watched. The price columns are
    immutable; ``last_seen_at`` is the only field that ever changes.
    """
    __tablename__ = "line_snapshots"
    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    bookmaker = Column(String, nullable=False)
    moneyline_home = Column(Integer, nullable=True)
    moneyline_away = Column(Integer, nullable=True)
    spread_home = Column(Float, nullable=True)
    spread_away = Column(Float, nullable=True)
    over_under = Column(Float, nullable=True)
    spread_home_price = Column(Integer, nullable=True)
    spread_away_price = Column(Integer, nullable=True)
    over_price = Column(Integer, nullable=True)
    under_price = Column(Integer, nullable=True)
    #: When this price was FIRST seen. The series is ordered by this.
    captured_at = Column(DateTime, nullable=False,
                         default=lambda: datetime.now(tz=timezone.utc))
    #: When this price was most recently confirmed still on the board.
    last_seen_at = Column(DateTime, nullable=False,
                          default=lambda: datetime.now(tz=timezone.utc))

    __table_args__ = (
        Index("ix_line_snapshots_series", "game_id", "bookmaker", "captured_at"),
    )


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
    model_prob = Column(Float, nullable=True)
    #: Kelly stake in units, where one unit is 1% of bankroll. NULL for every
    #: pick made before this was persisted -- a backfilled default would be a
    #: number nobody computed. 0.0 means the sizer declined the bet.
    suggested_unit_size = Column(Float, nullable=True)
    rationale_json = Column(Text, nullable=True)
    # Only for pick_type="prop". prop_market holds the market KEY
    # ("player_threes"), not the display label ("3-Pointers"): MARKET_STAT_MAP
    # is keyed by the former, and _market_label is not one-to-one.
    prop_player = Column(String, nullable=True)
    prop_market = Column(String, nullable=True)
    #: True when odds_at_pick was RECONSTRUCTED from surviving book rows
    #: rather than recorded when the pick was made. See
    #: backend/scripts/repair_invalid_odds.py. Treat these rows as an
    #: approximation in any ROI or CLV figure.
    #: server_default matters: the migration adds this column with
    #: DEFAULT 0, and without it here create_all() would build a
    #: different schema -- NOT NULL with only a Python-side default,
    #: which any raw INSERT (tests, scripts) then violates.
    odds_reconstructed = Column(Boolean, nullable=False, default=False,
                                server_default="0")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    game = relationship("Game")

class PickResult(Base):
    __tablename__ = "pick_results"
    id = Column(Integer, primary_key=True)
    pick_id = Column(Integer, ForeignKey("picks.id"), nullable=False)
    result = Column(String, nullable=False)
    payout = Column(Float, nullable=False, default=0.0)
    odds_at_close = Column(Integer, nullable=True)
    line_at_close = Column(Float, nullable=True)
    pick = relationship("PickModel")

class EmailedPick(Base):
    """A pick as it went out in the daily digest.

    ``pick_value`` and ``odds`` are copies of what the email said, not a
    pointer to them: a game pick is refreshed in place until kickoff, so the
    stored pick's price -- or side -- can change after the email is sent.
    The emailed record is graded from these columns
    (``backend.digest.record``), never from the pick's current values.
    """
    __tablename__ = "emailed_picks"
    __table_args__ = (UniqueConstraint("digest_date", "pick_id",
                                       name="uq_emailed_pick_per_digest"),)
    id = Column(Integer, primary_key=True)
    digest_date = Column(Date, nullable=False, index=True)
    pick_id = Column(Integer, ForeignKey("picks.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    sport = Column(String, nullable=False)
    pick_type = Column(String, nullable=False)
    pick_value = Column(String, nullable=False)
    odds = Column(Integer, nullable=False)
    prop_player = Column(String, nullable=True)
    prop_market = Column(String, nullable=True)
    #: Stars as emailed, copied at send time like the price. Nullable: rows
    #: recorded before 2026-09-29 carry it only where it was verifiable.
    confidence = Column(Integer, nullable=True)
    sent_at = Column(DateTime, nullable=False,
                     default=lambda: datetime.now(tz=timezone.utc))


class PickVersion(Base):
    """One snapshot of a pick's tracked fields, appended whenever they change.

    `PickModel` is overwritten in place until kickoff -- `_refresh_pick`
    (`backend/pipeline/pick_generator.py`) and `_refresh_prop_pick`
    (`backend/pipeline/prop_pipeline.py`) both rewrite the same row, so every
    earlier side, price, edge and probability is otherwise lost. This table
    is the series that overwrite destroys, on the same append-on-change
    footing `LineSnapshot` uses for prices: a row is written only when a
    tracked field differs from the latest version for that `pick_id`
    (`backend/pipeline/pick_versions.py`'s `record_pick_version`). `picks`
    itself is untouched by this table's existence -- email, the frontend and
    grading all keep reading `PickModel` exactly as before.

    `version` is 1-based per `pick_id`, enforced unique by
    `uq_pick_versions_pick_version` below, so gaps or duplicates are a schema
    violation rather than a silent possibility. `source` records why the row
    was written: `'insert'` for a brand-new pick, `'refresh'` for an
    in-place rewrite, `'backfill'` for a version-1 row reconstructed after
    the fact for a pick that predates this table
    (`backend/scripts/backfill_pick_versions.py`) -- its `recorded_at` is the
    pick's `created_at`, not "now", because backfill time is not advice time.
    """
    __tablename__ = "pick_versions"
    __table_args__ = (
        UniqueConstraint("pick_id", "version",
                         name="uq_pick_versions_pick_version"),
    )
    id = Column(Integer, primary_key=True)
    #: ondelete="CASCADE" so deleting a bad pick (the correction path
    #: `backend/pipeline/pick_generator.py`'s regeneration tests exercise,
    #: and the operator workflow for a pick made on wrong inputs) also
    #: clears its version history rather than leaving orphaned rows a
    #: foreign-key check would then refuse -- `get_engine` turns SQLite's
    #: `PRAGMA foreign_keys=ON` on for every connection, so this is enforced
    #: by the database, not just documented here.
    pick_id = Column(Integer, ForeignKey("picks.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    version = Column(Integer, nullable=False)
    recorded_at = Column(DateTime, nullable=False)
    source = Column(String, nullable=False)  # "insert" | "refresh" | "backfill"
    pick_value = Column(String, nullable=True)
    confidence = Column(Integer, nullable=True)
    edge_pct = Column(Float, nullable=True)
    odds_at_pick = Column(Integer, nullable=True)
    model_prob = Column(Float, nullable=True)
    suggested_unit_size = Column(Float, nullable=True)
    rationale_json = Column(Text, nullable=True)


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
    receptions = Column(Float, nullable=True)
    touchdowns = Column(Float, nullable=True)
    source = Column(String, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    is_stale = Column(Boolean, default=False)
    team = relationship("Team")


class ApiUsage(Base):
    __tablename__ = "api_usage"
    id = Column(Integer, primary_key=True)
    endpoint = Column(String, nullable=False)  # "odds", "events", "player_props"
    sport = Column(String, nullable=False)
    credits_used = Column(Integer, nullable=False, default=1)
    requests_remaining = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc), index=True)


class UserProfile(Base):
    __tablename__ = "user_profiles"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    starting_balance = Column(Float, nullable=False, default=10000.0)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    current_streak = Column(Integer, default=0)
    best_streak = Column(Integer, default=0)
    streak_type = Column(String, default="none")

    #: Salted PBKDF2 of the player's PIN (backend.api.pins). NULL for players
    #: created before PINs existed; they set one on their next bet.
    pin_hash = Column(String, nullable=True)
    pin_salt = Column(String, nullable=True)


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
    parlay_id = Column(Integer, ForeignKey("parlays.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    graded_at = Column(DateTime, nullable=True)
    user = relationship("UserProfile")
    game = relationship("Game")
    parlay = relationship("Parlay", back_populates="legs")


class Parlay(Base):
    __tablename__ = "parlays"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id"), nullable=False)
    stake = Column(Float, nullable=False)
    combined_odds = Column(Integer, nullable=False)  # American odds for the parlay
    result = Column(String, nullable=True)  # "win", "loss", "push", null=pending
    payout = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(tz=timezone.utc))
    user = relationship("UserProfile")
    legs = relationship("PaperPick", back_populates="parlay")


class ActivityFeed(Base):
    __tablename__ = "activity_feed"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user_profiles.id"), nullable=True)
    event_type = Column(String, nullable=False)  # pick_placed, pick_won, pick_lost, streak
    payload = Column(Text, nullable=False)  # JSON with event details
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    user = relationship("UserProfile")


class CalibrationHistory(Base):
    __tablename__ = "calibration_history"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    sport = Column(String, nullable=False)
    confidence_tier = Column(Integer, nullable=False)
    predicted_win_rate = Column(Float)
    actual_win_rate = Column(Float)
    sample_size = Column(Integer)
    old_threshold = Column(Float)
    new_threshold = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ModelMetrics(Base):
    __tablename__ = "model_metrics"
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    sport = Column(String, nullable=False)
    model_version = Column(String, nullable=False)
    accuracy = Column(Float)
    log_loss = Column(Float)
    feature_importances = Column(String)  # JSON
    training_games = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
