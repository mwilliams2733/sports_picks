from datetime import date

from backend.data_types import GameData, TeamStats, OddsSnapshot
from backend.analysis.variants.value_only import ValueOnlyStrategy


def _stats(point_diff, elo, off, deff, rest=2, fatigued=False, lookahead=False):
    return TeamStats(
        point_diff=point_diff, home_record=(5, 2), away_record=(4, 3),
        last_n_record=(7, 3), offensive_rating=off, defensive_rating=deff,
        pace=100.0, strength_of_schedule=0.5, elo_rating=elo, rest_days=rest,
        is_schedule_fatigued=fatigued, is_lookahead_spot=lookahead,
    )


def _game():
    return GameData(
        game_id=1, sport="nfl", date=date(2026, 3, 1),
        home_team_id=1, away_team_id=2,
        home_stats=_stats(6.0, 1650.0, 110.0, 100.0, rest=7),
        away_stats=_stats(0.0, 1500.0, 100.0, 105.0, rest=3, fatigued=True),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=-110, moneyline_away=-110,
                           spread_home=0.0, spread_away=0.0, over_under=45.0)],
    )


def test_moneyline_pick_carries_factors():
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    ml = [p for p in picks if p.pick_type == "moneyline"]
    assert ml, "expected a moneyline pick from this setup"
    assert ml[0].factors, "moneyline picks must carry factors"


def test_factors_use_known_codes_and_valid_fields():
    from backend.analysis.rationale import FACTOR_TEMPLATES
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    for p in picks:
        for f in p.factors:
            assert f.code in FACTOR_TEMPLATES, f"unknown factor code {f.code}"
            assert f.side in ("home", "away", "over", "under")
            assert f.strength in ("slight", "moderate", "strong")


def test_rating_gap_favors_the_stronger_side():
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    ml = [p for p in picks if p.pick_type == "moneyline"][0]
    gap = [f for f in ml.factors if f.code == "rating_gap"]
    assert gap, "expected a rating_gap factor"
    assert gap[0].side == "home", "home has the higher elo in this fixture"


def test_value_only_does_not_claim_fatigue_it_never_read():
    """ValueOnlyStrategy reads point_diff, elo and net rating only."""
    s = ValueOnlyStrategy("value_only", {"min_edge": 0.1})
    picks = s.predict(_game())
    ml = [p for p in picks if p.pick_type == "moneyline"][0]
    assert not [f for f in ml.factors if f.code == "schedule_fatigue"], (
        "away team is fatigued in this fixture, but ValueOnlyStrategy never "
        "reads is_schedule_fatigued — saying so would be a fabrication"
    )


def test_fatigue_factor_points_at_the_fatigued_team():
    # SportSpecificStrategy genuinely applies the fatigue adjustment.
    from backend.analysis.variants.sport_specific import SportSpecificStrategy
    s = SportSpecificStrategy("sport_specific", {"min_edge": 0.1})
    picks = s.predict(_game())
    ml = [p for p in picks if p.pick_type == "moneyline"][0]
    fatigue = [f for f in ml.factors if f.code == "schedule_fatigue"]
    assert fatigue, "away team is fatigued in this fixture"
    assert fatigue[0].side == "home", (
        "schedule_fatigue favors the RESTED side; the template names the other team"
    )
