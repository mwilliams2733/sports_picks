"""Guards against confidence tiers built on signals that carry no data.

Three defects, all confirmed against the live database on 2026-09-20:

* `_count_agreeing_models` counts `offensive_rating - defensive_rating` as a
  third vote. Nothing in this repository computes those stats -- they need
  possessions, which no collector fetches -- so both sides read the 100.0
  default and the comparison is `0 > 0` for every game in every sport. A tie
  is absence of evidence, but the `>=` gate counted it as evidence against,
  which is what made tier 5 (min 3 votes) unreachable through this path.

* `_count_total_agreeing_models` compares those same defaults against magic
  constants: `avg_pace <= 100`, `combined_off <= 200`, `combined_def <= 200`.
  All three are true by construction, so EVERY under scored 3/3 and every
  over 0/3. In the database: all 42 ncaab tier-5 picks are unders, and every
  over across boxing, mlb, mma and ncaaf sits at tier 1.

* With no history at all -- ncaaf before the 2026-09-20 backfill -- every
  signal ties, so every pick collapsed to tier 1.

The dangerous outcome is not a low tier. It is a HIGH tier asserted from
nothing, which the recalibrator then treats as a real cohort.
"""
from backend.analysis.confidence import (
    DEFAULT_MIN_MODELS, DEFAULT_THRESHOLDS, calculate_confidence,
)


def test_a_tier_still_needs_its_votes_when_every_signal_is_available():
    """The existing behaviour is the calibration point: three real signals
    must produce exactly what they produced before."""
    for tier, needed in DEFAULT_MIN_MODELS.items():
        edge = DEFAULT_THRESHOLDS[tier]
        assert calculate_confidence(edge, needed, models_available=3) >= tier


def test_no_available_signal_caps_the_tier_at_one():
    """A huge edge with nothing agreeing is still an unsupported edge."""
    assert calculate_confidence(99.0, 0, models_available=0) == 1


def test_unanimity_among_two_real_signals_can_still_reach_the_top():
    """Two signals that both agree is stronger evidence than two of three."""
    assert calculate_confidence(99.0, 2, models_available=2) == 5


def test_a_split_among_two_real_signals_lands_where_a_split_belongs():
    """One of two is a disagreement, not a two-thirds majority.

    The requirement must round UP when it is scaled: tier 4 asks for 2 of 3,
    which is 2 of 2, not 1 of 2. Rounding down would promote a split vote to
    the same tier as a unanimous one.
    """
    assert calculate_confidence(99.0, 1, models_available=2) == 2


def test_a_dead_third_signal_no_longer_blocks_the_top_tier():
    """The live defect: two real signals agree, the third cannot ever fire."""
    assert calculate_confidence(99.0, 2, models_available=2) == 5
    # ...whereas counting the dead signal as a dissenting vote capped it.
    assert calculate_confidence(99.0, 2, models_available=3) == 4


def test_the_edge_threshold_still_binds():
    """Availability must not become a way past the edge requirement."""
    assert calculate_confidence(1.0, 2, models_available=2) == 0


def test_default_availability_preserves_existing_callers():
    """combat_sports and recent_form pass no availability; they must be
    unchanged."""
    assert calculate_confidence(99.0, 2) == calculate_confidence(
        99.0, 2, models_available=3)


# --- the counters themselves ------------------------------------------------

from backend.analysis.variants.ensemble import EnsembleStrategy
from backend.data_types import GameData, TeamStats


def _stats(point_diff=0.0, elo=1500.0, off=100.0, deff=100.0, pace=100.0):
    return TeamStats(
        point_diff=point_diff, home_record=(0, 0), away_record=(0, 0),
        last_n_record=(0, 0), offensive_rating=off, defensive_rating=deff,
        pace=pace, strength_of_schedule=0.5, elo_rating=elo, rest_days=2)


def _game(home, away):
    import datetime
    return GameData(game_id=1, sport="ncaaf", date=datetime.date(2026, 9, 19),
                    home_team_id=1, away_team_id=2,
                    home_stats=home, away_stats=away, odds=[])


def _strategy():
    return EnsembleStrategy(name="t", config={})


def test_a_tied_signal_is_not_counted_as_available():
    """Two teams with no history tie on everything: nothing is available."""
    agreeing, available = _strategy()._count_agreeing_models(
        _game(_stats(), _stats()), "home")
    assert (agreeing, available) == (0, 0)


def test_only_the_signals_that_differ_are_available():
    """point_diff and elo differ; off-def is equal on both sides as always."""
    game = _game(_stats(point_diff=10.0, elo=1600.0),
                 _stats(point_diff=-5.0, elo=1500.0))
    assert _strategy()._count_agreeing_models(game, "home") == (2, 2)


def test_a_differing_signal_that_favours_the_other_side_still_counts_available():
    """Available means informative, not agreeing."""
    game = _game(_stats(point_diff=10.0, elo=1400.0),
                 _stats(point_diff=-5.0, elo=1600.0))
    assert _strategy()._count_agreeing_models(game, "home") == (1, 2)


def test_the_totals_counter_claims_no_evidence():
    """Its three signals all read stats nothing computes, so every under
    scored 3/3 and every over 0/3. It must report no evidence, not a
    unanimous one."""
    game = _game(_stats(), _stats())
    strategy = _strategy()
    assert strategy._count_total_agreeing_models(game, True) == (0, 0)
    assert strategy._count_total_agreeing_models(game, False) == (0, 0)
