"""The turnover experiment's two traps: lookahead, and who lost the fumble."""
import pandas as pd
import pytest

from backend.scripts.turnover_experiment import team_games_from_frame, walk_forward


def _play(gid, d, week, pos, interception=0, fumble_lost=0, fumbled=None):
    return {"game_id": gid, "season": 2025, "week": week, "game_date": d,
            "home_team": "KC", "away_team": "BUF", "posteam": pos,
            "interception": interception, "fumble_lost": fumble_lost,
            "fumbled_1_team": fumbled}


FRAME = pd.DataFrame([
    # game 1: KC throws an interception; BUF's punt returner fumbles while KC has the ball
    _play("g1", "2025-09-07", 1, "KC", interception=1),
    _play("g1", "2025-09-07", 1, "KC", fumble_lost=1, fumbled="BUF"),
    # game 2: KC loses two fumbles
    _play("g2", "2025-09-14", 2, "KC", fumble_lost=1, fumbled="KC"),
    _play("g2", "2025-09-14", 2, "KC", fumble_lost=1, fumbled="KC"),
    # game 3: nothing
    _play("g3", "2025-09-21", 3, "BUF"),
])


def _kc(games, gid):
    return next(t for t in games if t.game_id == gid and t.team == "KC")


def test_a_lost_fumble_is_charged_to_the_player_who_fumbled_not_the_offense():
    games = team_games_from_frame(FRAME)

    assert (_kc(games, "g1").giveaways, _kc(games, "g1").takeaways) == (1, 1)


def test_each_game_sees_only_earlier_games():
    feats = walk_forward(team_games_from_frame(FRAME))

    assert feats[("g1", "KC")] == {"to_margin": 0.0, "giveaways": 0.0}
    assert feats[("g2", "KC")]["to_margin"] == pytest.approx(0.0)       # g1: +1 -1
    assert feats[("g3", "KC")]["to_margin"] == pytest.approx(-1.0)      # (0 - 2) / 2
    assert feats[("g3", "KC")]["giveaways"] == pytest.approx(1.5)       # 3 / 2
