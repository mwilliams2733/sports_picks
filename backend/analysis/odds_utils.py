def american_to_implied_prob(odds: int) -> float:
    if odds < 0: return abs(odds) / (abs(odds) + 100)
    else: return 100 / (odds + 100)

def calculate_payout(odds: int) -> float:
    if odds < 0: return 100 / abs(odds)
    else: return odds / 100


def remove_vig(home_implied: float, away_implied: float) -> tuple[float, float]:
    """Remove bookmaker vig using proportional method."""
    total = home_implied + away_implied
    return home_implied / total, away_implied / total


def no_vig_implied_prob(side: str, home_odds: int, away_odds: int) -> float:
    """Get vig-adjusted implied probability for a specific side.

    Args:
        side: "home" or "away"
        home_odds: American odds for home side
        away_odds: American odds for away side
    """
    home_raw = american_to_implied_prob(home_odds)
    away_raw = american_to_implied_prob(away_odds)
    home_fair, away_fair = remove_vig(home_raw, away_raw)
    return home_fair if side == "home" else away_fair
