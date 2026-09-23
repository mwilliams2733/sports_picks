"""Walk-forward EPA/play ratings for NFL teams.

The experiment these support asks whether an EPA rating carries information
the closing spread does not. That question is only meaningful if a team's
rating for game N is built from games 1..N-1 and nothing else, so the
lookahead tests below are the load-bearing ones: a rating that peeks at its
own game would produce a beautiful and completely false result.
"""
import datetime

import pytest

from backend.analysis.epa_ratings import (GARBAGE_TIME_WP, PRIOR_PLAYS,
                                          SCRIMMAGE_PLAYS, SEASON_CARRYOVER,
                                          MarginFit, Play, Rating,
                                          aggregate_team_games, fit_margin,
                                          predict_margin, usable_plays,
                                          walk_forward_ratings)


def _play(**kw):
    base = dict(game_id="2024_01_AAA_BBB", season=2024, week=1,
                game_date=datetime.date(2024, 9, 8), home_team="BBB",
                away_team="AAA", posteam="AAA", defteam="BBB",
                play_type="pass", epa=0.5, wp=0.5, aborted_play=0)
    base.update(kw)
    return Play(**base)


# --- which plays count ----------------------------------------------------

def test_only_scrimmage_plays_count():
    """Punts, kickoffs and field goals have EPA but measure special teams,
    not the offence the rating claims to describe."""
    assert SCRIMMAGE_PLAYS == ("pass", "rush")
    kept = usable_plays([_play(play_type="pass"), _play(play_type="punt"),
                         _play(play_type="field_goal"), _play(play_type="rush")])

    assert [p.play_type for p in kept] == ["pass", "rush"]


def test_a_play_with_no_epa_is_dropped():
    """nflverse leaves epa null on plays it cannot model. Treating null as
    0.0 would silently pull every rating toward average."""
    assert usable_plays([_play(epa=None)]) == []


def test_a_play_with_no_possessing_team_is_dropped():
    assert usable_plays([_play(posteam=None)]) == []


def test_an_aborted_play_is_dropped():
    assert usable_plays([_play(aborted_play=1)]) == []


def test_garbage_time_is_dropped_by_default():
    """A 31-point lead in the fourth changes how both teams play. Leaving
    those snaps in measures score state, not team strength."""
    assert GARBAGE_TIME_WP == 0.05
    kept = usable_plays([_play(wp=0.5), _play(wp=0.01), _play(wp=0.99)])

    assert len(kept) == 1


def test_garbage_time_can_be_kept():
    """The filter is a judgement call, so the experiment can measure both."""
    assert len(usable_plays([_play(wp=0.5), _play(wp=0.01)],
                            drop_garbage_time=False)) == 2


def test_a_play_with_no_win_probability_survives_the_garbage_filter():
    """Unknown is not the same as lopsided -- absent, not defaulted."""
    assert len(usable_plays([_play(wp=None)])) == 1


# --- aggregating a game ---------------------------------------------------

def test_a_teams_offence_is_its_own_epa_per_play():
    games = aggregate_team_games([_play(posteam="AAA", defteam="BBB", epa=1.0),
                                  _play(posteam="AAA", defteam="BBB", epa=0.0)])

    aaa = next(g for g in games if g.team == "AAA")
    assert aaa.off_epa == pytest.approx(0.5)
    assert aaa.off_plays == 2


def test_a_teams_defence_is_the_epa_it_allowed():
    """Stored from the DEFENCE's point of view and NOT sign-flipped: a good
    defence has a low def_epa. The margin model does the subtracting."""
    games = aggregate_team_games([_play(posteam="AAA", defteam="BBB", epa=1.0),
                                  _play(posteam="AAA", defteam="BBB", epa=0.0)])

    bbb = next(g for g in games if g.team == "BBB")
    assert bbb.def_epa == pytest.approx(0.5)
    assert bbb.def_plays == 2


def test_both_sides_of_one_game_are_produced():
    games = aggregate_team_games([_play(posteam="AAA"),
                                  _play(posteam="BBB", defteam="AAA")])
    assert sorted(g.team for g in games) == ["AAA", "BBB"]


def test_home_and_away_are_recorded():
    games = aggregate_team_games([_play(posteam="AAA"),
                                  _play(posteam="BBB", defteam="AAA")])
    assert {g.team: g.is_home for g in games} == {"AAA": False, "BBB": True}


def test_a_team_that_never_had_the_ball_still_gets_a_defensive_row():
    """A shutout-by-turnovers game is rare but real, and dropping the row
    would silently remove that game from the opponent's schedule."""
    games = aggregate_team_games([_play(posteam="AAA", defteam="BBB")])

    bbb = next(g for g in games if g.team == "BBB")
    assert bbb.off_plays == 0 and bbb.def_plays == 1


# --- walk-forward ---------------------------------------------------------

def _season(n_games, epa_by_game, team="AAA", opp="BBB", season=2024):
    """n_games one-play games for team, each with the given offensive EPA."""
    return [_play(game_id=f"{season}_{i:02d}_{team}_{opp}", season=season,
                  week=i + 1, game_date=datetime.date(season, 9, 8)
                  + datetime.timedelta(days=7 * i),
                  home_team=opp, away_team=team, posteam=team, defteam=opp,
                  epa=epa_by_game[i])
            for i in range(n_games)]


def test_the_first_game_has_no_prior_information():
    ratings = walk_forward_ratings(aggregate_team_games(_season(1, [1.0])))

    first = ratings[("2024_00_AAA_BBB", "AAA")]
    assert first.off_plays == 0, "a team's first game cannot have a history"
    assert first.off == pytest.approx(0.0)


def test_a_rating_is_built_from_earlier_games_only(monkeypatch):
    """THE test. Change game 3 and game 3's own rating must not move.

    A rating that included its own game would score every prediction against
    a partial answer key.
    """
    import backend.analysis.epa_ratings as er
    monkeypatch.setattr(er, "PRIOR_PLAYS", 0.0)   # no shrinkage, so a leak shows

    quiet = _season(4, [0.1, 0.1, 0.1, 0.1])
    loud = _season(4, [0.1, 0.1, 9.9, 0.1])
    key = ("2024_02_AAA_BBB", "AAA")

    before = er.walk_forward_ratings(er.aggregate_team_games(quiet))[key]
    after = er.walk_forward_ratings(er.aggregate_team_games(loud))[key]

    assert after.off == pytest.approx(before.off), "game 3 leaked into its own rating"


def test_that_same_change_does_reach_the_next_game(monkeypatch):
    """The other half. Without this, a rating function that ignored every
    game would pass the lookahead test above."""
    import backend.analysis.epa_ratings as er
    monkeypatch.setattr(er, "PRIOR_PLAYS", 0.0)

    quiet = _season(4, [0.1, 0.1, 0.1, 0.1])
    loud = _season(4, [0.1, 0.1, 9.9, 0.1])
    key = ("2024_03_AAA_BBB", "AAA")

    before = er.walk_forward_ratings(er.aggregate_team_games(quiet))[key]
    after = er.walk_forward_ratings(er.aggregate_team_games(loud))[key]

    assert after.off > before.off + 1.0, "game 3 never reached game 4's rating"


def test_recent_games_weigh_more_than_old_ones(monkeypatch):
    """Same two prior games, opposite order. Comparing different histories
    instead would pass against a rating that ignored order entirely."""
    import backend.analysis.epa_ratings as er
    monkeypatch.setattr(er, "PRIOR_PLAYS", 0.0)

    improving = er.walk_forward_ratings(er.aggregate_team_games(
        _season(3, [0.0, 1.0, 0.0])))[("2024_02_AAA_BBB", "AAA")]
    declining = er.walk_forward_ratings(er.aggregate_team_games(
        _season(3, [1.0, 0.0, 0.0])))[("2024_02_AAA_BBB", "AAA")]

    assert improving.off > declining.off


def test_a_short_history_is_regressed_toward_average():
    """One great game does not make a great team. With PRIOR_PLAYS of
    weightless average snaps in the denominator, a 1-play history barely
    moves the rating."""
    assert PRIOR_PLAYS > 0
    ratings = walk_forward_ratings(aggregate_team_games(_season(2, [5.0, 0.0])))

    second = ratings[("2024_01_AAA_BBB", "AAA")]
    assert 0.0 < second.off < 0.5, second.off


def test_a_new_season_keeps_only_part_of_the_old_one():
    """Rosters and coaches turn over. Carrying a rating across the offseason
    undiluted treats March as if it were another bye week."""
    assert 0.0 < SEASON_CARRYOVER < 1.0
    plays = _season(3, [1.0, 1.0, 1.0], season=2023) + _season(1, [0.0],
                                                               season=2024)
    ratings = walk_forward_ratings(aggregate_team_games(plays))

    carried = ratings[("2024_00_AAA_BBB", "AAA")]
    last_year = ratings[("2023_02_AAA_BBB", "AAA")]
    assert 0.0 < carried.off < last_year.off


# --- turning ratings into points ------------------------------------------

def test_the_margin_fit_recovers_a_known_relationship():
    """Synthetic: margin is exactly 3 + 20 * edge, so the fit must find it."""
    rows = [(edge, 3.0 + 20.0 * edge) for edge in (-0.3, -0.1, 0.0, 0.2, 0.4)]
    fit = fit_margin(rows)

    assert fit.home_field == pytest.approx(3.0)
    assert fit.points_per_epa == pytest.approx(20.0)


def test_predicting_a_margin_uses_both_sides_of_the_ball():
    """edge = (home_off - home_def) - (away_off - away_def). A model using
    offence alone would rate these two teams identically when one has a far
    better defence."""
    fit = MarginFit(home_field=2.0, points_per_epa=10.0)
    good_d = Rating(off=0.0, deff=-0.2, off_plays=500, def_plays=500)
    bad_d = Rating(off=0.0, deff=0.2, off_plays=500, def_plays=500)
    average = Rating(off=0.0, deff=0.0, off_plays=500, def_plays=500)

    assert (predict_margin(good_d, average, fit)
            > predict_margin(bad_d, average, fit))


def test_two_average_teams_produce_the_home_field_edge():
    fit = MarginFit(home_field=2.0, points_per_epa=10.0)
    avg = Rating(off=0.0, deff=0.0, off_plays=500, def_plays=500)

    assert predict_margin(avg, avg, fit) == pytest.approx(2.0)


def test_a_margin_is_positive_when_the_home_team_is_better():
    """Sign convention: margin is home_score - away_score, matching
    Odds.spread_home once negated."""
    fit = MarginFit(home_field=0.0, points_per_epa=10.0)
    strong = Rating(off=0.3, deff=-0.1, off_plays=500, def_plays=500)
    weak = Rating(off=-0.2, deff=0.1, off_plays=500, def_plays=500)

    assert predict_margin(strong, weak, fit) > 0
    assert predict_margin(weak, strong, fit) < 0


def test_a_fit_needs_more_than_one_point():
    with pytest.raises(ValueError):
        fit_margin([(0.1, 3.0)])
