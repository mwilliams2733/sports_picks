"""Every emitted factor code must be one the strategy actually consumed.

`Strategy._build_factors` can derive all seven codes from TeamStats, and every
variant calls it identically — so before FACTOR_CODES, RecentFormStrategy told
readers that "rest advantage" and "starting pitcher matchup" drove a pick whose
model never looked at either. These sentences go out by email as the reasoning
behind a bet.

The fixture below makes EVERY signal fire, so a variant that fails to narrow
its declared set is caught rather than passing by accident.
"""
from datetime import date

import pytest

from backend.data_types import GameData, TeamStats, OddsSnapshot
from backend.analysis.strategy import Strategy
from backend.analysis.variants.recent_form import RecentFormStrategy
from backend.analysis.variants.value_only import ValueOnlyStrategy
from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.analysis.variants.sport_specific import SportSpecificStrategy
from backend.analysis.variants.combat_sports import CombatSportsStrategy

ALL_CODES = frozenset({
    "rating_gap", "recent_form", "net_rating", "schedule_fatigue",
    "lookahead_spot", "rest_advantage", "pitcher_edge",
})


def _loaded_stats(**over):
    """Home-favoring on every single signal at 'strong' magnitude."""
    base = dict(
        point_diff=12.0, home_record=(9, 1), away_record=(8, 2),
        last_n_record=(9, 1), offensive_rating=120.0, defensive_rating=95.0,
        pace=100.0, strength_of_schedule=0.5, elo_rating=1750.0, rest_days=8,
        is_schedule_fatigued=False, is_lookahead_spot=False,
        schedule_fatigue_score=0.0, pitcher_skill_score=0.90,
    )
    base.update(over)
    return TeamStats(**base)


def _loaded_game(sport="mlb"):
    """Big elo gap, big point-diff gap, fatigue, lookahead, rest gap, pitcher
    scores — every code in ALL_CODES would fire for "home" if unfiltered."""
    return GameData(
        game_id=1, sport=sport, date=date(2026, 5, 1),
        home_team_id=1, away_team_id=2,
        home_stats=_loaded_stats(),
        away_stats=_loaded_stats(
            point_diff=-10.0, home_record=(2, 8), away_record=(1, 9),
            last_n_record=(1, 9), offensive_rating=95.0, defensive_rating=120.0,
            elo_rating=1400.0, rest_days=1, is_schedule_fatigued=True,
            is_lookahead_spot=True, schedule_fatigue_score=1.0,
            pitcher_skill_score=0.20,
        ),
        odds=[OddsSnapshot(bookmaker="dk", moneyline_home=200, moneyline_away=200,
                           spread_home=0.0, spread_away=0.0, over_under=45.0)],
    )


def test_the_fixture_really_fires_every_code():
    """Without this, a narrowing test could pass because nothing fired at all."""
    class _Unfiltered(Strategy):
        FACTOR_CODES = ALL_CODES

        def predict(self, game):
            return []

    emitted = {f.code for f in _Unfiltered("x", {})._build_factors(_loaded_game(), "home")}
    assert emitted == ALL_CODES, f"fixture is too weak: only fired {sorted(emitted)}"


@pytest.mark.parametrize("cls,expected", [
    (RecentFormStrategy, frozenset({"recent_form"})),
    (ValueOnlyStrategy, frozenset({"rating_gap", "recent_form", "net_rating"})),
    (EnsembleStrategy, frozenset({"rating_gap", "recent_form", "net_rating",
                                  "schedule_fatigue", "lookahead_spot"})),
    (SportSpecificStrategy, ALL_CODES),
    (CombatSportsStrategy, frozenset()),
])
def test_declared_codes_match_the_code_that_was_read(cls, expected):
    assert cls.FACTOR_CODES == expected


@pytest.mark.parametrize("cls", [
    RecentFormStrategy, ValueOnlyStrategy, EnsembleStrategy,
    SportSpecificStrategy, CombatSportsStrategy,
])
def test_emitted_codes_are_a_subset_of_the_declared_set(cls):
    strategy = cls(cls.__name__, {"min_edge": 0.1})
    game = _loaded_game()
    for side in ("home", "away"):
        emitted = {f.code for f in strategy._build_factors(game, side)}
        assert emitted <= cls.FACTOR_CODES, (
            f"{cls.__name__} emitted {sorted(emitted - cls.FACTOR_CODES)}, "
            f"which its model never computes"
        )


@pytest.mark.parametrize("cls", [
    RecentFormStrategy, ValueOnlyStrategy, EnsembleStrategy, SportSpecificStrategy,
])
def test_real_predict_output_also_respects_the_declared_set(cls):
    """Not just _build_factors in isolation — the picks that reach the digest."""
    strategy = cls(cls.__name__, {"min_edge": 0.1})
    picks = strategy.predict(_loaded_game())
    for p in picks:
        emitted = {f.code for f in p.factors}
        assert emitted <= cls.FACTOR_CODES, (
            f"{cls.__name__} pick {p.pick_value!r} emitted "
            f"{sorted(emitted - cls.FACTOR_CODES)}"
        )


def test_combat_sports_emits_nothing_from_teamstats():
    """Its model reads fighter Elo/form, not TeamStats — no base code applies."""
    s = CombatSportsStrategy("combat", {"min_edge": 0.1})
    assert s._build_factors(_loaded_game("mma"), "home") == []
