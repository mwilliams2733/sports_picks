"""Guards for deleting the 2026-09-19 backfill's duplicated game logs."""
from datetime import date, timedelta

from backend.scripts.dedupe_game_logs import BACKFILL_DAY, STAT_COLUMNS, find_copies

D = date(2026, 2, 10)
LATER = date(2026, 9, 20)


def _row(i, d, fetched=LATER, points=40.0, player="Brunson"):
    r = {c: None for c in STAT_COLUMNS}
    r.update(id=i, sport="nba", player_name=player, team_id=4, game_date=d, fetched=fetched,
             minutes=42.0, points=points, rebounds=5.0, assists=8.0)
    return r


def test_games_decide_when_only_the_earlier_date_was_played():
    rows = [_row(1, D), _row(2, D + timedelta(days=1))]
    assert find_copies(rows, {("nba", 4): {D}}) == ([2], [])


def test_a_back_to_back_is_settled_by_the_backfill_day():
    """A game on both dates: the copy is the later row the backfill wrote."""
    played = {("nba", 4): {D, D + timedelta(days=1)}}
    rows = [_row(1, D), _row(2, D + timedelta(days=1), fetched=BACKFILL_DAY)]
    assert find_copies(rows, played) == ([2], [])


def test_anything_else_is_skipped_not_guessed():
    played = {("nba", 4): {D, D + timedelta(days=1)}}
    rows = [_row(1, D), _row(2, D + timedelta(days=1), fetched=LATER)]
    doomed, ambiguous = find_copies(rows, played)
    assert doomed == [] and ambiguous == [("nba", "Brunson", 4, D)]


def test_different_lines_on_adjacent_days_are_real_games():
    rows = [_row(1, D), _row(2, D + timedelta(days=1), points=12.0, fetched=BACKFILL_DAY)]
    assert find_copies(rows, {("nba", 4): {D}}) == ([], [])


def test_only_the_same_player_and_a_one_day_gap():
    rows = [_row(1, D), _row(2, D + timedelta(days=2), fetched=BACKFILL_DAY),
            _row(3, D + timedelta(days=1), fetched=BACKFILL_DAY, player="Hart")]
    assert find_copies(rows, {("nba", 4): {D}}) == ([], [])


def test_an_empty_line_proves_nothing():
    a, b = _row(1, D), _row(2, D + timedelta(days=1), fetched=BACKFILL_DAY)
    for r in (a, b):
        for c in STAT_COLUMNS:
            r[c] = None
    assert find_copies([a, b], {("nba", 4): {D}}) == ([], [])
