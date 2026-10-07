"""Game-time forecasts for outdoor NFL games, from Open-Meteo (free, no key).

Captured in each NFL window, about 2h before kickoff, and appended to
`game_weather`. The hours and the stadium rule are
`analysis.football_weather`'s, the same ones the forecast backtest measured.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.orm import Session

from backend.analysis.football_weather import FORECAST_HOURS, outdoor_stadium
from backend.collectors.espn_http import get_with_retry
from backend.models import Game, GameWeather, Team

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def summarize_hours(payload: dict, kickoff_utc: datetime) -> tuple[float, float, float] | None:
    """(kickoff temp F, precip mm over FORECAST_HOURS, mean wind mph) from an
    hourly Open-Meteo payload in UTC, or None if any hour is missing."""
    h = payload.get("hourly") or {}
    by_time = {t: (tt, p, w) for t, tt, p, w in zip(
        h.get("time", []), h.get("temperature_2m", []),
        h.get("precipitation", []), h.get("wind_speed_10m", []))}
    start = kickoff_utc.replace(minute=0, second=0, microsecond=0)
    hours = [by_time.get((start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00"))
             for i in range(FORECAST_HOURS)]
    if any(x is None or None in x for x in hours):
        return None
    return (hours[0][0], sum(x[1] for x in hours), sum(x[2] for x in hours) / len(hours))


async def fetch_forecast(client: httpx.AsyncClient, lat: float, lon: float,
                         kickoff_utc: datetime) -> tuple[float, float, float] | None:
    end = (kickoff_utc + timedelta(hours=FORECAST_HOURS)).date()
    try:
        resp = await get_with_retry(client, FORECAST_URL, params=dict(
            latitude=lat, longitude=lon, hourly="temperature_2m,precipitation,wind_speed_10m",
            temperature_unit="fahrenheit", wind_speed_unit="mph", timezone="UTC",
            start_date=kickoff_utc.date().isoformat(), end_date=end.isoformat()))
        resp.raise_for_status()
        return summarize_hours(resp.json(), kickoff_utc)
    except Exception as exc:
        logger.warning("weather: forecast at %.4f,%.4f failed: %s", lat, lon, exc)
        return None


async def collect_game_weather(session: Session, games: list[Game]) -> dict[int, GameWeather]:
    """Capture and append a forecast for each outdoor NFL game in `games`.

    Skips neutral sites (the home team's stadium is not the venue), roofed
    and retractable stadiums, and games with no kickoff time yet. Returns
    game_id -> the row just stored; a failed fetch stores nothing.
    """
    out: dict[int, GameWeather] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        for g in games:
            if g.sport != "nfl" or g.neutral_site or g.start_time is None:
                continue
            home = session.get(Team, g.home_team_id)
            stadium = outdoor_stadium(home.abbreviation) if home else None
            if stadium is None:
                continue
            kickoff = g.start_time.replace(tzinfo=timezone.utc) if g.start_time.tzinfo is None \
                else g.start_time.astimezone(timezone.utc)
            fc = await fetch_forecast(client, stadium["lat"], stadium["lon"], kickoff)
            if fc is None:
                continue
            row = GameWeather(game_id=g.id, kickoff=kickoff.replace(tzinfo=None),
                              stadium=stadium["stadium"], temp_f=fc[0], precip_mm=fc[1],
                              wind_mph=fc[2], captured_at=datetime.now(timezone.utc))
            session.add(row)
            out[g.id] = row
    session.commit()
    if out:
        logger.info("weather: captured %d NFL forecast(s): %s", len(out),
                    {gid: (round(r.precip_mm, 1), round(r.wind_mph)) for gid, r in out.items()})
    return out


def latest_weather(session: Session, game_ids) -> dict[int, GameWeather]:
    """game_id -> most recent captured forecast."""
    out: dict[int, GameWeather] = {}
    for row in (session.query(GameWeather).filter(GameWeather.game_id.in_(list(game_ids)))
                .order_by(GameWeather.captured_at)):
        out[row.game_id] = row
    return out
