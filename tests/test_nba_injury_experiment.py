"""Guards for the NBA star-out experiment's definitions."""
from datetime import date, timedelta

from backend.scripts import nba_injury_experiment as ne

D0 = date(2025, 11, 1)


def _r(minutes=30.0, points=10.0, rebounds=5.0, assists=3.0):
    return dict(minutes=minutes, points=points, rebounds=rebounds, assists=assists)


def _season(n_games, star_points=30.0, other="Role", star_plays=lambda i: True):
    """Team 1: a star scoring star_points, a role player scoring 10, a bench guy."""
    games = []
    for i in range(n_games):
        players = {other: _r(points=10.0), "Bench": _r(points=4.0, minutes=12.0)}
        if star_plays(i):
            players["Star"] = _r(points=star_points)
        games.append((D0 + timedelta(days=2 * i), players))
    return {1: games}


def test_no_star_until_he_has_enough_games():
    flags = ne.star_out_flags(_season(ne.MIN_STAR_GAMES + 2))
    first = sorted(d for (_, d) in flags)[0]
    assert first == D0 + timedelta(days=2 * ne.MIN_STAR_GAMES)      # game 11: 10 prior games


def test_star_is_chosen_from_earlier_games_only():
    """A role player's 60-point night makes him the leader only AFTER it."""
    games = _season(12)
    games[1][11][1]["Role"] = _r(points=400.0)
    flags = ne.star_out_flags(games)
    assert flags[(1, games[1][11][0])][0] == "Star"


def test_out_needs_a_recent_appearance():
    gone_after = ne.MIN_STAR_GAMES + 1
    games = _season(gone_after + ne.RECENT_GAMES + 2, star_plays=lambda i: i < gone_after)
    flags = ne.star_out_flags(games)
    dates = [d for d, _ in games[1]]
    assert flags[(1, dates[gone_after])][1] is True                     # just missed
    assert flags[(1, dates[gone_after + ne.RECENT_GAMES])][1] is False  # long gone


def test_the_star_is_never_his_own_teammate():
    """Once he is the star (from his 11th game) he is the treatment, never a
    teammate whose numbers move; before that he is an ordinary player."""
    games = _season(20)
    flags = ne.star_out_flags(games)
    rows = ne.teammate_rows(games, flags)
    star_games = {d for (_, d), (star, _) in flags.items() if star == "Star"}
    assert star_games
    assert not [r for r in rows if r[6] == "Star" and r[5] in star_games]
    assert [r for r in rows if r[6] == "Role" and r[5] in star_games]


def test_a_next_day_copy_of_a_game_is_dropped_but_a_real_back_to_back_is_kept():
    g1 = {"A": _r(points=20.0), "B": _r(points=12.0)}
    copy = {"A": _r(points=20.0), "B": _r(points=12.0)}
    real = {"A": _r(points=25.0), "B": _r(points=8.0)}
    kept = ne.drop_duplicated_games([(D0, g1), (D0 + timedelta(days=1), copy),
                                     (D0 + timedelta(days=2), real)])
    assert [d for d, _ in kept] == [D0, D0 + timedelta(days=2)]
    b2b = ne.drop_duplicated_games([(D0, g1), (D0 + timedelta(days=1), real)])
    assert len(b2b) == 2


def test_only_prop_sized_players_count():
    """The bench player averages 4 points (under the 8-point floor) and 5
    rebounds (over the 4-rebound floor)."""
    games = _season(20)
    rows = ne.teammate_rows(games, ne.star_out_flags(games))
    assert not [r for r in rows if r[0] == "points" and r[6] == "Bench"]
    assert [r for r in rows if r[0] == "rebounds" and r[6] == "Bench"]
