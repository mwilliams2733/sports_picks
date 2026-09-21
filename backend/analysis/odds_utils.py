import re

#: The conventional price on a spread or total when the real one is unknown.
#:
#: `ensemble` used to write this as a bare ``-110`` at four call sites, for
#: `odds_at_pick` (what grading pays out against, and what CLV is measured
#: from) and for the Kelly stake. It was standing in for a price the
#: collector discarded: `_parse_bookmaker` read ``point`` for spreads and
#: totals and dropped ``price``, which the API returns beside it.
#:
#: The collector now captures the real price, so this applies only where
#: none was quoted -- notably every Odds row written before those columns
#: existed. Named rather than repeated so a fallback is visibly a fallback.
STANDARD_JUICE = -110


class InvalidOddsError(ValueError):
    """Raised when an American odds value is 0 or outside the valid range.

    Valid American odds are always <= -100 or >= 100; anything in between
    (including 0) doesn't correspond to a real price.
    """


def _validate_american_odds(odds: int) -> None:
    if odds == 0 or -100 < odds < 100:
        raise InvalidOddsError(f"Invalid American odds: {odds}")


def american_to_implied_prob(odds: int) -> float:
    _validate_american_odds(odds)
    if odds < 0: return abs(odds) / (abs(odds) + 100)
    else: return 100 / (odds + 100)

def calculate_payout(odds: int) -> float:
    _validate_american_odds(odds)
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


def parse_pick_line(pick_value: str) -> float | None:
    """Extract the numeric line from a spread or over_under pick_value.

    Spread values look like "HOME -3.5" / "AWAY +4.0".
    Total values look like "Over 218.5" / "Under 220.5".
    Returns None if no numeric line can be parsed.
    """
    match = re.search(r"([+-]?\d+\.?\d*)", pick_value)
    return float(match.group(1)) if match else None


def signed_line_clv(pick_type: str, pick_value: str, line_at_close: float) -> float | None:
    """Return CLV in line points where positive = bettor beat the close.

    For spread: pick_line - close_line works for both HOME and AWAY because the
    away line is the negation of the home line and the bettor's preferred
    direction flips with the sign.
    For totals: OVER wants close > pick (line moved up = bettor took the lower);
    UNDER wants close < pick.
    """
    pick_line = parse_pick_line(pick_value)
    if pick_line is None:
        return None
    if pick_type == "spread":
        return pick_line - line_at_close
    if pick_type == "over_under":
        if "Over" in pick_value:
            return line_at_close - pick_line
        if "Under" in pick_value:
            return pick_line - line_at_close
    return None


def compute_pick_clv(
    pick_type: str,
    pick_value: str,
    odds_at_pick: int | None,
    odds_at_close: int | None,
    line_at_close: float | None,
) -> tuple[float | None, float | None]:
    """Return (clv_pct, clv_points) for a single graded pick.

    clv_pct is meaningful for moneyline only: implied-probability delta in
    percentage points. Positive = bettor's price beat the close.
    clv_points is meaningful for spread / over_under only: how many line points
    the bettor beat the close by. Positive = better line at pick time.
    Returns (None, None) for ungradeable picks (props, missing data).
    """
    if pick_type == "moneyline":
        if odds_at_pick is None or odds_at_close is None:
            return None, None
        clv_pct = (american_to_implied_prob(odds_at_close) - american_to_implied_prob(odds_at_pick)) * 100
        return clv_pct, None
    if pick_type in ("spread", "over_under"):
        if line_at_close is None:
            return None, None
        return None, signed_line_clv(pick_type, pick_value, line_at_close)
    return None, None
