"""Human-readable rationale for a pick.

This module is the ONLY place that holds prose about why a pick was made.
Strategies emit structured PickFactor codes; everything a reader sees is
rendered here, so the wording stays consistent and testable.
"""
from backend.data_types import PickFactor

_STRENGTH_ADVERB = {
    "slight": "slightly",
    "moderate": "modestly",
    "strong": "strongly",
}

# {team} is the side the factor favors; {other} is the opposing team.
FACTOR_TEMPLATES = {
    "rating_gap": "Rating gap {adverb} favors {team}",
    "recent_form": "Recent scoring margin {adverb} favors {team}",
    "net_rating": "Net efficiency {adverb} favors {team}",
    "schedule_fatigue": "{other} on a compressed schedule",
    "lookahead_spot": "{other} may be looking ahead to its next game",
    "rest_advantage": "{team} has the rest advantage",
    "pitcher_edge": "Starting pitcher matchup favors {team}",
    "season_avg_vs_line": "Season average sits {adverb} on the {side} of this line",
    "recent_trend_vs_line": "Recent games trend {adverb} to the {side}",
    "stale_stats": "Caution: player stats may be out of date",
}

_STRENGTH_ORDER = {"strong": 0, "moderate": 1, "slight": 2}


def render_factor(factor: PickFactor, home_team: str, away_team: str) -> str:
    """Render one factor. Unknown codes render as "" rather than raising —
    a future strategy adding a factor must never break the digest."""
    template = FACTOR_TEMPLATES.get(factor.code)
    if template is None:
        return ""
    if factor.side == "home":
        team, other = home_team, away_team
    elif factor.side == "away":
        team, other = away_team, home_team
    else:
        team, other = home_team, away_team
    return template.format(
        adverb=_STRENGTH_ADVERB.get(factor.strength, "modestly"),
        team=team,
        other=other,
        side=factor.side,
    )


def render_rationale(
    factors: list[PickFactor],
    home_team: str,
    away_team: str,
    limit: int = 2,
) -> str:
    """Render the `limit` strongest factors as one sentence.

    Returns "" when there is nothing to say, so callers can omit the line
    entirely rather than printing an empty rationale.
    """
    ordered = sorted(factors, key=lambda f: _STRENGTH_ORDER.get(f.strength, 3))
    parts = []
    for f in ordered:
        text = render_factor(f, home_team, away_team)
        if text:
            parts.append(text)
        if len(parts) >= limit:
            break
    if not parts:
        return ""
    return "; ".join(parts) + "."
