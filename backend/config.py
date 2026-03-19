import os
from datetime import date
import yaml
from dotenv import load_dotenv

load_dotenv(override=False)

def load_config(path: str) -> dict:
    with open(path) as f:
        config = yaml.safe_load(f)
    api_key = os.environ.get("ODDS_API_KEY")
    if api_key:
        config["odds_api_key"] = api_key
    return config

def is_sport_in_season(sport: str, seasons: dict, today: date | None = None) -> bool:
    today = today or date.today()
    season = seasons.get(sport)
    if not season:
        return False
    start_month, start_day = map(int, season["start"].split("-"))
    end_month, end_day = map(int, season["end"].split("-"))
    start = date(today.year, start_month, start_day)
    end = date(today.year, end_month, end_day)
    if start <= end:
        return start <= today <= end
    else:
        return today >= start or today <= end
