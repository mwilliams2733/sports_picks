"""Bookmaker dedup for prop picks.

The same prop (game/player/market/outcome/line) is stored once per bookmaker,
all carrying an identical edge (edge ignores odds). The pipeline must emit a
single pick per prop, keeping the best price for the bettor.
"""
from backend.data_types import PropAnalysis
from backend.pipeline.prop_pipeline import _dedup_prop_analyses


def _analysis(odds: int, line: float = 7.5, outcome: str = "Over",
              player: str = "SGA", market: str = "player_assists",
              game_id: int = 1, bookmaker: str = "draftkings") -> PropAnalysis:
    return PropAnalysis(
        player_name=player, market=market, line=line, outcome=outcome,
        season_avg=9.0, recent_avg=9.0, projection=9.0, edge_pct=18.48,
        confidence=4, source="nba_api", is_stale=False, game_id=game_id, odds=odds,
        bookmaker=bookmaker,
    )


def test_same_prop_across_books_collapses_to_one_best_price():
    # Three books offering the identical Over 7.5 assists prop at different prices.
    analyses = [_analysis(odds=-110), _analysis(odds=120), _analysis(odds=-130)]

    result = _dedup_prop_analyses(analyses)

    assert len(result) == 1
    # +120 pays 1.20/unit vs -110 (0.909) and -130 (0.769): best price wins.
    assert result[0].odds == 120


def test_different_lines_stay_separate():
    analyses = [_analysis(odds=-110, line=7.5), _analysis(odds=-110, line=8.5)]

    result = _dedup_prop_analyses(analyses)

    assert len(result) == 2


def test_over_and_under_same_line_stay_separate():
    analyses = [_analysis(odds=-110, outcome="Over"),
                _analysis(odds=-110, outcome="Under")]

    result = _dedup_prop_analyses(analyses)

    assert len(result) == 2


def test_same_prop_different_games_stay_separate():
    analyses = [_analysis(odds=-110, game_id=1), _analysis(odds=-110, game_id=2)]

    result = _dedup_prop_analyses(analyses)

    assert len(result) == 2


def test_tied_payout_breaks_tie_by_bookmaker_alphabetically():
    # Same payout (-110 == -110) at two different books — must resolve the
    # same way regardless of which one appears first in the input list.
    fanduel = _analysis(odds=-110, bookmaker="fanduel")
    draftkings = _analysis(odds=-110, bookmaker="draftkings")

    result_a = _dedup_prop_analyses([fanduel, draftkings])
    result_b = _dedup_prop_analyses([draftkings, fanduel])

    assert len(result_a) == 1 and len(result_b) == 1
    assert result_a[0].bookmaker == "draftkings"
    assert result_b[0].bookmaker == "draftkings"


def test_invalid_odds_are_skipped_not_raised():
    # A prop with unusable odds (0) must not crash the whole dedup pass; it's
    # dropped and the remaining valid props still come through.
    analyses = [_analysis(odds=0), _analysis(odds=-110, player="Someone Else")]

    result = _dedup_prop_analyses(analyses)

    assert len(result) == 1
    assert result[0].player_name == "Someone Else"
