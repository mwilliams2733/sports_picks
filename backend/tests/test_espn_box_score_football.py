"""Football box scores parsed into the columns the prop analyzer reads.

`SPORT_PATHS` has listed nfl and ncaaf since the module was written, so
football games were fetched, parsed and stored all along -- as rows with
every football column NULL. On 2026-09-20 production held 1,965 nfl and
19,112 ncaaf `game_log` rows; `pass_yards`, `rush_yards`, `rec_yards`,
`receptions` and `touchdowns` were non-null in *zero* of them.

That is why the prop pipeline produced no picks after 2026-05-25. The
analyzer needs `_MIN_VARIANCE_SAMPLES` usable game-by-game values before
it returns an analysis; with every football column NULL it had none, and
returned None for all 2,082 analyzable nfl lines offered that day.

The 60 nfl rows that DID carry `points` name the cause exactly: every one
is a placekicker. ESPN's `kicking` block has a `PTS` column, and
`_LABEL_FIELD` -- written for basketball -- matched it. The parser was
never broken; it was reading football against basketball's schema.

Why `keys`, not `labels`
------------------------
A flat label map cannot express football. `YDS` appears in seven blocks
(passing, rushing, receiving, interceptions, kickReturns, puntReturns,
punting) meaning something different in each, and `REC` is *receptions*
under `receiving` but *fumbles recovered* under `fumbles`. ESPN ships a
parallel `keys` array whose names are globally unambiguous
(`passingYards`, `receivingYards`, `receptions`), so that is what the
parser keys on. `labels` stays as a fallback: every basketball fixture in
`test_espn_box_score.py` omits `keys`, and a payload without them must
still parse.
"""
from backend.collectors.espn_box_score import parse_box_score


def _football(*blocks, team="CAR"):
    """A summary with one team block holding the given statistics blocks."""
    return {"boxscore": {"players": [
        {"team": {"abbreviation": team}, "statistics": list(blocks)}
    ]}}


def _cat(name, keys, labels, *athletes):
    return {"name": name, "keys": list(keys), "labels": list(labels),
            "athletes": [{"athlete": {"displayName": n}, "stats": list(s)}
                         for n, s in athletes]}


# Shapes copied from a live ESPN payload (nfl event 401872933, 2026-09-20).
PASSING = ("passing",
           ["completions/passingAttempts", "passingYards", "yardsPerPassAttempt",
            "passingTouchdowns", "interceptions", "sacks-sackYardsLost",
            "adjQBR", "QBRating"],
           ["C/ATT", "YDS", "AVG", "TD", "INT", "SACKS", "QBR", "RTG"])
RUSHING = ("rushing",
           ["rushingAttempts", "rushingYards", "yardsPerRushAttempt",
            "rushingTouchdowns", "longRushing"],
           ["CAR", "YDS", "AVG", "TD", "LONG"])
RECEIVING = ("receiving",
             ["receptions", "receivingYards", "yardsPerReception",
              "receivingTouchdowns", "longReception", "receivingTargets"],
             ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"])
FUMBLES = ("fumbles", ["fumbles", "fumblesLost", "fumblesRecovered"],
           ["FUM", "LOST", "REC"])
KICKING = ("kicking",
           ["fieldGoalsMade/fieldGoalAttempts", "fieldGoalPct",
            "longFieldGoalMade", "extraPointsMade/extraPointAttempts",
            "totalKickingPoints"],
           ["FG", "PCT", "LONG", "XP", "PTS"])


def test_passing_yards_reach_the_pass_yards_column():
    """The whole defect in one assertion: 234 player_pass_yds lines were
    offered on 2026-09-20 and not one could be analyzed."""
    rows = parse_box_score(_football(
        _cat(*PASSING, ("Bryce Young",
                        ["23/36", "287", "8.0", "3", "0", "2-15", "92.9", "116.3"]))))

    assert len(rows) == 1
    assert rows[0]["pass_yards"] == 287.0


def test_rushing_and_receiving_yards_do_not_collide():
    """`YDS` is the label in seven different blocks. Keying on it puts
    receiving yards in whichever column was written last."""
    rows = parse_box_score(_football(
        _cat(*RUSHING, ("Chuba Hubbard", ["12", "53", "4.4", "0", "12"])),
        _cat(*RECEIVING, ("Tetairoa McMillan",
                          ["5", "101", "20.2", "0", "28", "10"]))))

    by_name = {r["player_name"]: r for r in rows}
    assert by_name["Chuba Hubbard"]["rush_yards"] == 53.0
    assert "rec_yards" not in by_name["Chuba Hubbard"]
    assert by_name["Tetairoa McMillan"]["rec_yards"] == 101.0
    assert by_name["Tetairoa McMillan"]["receptions"] == 5.0
    assert "rush_yards" not in by_name["Tetairoa McMillan"]


def test_a_player_in_two_blocks_becomes_one_merged_row():
    """A back who runs and catches appears under both `rushing` and
    `receiving`. Two rows upsert onto one another on
    (player_name, sport, stat_type, game_date), so the parser merges them
    here rather than emitting a row per block."""
    rows = parse_box_score(_football(
        _cat(*RUSHING, ("Chuba Hubbard", ["12", "53", "4.4", "0", "12"])),
        _cat(*RECEIVING, ("Chuba Hubbard", ["4", "31", "7.8", "0", "12", "5"]))))

    assert len(rows) == 1, "one player in one game is one row"
    assert rows[0]["rush_yards"] == 53.0
    assert rows[0]["rec_yards"] == 31.0
    assert rows[0]["receptions"] == 4.0


def test_touchdowns_sum_across_the_ways_a_player_scores():
    """`touchdowns` is one column fed by several blocks. Assigning rather
    than summing reports a two-touchdown game as one."""
    rows = parse_box_score(_football(
        _cat(*RUSHING, ("Chuba Hubbard", ["12", "53", "4.4", "1", "12"])),
        _cat(*RECEIVING, ("Chuba Hubbard", ["4", "31", "7.8", "1", "12", "5"]))))

    assert rows[0]["touchdowns"] == 2.0


def test_a_thrown_touchdown_is_not_a_scored_one():
    """`player_anytime_td` resolves on touchdowns SCORED. A quarterback who
    throws three and runs none has not scored; counting them would grade
    every QB anytime-TD Over as a win."""
    rows = parse_box_score(_football(
        _cat(*PASSING, ("Bryce Young",
                        ["23/36", "287", "8.0", "3", "0", "2-15", "92.9", "116.3"]))))

    assert rows[0].get("touchdowns", 0.0) == 0.0
    assert rows[0]["pass_yards"] == 287.0


def test_kicking_points_are_not_stored_as_points():
    """The 60 nfl rows that carried `points` were placekickers, matched by
    basketball `PTS`. `totalKickingPoints` is not the `points` column the
    analyzer means, and no football market reads it."""
    rows = parse_box_score(_football(
        _cat(*KICKING, ("Ryan Fitzgerald", ["2/2", "100.0", "37", "4/4", "10"]))))

    assert "points" not in rows[0], "kicking points are not basketball points"


def test_fumbles_recovered_is_not_receptions():
    """`REC` means receptions under `receiving` and fumbles recovered under
    `fumbles`. A label-keyed parser credits a lineman with a reception."""
    rows = parse_box_score(_football(
        _cat(*FUMBLES, ("Tommy Tremble", ["1", "0", "1"]))))

    assert "receptions" not in rows[0]


def test_a_named_block_without_keys_yields_nothing_rather_than_guesses():
    """The label fallback is basketball-only, and the block `name` is what
    distinguishes them: ESPN gives basketball one unnamed block, football a
    named block per category. Falling back to labels on a named block reads
    `REC` in `fumbles` as receptions and `YDS` in `punting` as passing
    yards -- numbers that look right and grade props wrongly."""
    rows = parse_box_score({"boxscore": {"players": [{
        "team": {"abbreviation": "CAR"},
        "statistics": [{
            "name": "fumbles",
            "labels": ["FUM", "LOST", "REC"],
            "athletes": [{"athlete": {"displayName": "Tommy Tremble"},
                          "stats": ["1", "0", "1"]}],
        }]}]}})

    assert rows == [], "no keys on a football block means no stats, not wrong ones"


def test_basketball_still_parses_from_labels_when_keys_are_absent():
    """Every basketball fixture in `test_espn_box_score.py` omits `keys`.
    A payload without them must keep working."""
    rows = parse_box_score({"boxscore": {"players": [{
        "team": {"abbreviation": "OKC"},
        "statistics": [{
            "labels": ["MIN", "PTS", "3PT", "REB", "AST"],
            "athletes": [{"athlete": {"displayName": "Chet Holmgren"},
                          "stats": ["26", "10", "2-7", "9", "2"]}],
        }]}]}})

    assert rows[0]["points"] == 10.0
    assert rows[0]["threes"] == 2.0
    assert rows[0]["rebounds"] == 9.0


def test_basketball_parses_from_keys_when_they_are_present():
    """The live nba payload does ship `keys`, including the made-attempted
    pair under a hyphenated name."""
    rows = parse_box_score({"boxscore": {"players": [{
        "team": {"abbreviation": "OKC"},
        "statistics": [{
            "keys": ["minutes", "points",
                     "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                     "rebounds", "assists"],
            "labels": ["MIN", "PTS", "3PT", "REB", "AST"],
            "athletes": [{"athlete": {"displayName": "Chet Holmgren"},
                          "stats": ["26", "10", "2-7", "9", "2"]}],
        }]}]}})

    assert rows[0]["points"] == 10.0
    assert rows[0]["threes"] == 2.0


def test_a_block_whose_keys_are_the_wrong_length_falls_back_to_labels():
    """Trusting a mismatched `keys` array would read every column shifted."""
    rows = parse_box_score({"boxscore": {"players": [{
        "statistics": [{
            "keys": ["points"],
            "labels": ["MIN", "PTS"],
            "athletes": [{"athlete": {"displayName": "X"}, "stats": ["26", "10"]}],
        }]}]}})

    assert rows[0]["minutes"] == 26.0
    assert rows[0]["points"] == 10.0


def test_a_did_not_play_football_athlete_produces_no_row():
    rows = parse_box_score(_football(_cat(*RUSHING, ("Benched Back", []))))
    assert rows == []
