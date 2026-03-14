import re
from backend.analysis.odds_utils import calculate_payout

def grade_pick(pick_type: str, pick_value: str, home_score: int, away_score: int, odds_at_pick: int) -> tuple[str, float]:
    if pick_type == "moneyline":
        if "HOME" in pick_value:
            won = home_score > away_score
        else:
            won = away_score > home_score
        if home_score == away_score:
            return "push", 0.0
    elif pick_type == "spread":
        match = re.search(r"([+-]?\d+\.?\d*)", pick_value)
        spread = float(match.group(1)) if match else 0.0
        if "HOME" in pick_value:
            margin = home_score - away_score + spread
        else:
            margin = away_score - home_score + spread
        if margin == 0:
            return "push", 0.0
        won = margin > 0
    elif pick_type == "over_under":
        match = re.search(r"(\d+\.?\d*)", pick_value)
        total_line = float(match.group(1)) if match else 0.0
        actual_total = home_score + away_score
        if actual_total == total_line:
            return "push", 0.0
        if "Over" in pick_value:
            won = actual_total > total_line
        else:
            won = actual_total < total_line
    else:
        return "loss", -1.0
    if won:
        return "win", calculate_payout(odds_at_pick)
    else:
        return "loss", -1.0
