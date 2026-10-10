"""Human-readable rationale for a pick.

This module is the ONLY place that holds prose about why a pick was made.
Strategies emit structured PickFactor codes; everything a reader sees is
rendered here, so the wording stays consistent and testable.
"""
import json

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


def factors_from_json(text: str | None) -> list[PickFactor]:
    """A pick's stored `rationale_json` as factors; anything malformed is []."""
    if not text:
        return []
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    return [PickFactor(code=f.get("code", ""), side=f.get("side", "home"),
                       strength=f.get("strength", "moderate"))
            for f in raw if isinstance(f, dict)]


#: The sentence that closes every model-pick note, worded ONLY from measured
#: results. NFL/MLB: shrink weight 0.00, no edge over the closing line shown
#: (memory: shrunk edge / CLV report). Other sports: nothing measured to say.
SPORT_CAVEATS = {
    "nfl": ("The model has not shown an edge over NFL closing lines yet, so treat "
            "this as one opinion, not a sure thing."),
    "mlb": ("The model has not shown an edge over MLB closing lines yet, so treat "
            "this as one opinion, not a sure thing."),
    # config.yaml digest note: measured first, no edge over the close
    # (backend/scripts/nba_market_experiment.py).
    "nba": ("The model has not shown an edge over NBA closing lines yet, so treat "
            "this as one opinion, not a sure thing."),
}
DEFAULT_CAVEAT = "This is the model's opinion, not a sure thing; its record is on the Track Record page."

_OUTCOME = {"moneyline": "to win", "spread": "to cover", "over_under": "to hit"}


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _odds(o: int) -> str:
    return f"+{o}" if o > 0 else str(o)


def _who(pick_type: str, pick_value: str, home: str, away: str) -> str:
    side, _, rest = pick_value.partition(" ")
    if pick_type == "over_under":
        return f"the {pick_value}"
    team = home if side == "HOME" else away
    return team if pick_type == "moneyline" else f"{team} {rest}"


def pick_note(*, sport: str, pick_type: str, pick_value: str, home: str, away: str,
              model_prob: float, market_prob: float | None, edge_pct: float | None,
              odds: int | None, factors: list[PickFactor]) -> str:
    """Why the model made a game pick, in plain words, from its own numbers:
    its chance vs the market's margin-free chance, the edge it was priced at,
    the factors it actually computed, and the per-sport caveat. A missing
    number drops its clause; nothing is ever filled in."""
    from backend.analysis.odds_utils import prob_to_american
    first = (f"The model gives {_who(pick_type, pick_value, home, away)} a "
             f"{_pct(model_prob)} chance {_OUTCOME.get(pick_type, 'to win')}")
    if market_prob is not None:
        first += f"; the books' price, with their margin removed, says {_pct(market_prob)}"
    parts = [first + "."]
    if odds is not None and edge_pct is not None:
        price = f"At {_odds(odds)} that is a {edge_pct:.1f}% edge"
        if 0 < model_prob < 1:
            # "the model's": right after the books' margin-free chance, a bare
            # "fair price" reads as the books' own.
            price += f" (the model's fair price {_odds(prob_to_american(model_prob))})"
        parts.append(price + ".")
    because = render_rationale(factors, home, away)
    if because:
        parts.append(because)
    parts.append(SPORT_CAVEATS.get(sport, DEFAULT_CAVEAT))
    return " ".join(parts)
