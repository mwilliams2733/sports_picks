"""The CLV report's arithmetic and its refusals.

CLV is the only edge measurement that resolves before the game does, which
is exactly why it is easy to make it say something flattering. Three traps
are pinned here:

* **the null is zero, and it is two-sided.** A bettor with no edge lands
  either side of the close at random. Testing "is the mean positive" against
  a one-sided null finds an edge in half of all noise.
* **reconstructed prices are not observations.** `odds_reconstructed` marks
  picks whose `odds_at_pick` was rebuilt from surviving book rows rather
  than recorded at pick time. Their CLV is the reconstruction error.
* **games are not independent.** Every pick on one game shares its close, so
  a slate of correlated picks is not n independent observations.
"""
import pytest

from backend.analysis.clv_report import (ClvSample, Summary, mean_clv,
                                         measurable, summarize, usable)


def _s(clv=1.0, sport="nfl", market="moneyline", game_id=1,
       reconstructed=False):
    return ClvSample(pick_id=1, game_id=game_id, sport=sport, market=market,
                     clv=clv, reconstructed=reconstructed)


# --- what counts ----------------------------------------------------------

def test_a_pick_with_no_clv_is_not_a_sample():
    """No closing price on record is an ABSENT measurement, never a zero.
    Counting it as zero drags every average toward 'no edge'."""
    assert usable([ClvSample(1, 1, "nfl", "moneyline", None, False)]) == []


def test_a_reconstructed_price_is_excluded_by_default():
    """Its CLV is the error in the reconstruction, not market movement."""
    assert usable([_s(reconstructed=True)]) == []


def test_reconstructed_prices_can_be_included_deliberately():
    assert len(usable([_s(reconstructed=True)], include_reconstructed=True)) == 1


def test_an_ordinary_pick_counts():
    assert len(usable([_s()])) == 1


# --- the summary ----------------------------------------------------------

def test_the_mean_is_the_mean():
    assert mean_clv([_s(clv=1.0), _s(clv=3.0)]) == pytest.approx(2.0)


def test_an_empty_sample_has_no_mean():
    """None, not 0.0 -- absent is not neutral."""
    assert mean_clv([]) is None


def test_beat_rate_counts_strictly_positive_clv():
    """Landing exactly on the close is not beating it."""
    s = summarize([_s(clv=1.0), _s(clv=-1.0), _s(clv=0.0)])

    assert s.n == 3 and s.beat == 1


def test_a_positive_mean_with_a_tiny_sample_is_not_significant():
    """Four picks cannot establish an edge however good they look."""
    s = summarize([_s(clv=5.0, game_id=i) for i in range(4)])

    assert s.mean > 0
    assert s.p_value > 0.05, s


def test_a_large_consistent_edge_is_significant():
    """The other half: without it, a p_value hardcoded to 1.0 would pass
    every test above."""
    s = summarize([_s(clv=2.0 + (i % 3) * 0.5, game_id=i) for i in range(200)])

    assert s.p_value < 0.01, s


def test_the_test_is_two_sided():
    """A bettor with no edge lands either side of the close at random.
    A one-sided test finds an edge in half of all noise -- so a strongly
    NEGATIVE mean must also be significant, not merely 'not positive'."""
    s = summarize([_s(clv=-2.0 - (i % 3) * 0.5, game_id=i) for i in range(200)])

    assert s.mean < 0
    assert s.p_value < 0.01, s


def test_a_sample_with_no_variance_is_not_infinitely_significant():
    """Every pick beating the close by exactly 1.0 has zero standard error,
    which would divide by zero and report certainty from 3 observations."""
    s = summarize([_s(clv=1.0, game_id=i) for i in range(3)])

    assert s.p_value is not None
    assert 0.0 <= s.p_value <= 1.0


def test_one_pick_cannot_be_summarized():
    s = summarize([_s()])

    assert s.n == 1 and s.p_value == 1.0


def test_nothing_at_all_summarizes_to_an_empty_result():
    s = summarize([])

    assert s.n == 0 and s.mean is None and s.p_value == 1.0


# --- effective sample size ------------------------------------------------

def test_picks_on_one_game_are_not_independent_observations():
    """Three picks on one game share one close. Reporting n=3 overstates the
    evidence; the report carries the game count so the reader can see it."""
    s = summarize([_s(game_id=1), _s(game_id=1), _s(game_id=1)])

    assert s.n == 3
    assert s.games == 1


def test_picks_across_games_count_as_separate_games():
    s = summarize([_s(game_id=1), _s(game_id=2)])

    assert s.games == 2


def test_the_p_value_uses_games_not_picks():
    """The conservative choice. Ten picks on one game is one observation of
    one closing line, and treating it as ten manufactures significance."""
    # The CLV values must VARY. Forty identical numbers have no variance in
    # either grouping, so both would return 1.0 and the test would pass
    # against an implementation that ignored game clustering entirely.
    clvs = [2.0 + (i % 4) * 0.5 for i in range(40)]
    one_game = summarize([_s(clv=c, game_id=1) for c in clvs])
    many_games = summarize([_s(clv=c, game_id=i) for i, c in enumerate(clvs)])

    assert one_game.games == 1 and many_games.games == 40
    assert one_game.p_value > many_games.p_value
    assert one_game.p_value == 1.0, "one game cannot establish anything"


# --- is the CLV even measurable? ------------------------------------------

def test_a_line_never_seen_to_move_is_not_a_measurement():
    """One observation per book means the closing price IS the price the
    pick was made from. CLV against it is structurally near zero and is
    evidence of not having watched, not of matching the close."""
    assert measurable([_s()]) == []


def test_a_line_that_moved_is_measurable():
    """The other half: without it, `measurable` returning [] always would
    pass the test above."""
    assert len(measurable([ClvSample(1, 1, "nfl", "moneyline", 1.0, False,
                                     depth=2)])) == 1


def test_depth_defaults_to_one_so_an_unknown_series_is_not_trusted():
    """A sample built without depth information must not be assumed
    measurable -- the conservative direction."""
    assert ClvSample(1, 1, "nfl", "moneyline", 1.0, False).depth == 1
