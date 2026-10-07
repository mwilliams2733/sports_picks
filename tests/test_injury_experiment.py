"""Guards for the two places the injury experiment could fool itself:
lookahead in the walk-forward leader, and who counts as absent."""
from collections import Counter

import pandas as pd

from backend.scripts.injury_experiment import (Availability, TeamGame,
                                               walk_forward_leaders)


def _tg(week, attempts, game_id=None):
    return TeamGame(game_id or f"g{week}", 2025, week, "BUF",
                    attempts=Counter(attempts))


def test_leader_uses_only_prior_games():
    # B throws 40 in week 2; the week-2 leader must still be A from week 1.
    games = [_tg(1, {"A": 30}), _tg(2, {"B": 40}), _tg(3, {"A": 5})]
    leaders = walk_forward_leaders(games)
    assert ("g1", "BUF") not in leaders or "qb" not in leaders[("g1", "BUF")]
    assert leaders[("g2", "BUF")]["qb"] == "A"
    assert leaders[("g3", "BUF")]["qb"] == "B"


def test_leader_ignores_input_order():
    games = [_tg(3, {"A": 5}), _tg(2, {"B": 40}), _tg(1, {"A": 30})]
    assert walk_forward_leaders(games)[("g2", "BUF")]["qb"] == "A"


def _injuries(rows, with_season_type=False):
    cols = ["season", "game_type", "team", "week", "gsis_id", "report_status"]
    df = pd.DataFrame(rows, columns=cols)
    if with_season_type:
        df.insert(1, "season_type", df.game_type)
    return df


def _rosters(rows):
    return pd.DataFrame(rows, columns=["season", "week", "team", "gsis_id",
                                       "status", "game_type"])


def test_report_without_season_type_column_counts():
    # 2022-2024 files have no season_type; they must not be dropped.
    old = _injuries([(2023, "REG", "BUF", 5, "QB1", "Out")])
    new = _injuries([(2025, "REG", "BUF", 5, "QB1", "Doubtful")], with_season_type=True)
    avail = Availability(pd.concat([old, new]))
    assert avail.absent(2023, 5, "BUF", "QB1")
    assert avail.absent(2025, 5, "BUF", "QB1")


def test_questionable_is_not_absent():
    avail = Availability(_injuries([(2025, "REG", "BUF", 5, "QB1", "Questionable")]))
    assert not avail.absent(2025, 5, "BUF", "QB1")
    assert avail.status(2025, 5, "BUF", "QB1") == "Questionable"


def test_roster_catches_injured_reserve_the_report_omits():
    rosters = _rosters([(2025, 6, "BUF", "QB1", "RES", "REG"),
                        (2025, 6, "BUF", "QB2", "ACT", "REG")])
    avail = Availability(_injuries([]), rosters)
    assert avail.absent(2025, 6, "BUF", "QB1")
    assert not avail.absent(2025, 6, "BUF", "QB2")
    # Gone from the roster entirely (released, traded) is absent too.
    assert avail.absent(2025, 6, "BUF", "QB9")


def test_unknown_roster_week_is_not_everyone_absent():
    rosters = _rosters([(2025, 6, "BUF", "QB1", "ACT", "REG")])
    avail = Availability(_injuries([]), rosters)
    assert not avail.absent(2025, 7, "BUF", "QB1")
    assert not avail.absent(2025, 6, "MIA", "QB1")
