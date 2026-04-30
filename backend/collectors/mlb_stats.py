"""Free MLB Stats API client. No auth required.

Used to fetch schedule + probable pitchers + pitcher recent rolling stats.
The Odds API and ESPN do not reliably surface probable starting pitchers,
which is the dominant feature for MLB game predictions.
"""
from __future__ import annotations
from datetime import date
import httpx


BASE_URL = "https://statsapi.mlb.com/api/v1"


class MLBStatsCollector:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def fetch_schedule(self, target_date: date) -> list[dict]:
        """Return today's games with probable pitcher IDs (if announced).

        Each item: {mlb_game_pk, home_team, away_team, home_probable_pitcher_id,
        away_probable_pitcher_id, game_date_iso}
        """
        url = f"{BASE_URL}/schedule"
        params = {
            "sportId": 1,  # MLB
            "date": target_date.strftime("%Y-%m-%d"),
            "hydrate": "probablePitcher",
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        out: list[dict] = []
        for d in data.get("dates", []):
            for g in d.get("games", []):
                home = g["teams"]["home"]
                away = g["teams"]["away"]
                out.append({
                    "mlb_game_pk": g["gamePk"],
                    "game_date_iso": g["gameDate"],
                    "home_team": home["team"].get("abbreviation"),
                    "away_team": away["team"].get("abbreviation"),
                    "home_probable_pitcher_id": (home.get("probablePitcher") or {}).get("id"),
                    "away_probable_pitcher_id": (away.get("probablePitcher") or {}).get("id"),
                })
        return out

    async def fetch_pitcher_recent(self, pitcher_id: int, season: int,
                                    last_n: int = 5) -> dict | None:
        """Return a dict {era_recent, k9_recent, starts_seen} for the pitcher's
        most recent N starts this season, or None if no starts logged.
        """
        url = f"{BASE_URL}/people/{pitcher_id}/stats"
        params = {"stats": "gameLog", "group": "pitching", "season": season}
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        splits = (response.json().get("stats") or [{}])[0].get("splits", [])
        if not splits:
            return None
        recent = splits[-last_n:] if last_n else splits
        eras: list[float] = []
        ks_total = 0
        ip_total = 0.0
        for s in recent:
            stat = s.get("stat", {})
            era_val = stat.get("era")
            if era_val is not None:
                try:
                    eras.append(float(era_val))
                except (TypeError, ValueError):
                    pass
            ks_total += int(stat.get("strikeOuts", 0) or 0)
            # MLB IP is decimal: 6.0 = 6 IP, 6.1 = 6 1/3, 6.2 = 6 2/3.
            ip_str = str(stat.get("inningsPitched", "0"))
            ip_total += _ip_to_float(ip_str)
        if not eras or ip_total <= 0:
            return None
        return {
            "era_recent": sum(eras) / len(eras),
            "k9_recent": ks_total * 9.0 / ip_total,
            "starts_seen": len(recent),
        }

    async def close(self):
        await self.client.aclose()


def _ip_to_float(ip: str) -> float:
    """Convert MLB-style innings-pitched string ('6.1' = 6 1/3) to a float."""
    try:
        whole, frac = ip.split(".")
        whole_n = int(whole)
        third = int(frac) if frac else 0
        return whole_n + third / 3.0
    except (ValueError, AttributeError):
        try:
            return float(ip)
        except (TypeError, ValueError):
            return 0.0
