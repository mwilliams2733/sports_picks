import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

from backend.collectors.player_stats.base import PlayerStatsSource
from backend.models import PlayerStat

logger = logging.getLogger(__name__)
STALENESS_HOURS = 24


class PlayerStatsCollector:
    def __init__(self, fallback_chains: dict[str, list[PlayerStatsSource]]):
        self.chains = fallback_chains

    async def fetch_player_stats(self, sport: str, team_abbr: str) -> tuple[list[dict], str | None]:
        """Fetch season averages using fallback chain. Returns (stats, source_name)."""
        chain = self.chains.get(sport, [])
        for source in chain:
            try:
                result = await source.fetch_season_averages(sport, team_abbr)
                if result:
                    logger.info(f"Got {len(result)} player stats from {source.name} for {team_abbr}")
                    return result, source.name
                logger.info(f"{source.name} returned empty for {team_abbr}, trying next")
            except Exception as e:
                logger.warning(f"{source.name} failed for {team_abbr}: {e}")
        return [], None

    async def fetch_player_recent(self, sport: str, player_name: str, n: int = 5) -> tuple[list[dict], str | None]:
        """Fetch recent game logs using fallback chain."""
        chain = self.chains.get(sport, [])
        for source in chain:
            try:
                result = await source.fetch_recent_games(sport, player_name, n)
                if result:
                    logger.info(f"Got {len(result)} game logs from {source.name} for {player_name}")
                    return result, source.name
            except Exception as e:
                logger.warning(f"{source.name} failed for {player_name}: {e}")
        return [], None

    def store_stats(self, session: Session, stats: list[dict], stat_type: str,
                    team_id: int, sport: str, source: str, is_stale: bool = False) -> int:
        """Normalize and store stats. Upserts on (player_name, sport, stat_type, game_date)."""
        count = 0
        now = datetime.now(tz=timezone.utc)
        for s in stats:
            name = self._normalize_name(s.get("player_name", ""))
            if not name:
                continue
            game_date = s.get("game_date")
            if isinstance(game_date, str) and game_date:
                from datetime import date as date_type
                parts = game_date.split("-")
                game_date = date_type(int(parts[0]), int(parts[1]), int(parts[2]))
            elif stat_type == "season_avg":
                game_date = None

            existing = session.query(PlayerStat).filter_by(
                player_name=name, sport=sport, stat_type=stat_type, game_date=game_date
            ).first()

            if existing:
                for field in ["minutes", "points", "rebounds", "assists", "threes",
                              "steals", "blocks", "turnovers", "pass_yards",
                              "rush_yards", "rec_yards", "touchdowns"]:
                    val = s.get(field)
                    if val is not None:
                        setattr(existing, field, float(val))
                existing.source = source
                existing.fetched_at = now
                existing.is_stale = is_stale
            else:
                row = PlayerStat(
                    player_name=name, team_id=team_id, sport=sport,
                    stat_type=stat_type, game_date=game_date,
                    minutes=s.get("minutes"), points=s.get("points"),
                    rebounds=s.get("rebounds"), assists=s.get("assists"),
                    threes=s.get("threes"), steals=s.get("steals"),
                    blocks=s.get("blocks"), turnovers=s.get("turnovers"),
                    pass_yards=s.get("pass_yards"), rush_yards=s.get("rush_yards"),
                    rec_yards=s.get("rec_yards"), touchdowns=s.get("touchdowns"),
                    source=source, fetched_at=now, is_stale=is_stale,
                )
                session.add(row)
            count += 1
        session.commit()
        return count

    def get_cached_stats(self, session: Session, player_name: str, sport: str,
                         stat_type: str) -> list[PlayerStat]:
        """Get cached stats, marking as stale if older than threshold."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=STALENESS_HOURS)
        rows = session.query(PlayerStat).filter_by(
            player_name=self._normalize_name(player_name), sport=sport, stat_type=stat_type,
        ).all()
        for row in rows:
            if row.fetched_at < cutoff:
                row.is_stale = True
        if rows:
            session.commit()
        return rows

    async def close(self):
        """Close all source HTTP clients."""
        for chain in self.chains.values():
            for source in chain:
                if hasattr(source, "close"):
                    await source.close()

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize to 'First Last' format."""
        name = name.strip()
        if not name:
            return ""
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            if len(parts) == 2:
                name = f"{parts[1]} {parts[0]}"
        return name
