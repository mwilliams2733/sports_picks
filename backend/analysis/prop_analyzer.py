from backend.models import PlayerProp, PlayerStat
from backend.data_types import PropAnalysis
from backend.analysis.prop_confidence import calculate_prop_confidence

MARKET_TO_STAT: dict[str, list[str]] = {
    "player_points": ["points"],
    "player_rebounds": ["rebounds"],
    "player_assists": ["assists"],
    "player_threes": ["threes"],
    "player_steals": ["steals"],
    "player_blocks": ["blocks"],
    "player_turnovers": ["turnovers"],
    "player_points_rebounds": ["points", "rebounds"],
    "player_points_assists": ["points", "assists"],
    "player_rebounds_assists": ["rebounds", "assists"],
    "player_points_rebounds_assists": ["points", "rebounds", "assists"],
    "player_pass_yards": ["pass_yards"],
    "player_rush_yards": ["rush_yards"],
    "player_rec_yards": ["rec_yards"],
    "player_touchdowns": ["touchdowns"],
}


class PropAnalyzer:
    def __init__(
        self,
        season_weight: float = 0.4,
        recent_weight: float = 0.6,
        min_edge: float = 5.0,
    ):
        self.season_weight = season_weight
        self.recent_weight = recent_weight
        self.min_edge = min_edge

    def analyze(
        self,
        prop: PlayerProp,
        season_avg: PlayerStat | None,
        recent_games: list[PlayerStat],
    ) -> PropAnalysis | None:
        # Validate line exists
        if prop.line is None:
            return None

        # Validate market is known
        fields = MARKET_TO_STAT.get(prop.market)
        if fields is None:
            return None

        # Compute season average value for this market
        season_val: float | None = None
        if season_avg is not None:
            season_val = self._sum_fields(season_avg, fields)

        # Compute recent average value for this market
        recent_val: float | None = None
        if recent_games:
            game_vals = [self._sum_fields(g, fields) for g in recent_games]
            valid_vals = [v for v in game_vals if v is not None]
            if valid_vals:
                recent_val = sum(valid_vals) / len(valid_vals)

        # Need at least one source of stats
        if season_val is None and recent_val is None:
            return None

        # Compute projection as weighted blend
        if season_val is not None and recent_val is not None:
            projection = self.season_weight * season_val + self.recent_weight * recent_val
        elif season_val is not None:
            projection = season_val
        else:
            projection = recent_val  # type: ignore[assignment]

        line = prop.line
        diff = projection - line
        edge_pct = abs(diff / line) * 100

        # Determine signed edge: positive means the edge is in the correct direction
        if prop.outcome == "Over":
            signed_edge = diff  # positive when projection > line
        else:  # Under
            signed_edge = -diff  # positive when projection < line

        if signed_edge < 0:
            return None

        if edge_pct < self.min_edge:
            return None

        confidence = calculate_prop_confidence(edge_pct)

        # Determine source/stale from season_avg or first recent game
        source = "unknown"
        is_stale = False
        if season_avg is not None:
            source = season_avg.source
            is_stale = bool(season_avg.is_stale)
        elif recent_games:
            source = recent_games[0].source
            is_stale = bool(recent_games[0].is_stale)

        return PropAnalysis(
            player_name=prop.player_name,
            market=prop.market,
            line=line,
            outcome=prop.outcome,
            season_avg=season_val,
            recent_avg=recent_val,
            projection=projection,
            edge_pct=edge_pct,
            confidence=confidence,
            source=source,
            is_stale=is_stale,
            game_id=prop.game_id,
            odds=prop.odds,
        )

    @staticmethod
    def _sum_fields(stat: PlayerStat, fields: list[str]) -> float | None:
        total = 0.0
        for f in fields:
            val = getattr(stat, f, None)
            if val is None:
                return None
            total += val
        return total
