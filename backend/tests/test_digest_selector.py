import os
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.config import load_config
from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, StrategyModel, PickModel, PickResult
from backend.digest.selector import select_digest, DigestDiagnostics

#: Repo root, computed the same way backend/scripts/check_digest.py does it,
#: so this test finds the real config.yaml regardless of the runner's cwd.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_CONFIG_PATH = os.path.join(_REPO_ROOT, "config.yaml")

SEASONS = {"nfl": {"start": "09-05", "end": "02-10"},
           "nba": {"start": "10-22", "end": "06-20"}}

#: A wide-open bar: no price window, generous caps. Used by tests that
#: exercise something other than the price window itself (dedupe, matchup
#: text, rationale, ...). There is no edge gate any more -- blend_weight is
#: recorded only, not used for filtering or ranking.
OPEN_BAR = {"min_odds": -100_000, "max_odds": 100_000,
           "max_game_picks": 10, "max_props": 10, "blend_weight": {}}


def _bar(**overrides):
    return {**OPEN_BAR, **overrides}


def _mk(session, sport, gid, tid_h, tid_a, d, picks):
    session.add_all([
        Team(id=tid_h, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
        Team(id=tid_a, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport),
    ])
    session.flush()
    session.add(Game(id=gid, sport=sport, season="2026", date=d,
                     home_team_id=tid_h, away_team_id=tid_a, status="scheduled"))
    session.flush()
    for i, (conf, edge) in enumerate(picks):
        session.add(PickModel(game_id=gid, strategy_id=1, pick_type="moneyline",
                              pick_value=f"P{gid}-{i}", confidence=conf,
                              edge_pct=edge, odds_at_pick=-110))
    session.commit()


def _mk_priced(session, sport, gid, tid_h, tid_a, d, picks, pick_type="moneyline",
              start_time=None):
    """picks: list of (confidence, edge, odds, model_prob)."""
    session.add_all([
        Team(id=tid_h, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
        Team(id=tid_a, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport),
    ])
    session.flush()
    session.add(Game(id=gid, sport=sport, season="2026", date=d, start_time=start_time,
                     home_team_id=tid_h, away_team_id=tid_a, status="scheduled"))
    session.flush()
    for i, (conf, edge, odds, prob) in enumerate(picks):
        session.add(PickModel(game_id=gid, strategy_id=1, pick_type=pick_type,
                              pick_value=f"P{gid}-{i}", confidence=conf,
                              edge_pct=edge, odds_at_pick=odds, model_prob=prob))
    session.commit()


def _session():
    engine = get_engine(":memory:")
    Base.metadata.create_all(engine)
    s = get_session(engine)
    s.add(StrategyModel(id=1, name="x", config_json="{}", is_active=True))
    s.commit()
    return s


# --- structural behavior unaffected by the send bar ------------------------

def test_caps_at_max_game_picks_and_never_pads():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0), (5, 8.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=_bar(max_game_picks=5))
    assert len(sections[0].picks) == 2, "must not pad to five"


def test_out_of_season_sport_is_omitted():
    s = _session()
    d = date(2026, 7, 1)  # NFL out of season
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections == []


def test_a_sport_in_season_with_no_games_still_gets_an_empty_section():
    """Both nfl and nba are in season on 2026-11-01; only nfl has a game.
    nba must still appear, empty, with diagnostics -- the empty-day email
    needs a line for it ("no picks generated today")."""
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl", "nba"], SEASONS, send_bar=OPEN_BAR)
    by_sport = {sec.sport: sec for sec in sections}
    assert set(by_sport) == {"nfl", "nba"}
    assert by_sport["nba"].picks == []
    assert by_sport["nba"].props == []
    assert by_sport["nba"].diagnostics == DigestDiagnostics(
        games=0, generated=0, survived_price=0, lambda_used=0.0, lambda_measured=False)


def test_zero_confidence_picks_are_excluded_but_the_section_still_appears():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(0, 20.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert len(sections) == 1
    assert sections[0].picks == []
    assert sections[0].diagnostics.generated == 0


def test_matchup_reads_away_at_home():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].picks[0].matchup == "A1 at H1"


def test_digest_pick_carries_both_team_names():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    pick = sections[0].picks[0]
    assert pick.home_team == "H1"
    assert pick.away_team == "A1"


def test_a_missing_team_row_falls_back_without_crashing():
    # `games.home_team_id` has a foreign key to `teams`, so a persisted Game
    # cannot actually reference a missing Team row -- the schema forbids it.
    # But `_team_names` still has to be defensive: it is a lookup by id, and
    # a lookup can always miss. Exercise the fallback directly rather than
    # fighting the FK constraint to construct an impossible row.
    from backend.digest.selector import _team_names

    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    # A transient (never added/committed) Game object, so the FK constraint
    # never gets a chance to fire.
    game = Game(sport="nfl", season="2026", date=d,
                home_team_id=999, away_team_id=2, status="scheduled")
    away_name, home_name = _team_names(s, game)
    assert home_name == "Home"
    assert away_name == "A1"

    # And the section still renders when a real digest is built for the
    # (unmodified, DB-valid) game.
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].picks[0].home_team == "H1"


def test_prop_picks_never_rank_against_game_picks():
    # prop_pipeline writes analyzed props into the SAME picks table with
    # pick_type="prop". A prop's edge_pct is (prob - 0.5) * 200 and ignores
    # the prop's price, so it is not comparable to a game pick's de-vigged
    # edge. A juiced prop with a huge nominal edge must not appear in -- let
    # alone top -- the game-pick list.
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(3, 4.0)])
    s.add(PickModel(game_id=1, strategy_id=1, pick_type="prop",
                    pick_value="Mahomes Over 275.5 Pass Yards",
                    prop_market="player_pass_yds",
                    confidence=5, edge_pct=40.0, odds_at_pick=-300))
    s.commit()

    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    game_values = [p.pick_value for p in sections[0].picks]
    assert "Mahomes Over 275.5 Pass Yards" not in game_values
    assert game_values == ["P1-0"]

    prop_values = [p.pick_value for p in sections[0].props]
    assert prop_values == ["Mahomes Over 275.5 Pass Yards"]
    assert sections[0].props[0].confidence == 5, (
        "props carry real confidence from PickModel, not a placeholder 0"
    )


def _mk_with_rationale(session, sport, gid, tid_h, tid_a, d, rationale_json):
    session.add_all([
        Team(id=tid_h, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
        Team(id=tid_a, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport),
    ])
    session.flush()
    session.add(Game(id=gid, sport=sport, season="2026", date=d,
                     home_team_id=tid_h, away_team_id=tid_a, status="scheduled"))
    session.flush()
    session.add(PickModel(game_id=gid, strategy_id=1, pick_type="moneyline",
                          pick_value=f"P{gid}-0", confidence=5,
                          edge_pct=9.0, odds_at_pick=-110,
                          rationale_json=rationale_json))
    session.commit()


def test_rationale_json_null_degrades_to_empty_string():
    s = _session()
    d = date(2026, 11, 1)
    _mk_with_rationale(s, "nfl", 1, 1, 2, d, "null")
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].picks[0].rationale == ""


def test_rationale_json_number_degrades_to_empty_string():
    s = _session()
    d = date(2026, 11, 1)
    _mk_with_rationale(s, "nfl", 1, 1, 2, d, "5")
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].picks[0].rationale == ""


def test_rationale_json_dict_degrades_to_empty_string():
    s = _session()
    d = date(2026, 11, 1)
    _mk_with_rationale(s, "nfl", 1, 1, 2, d, '{"code":"rating_gap"}')
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].picks[0].rationale == ""


def test_rationale_json_wellformed_list_still_renders():
    s = _session()
    d = date(2026, 11, 1)
    _mk_with_rationale(
        s, "nfl", 1, 1, 2, d,
        '[{"code": "rating_gap", "side": "home", "strength": "moderate"}]',
    )
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].picks[0].rationale != ""


def _dup_setup(session, d, pick_type, pick_value, prop_market=None):
    session.add_all([
        Team(id=1, name="H1", abbreviation="H1", sport="nfl"),
        Team(id=2, name="A1", abbreviation="A1", sport="nfl"),
    ])
    session.flush()
    session.add(Game(id=1, sport="nfl", season="2026", date=d,
                     home_team_id=1, away_team_id=2, status="scheduled"))
    session.flush()
    # _run_window regenerates picks for ALL of today's games once per window,
    # and generate_and_store_picks inserts unconditionally -- so the same
    # pick lands once per window with a later created_at each time.
    session.add(PickModel(game_id=1, strategy_id=1, pick_type=pick_type,
                          pick_value=pick_value, confidence=4, edge_pct=6.0,
                          odds_at_pick=-110, prop_market=prop_market,
                          created_at=datetime(2026, 11, 1, 13, 0)))
    session.add(PickModel(game_id=1, strategy_id=1, pick_type=pick_type,
                          pick_value=pick_value, confidence=4, edge_pct=9.9,
                          odds_at_pick=-125, prop_market=prop_market,
                          created_at=datetime(2026, 11, 1, 18, 0)))
    session.commit()


def test_repeated_game_pick_appears_once_and_is_the_newest():
    s = _session()
    d = date(2026, 11, 1)
    _dup_setup(s, d, "moneyline", "HOME ML")
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    picks = sections[0].picks
    assert len(picks) == 1, f"three windows must not yield {len(picks)} identical rows"
    assert picks[0].edge_pct == 9.9, "must keep the most recent created_at"
    assert picks[0].odds == -125


def test_repeated_prop_appears_once_and_is_the_newest():
    s = _session()
    d = date(2026, 11, 1)
    _dup_setup(s, d, "prop", "Mahomes Over 275.5 Pass Yards", prop_market="player_pass_yds")
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    props = sections[0].props
    assert len(props) == 1
    assert props[0].edge_pct == 9.9
    assert props[0].odds == -125


def test_dedup_keys_on_pick_value_not_just_the_game():
    """Different picks on the same game must both survive."""
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0), (4, 8.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert len(sections[0].picks) == 2


def test_dedup_tolerates_a_missing_or_naive_created_at():
    """picks.created_at is NOT NULL in the current schema, but the column was
    added by migration and rows can come back naive or aware depending on how
    they were written. _dedupe_latest must not raise on either."""
    from backend.digest.selector import _dedupe_latest

    old = PickModel(id=1, game_id=1, strategy_id=1, pick_type="moneyline",
                    pick_value="HOME ML", confidence=4, edge_pct=1.0,
                    created_at=None)
    naive = PickModel(id=2, game_id=1, strategy_id=1, pick_type="moneyline",
                      pick_value="HOME ML", confidence=4, edge_pct=6.0,
                      created_at=datetime(2026, 11, 1, 13, 0))
    aware = PickModel(id=3, game_id=1, strategy_id=1, pick_type="moneyline",
                      pick_value="HOME ML", confidence=4, edge_pct=9.9,
                      created_at=datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc))

    kept = _dedupe_latest([old, naive, aware])
    assert len(kept) == 1
    assert kept[0].edge_pct == 9.9, "the newest row wins; a NULL never does"


# --- the send bar: price window, caps, raw-edge ranking ---------------------
# The shrunk-edge gate is gone (owner decision 2026-09-30): every priced
# game pick is shown, ranked by raw edge_pct. blend_weight is recorded only.

def test_price_minus_150_is_in_and_minus_151_is_out():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 10.0, -150, 0.55), (3, 10.0, -151, 0.55)])
    sections = select_digest(
        s, d, ["nfl"], SEASONS,
        send_bar=_bar(min_odds=-150, max_odds=150))
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0"]


def test_price_plus_150_is_in_and_plus_151_is_out():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 10.0, 150, 0.45), (3, 10.0, 151, 0.45)])
    sections = select_digest(
        s, d, ["nfl"], SEASONS,
        send_bar=_bar(min_odds=-150, max_odds=150))
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0"]


def test_a_pick_with_no_price_is_dropped():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 10.0, None, 0.55)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].picks == []
    assert sections[0].diagnostics.survived_price == 0


def test_a_negative_edge_still_survives_and_is_shown():
    """There is no edge floor any more -- a priced pick with a negative edge
    is shown too, not silently dropped."""
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, -20.0, -110, 0.3)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0"]
    assert sections[0].picks[0].edge_pct == -20.0


def test_the_fourth_game_pick_is_cut():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 40.0, -110, 0.7), (3, 30.0, -110, 0.65),
               (3, 20.0, -110, 0.6), (3, 10.0, -110, 0.55)])
    sections = select_digest(
        s, d, ["nfl"], SEASONS,
        send_bar=_bar(max_game_picks=3))
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0", "P1-1", "P1-2"]


def test_a_sport_missing_from_blend_weight_still_shows_its_picks():
    """blend_weight no longer gates anything -- a sport missing from it
    still gets its picks, ranked by edge like any other sport. Only
    diagnostics.lambda_used reflects the missing measurement."""
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 100.0, -110, 0.9)])
    sections = select_digest(
        s, d, ["nfl"], SEASONS,
        send_bar=_bar(blend_weight={"mlb": 1.0}))
    assert [p.pick_value for p in sections[0].picks] == ["P1-0"]
    assert sections[0].diagnostics.lambda_used == 0.0
    assert sections[0].diagnostics.lambda_measured is False


def test_ranked_by_raw_edge_descending():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 4.0, -110, 0.9), (3, 18.0, -110, 0.4), (3, 9.0, -110, 0.55)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-1", "P1-2", "P1-0"], (
        "ranking must follow raw edge, not model probability"
    )


def test_ties_break_by_start_time_then_id():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 10.0, -110, 0.6)],
              start_time=datetime(2026, 11, 1, 20, 0))
    _mk_priced(s, "nfl", 2, 3, 4, d, [(3, 10.0, -110, 0.6)],
              start_time=datetime(2026, 11, 1, 13, 0))
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P2-0", "P1-0"], "the earlier start time must sort first"


def test_a_leftover_min_shrunk_edge_pp_key_is_ignored_not_honoured(caplog):
    """A stale config carrying the removed key must not silently change
    behaviour -- it is ignored (with a WARNING), not re-honoured as a gate."""
    import logging
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, -20.0, -110, 0.3)])
    caplog.set_level(logging.WARNING)
    sections = select_digest(
        s, d, ["nfl"], SEASONS,
        send_bar=_bar(min_shrunk_edge_pp=50.0))  # would reject everything if honoured
    assert [p.pick_value for p in sections[0].picks] == ["P1-0"]
    assert "min_shrunk_edge_pp" in caplog.text


# --- props: gradeable markets, same price window, no edge gate -------------

def test_an_ungradeable_prop_market_is_dropped():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 40.0, -110, 0.9)], pick_type="prop")
    s.query(PickModel).filter_by(pick_value="P1-0").update({"prop_market": "batter_hits"})
    s.commit()
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].props == []


def test_a_gradeable_prop_market_survives():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 40.0, -110, 0.9)], pick_type="prop")
    s.query(PickModel).filter_by(pick_value="P1-0").update({"prop_market": "player_pass_yds"})
    s.commit()
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert [p.pick_value for p in sections[0].props] == ["P1-0"]


def test_props_apply_the_same_price_window():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 10.0, -200, 0.7), (3, 10.0, -110, 0.55)], pick_type="prop")
    for pv in ("P1-0", "P1-1"):
        s.query(PickModel).filter_by(pick_value=pv).update({"prop_market": "player_pass_yds"})
    s.commit()
    sections = select_digest(
        s, d, ["nfl"], SEASONS,
        send_bar=_bar(min_odds=-150, max_odds=150))
    assert [p.pick_value for p in sections[0].props] == ["P1-1"]


def test_a_prop_with_no_price_is_dropped():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 10.0, None, 0.55)], pick_type="prop")
    s.query(PickModel).filter_by(pick_value="P1-0").update({"prop_market": "player_pass_yds"})
    s.commit()
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].props == []


def test_props_are_not_edge_gated():
    """A prop with a tiny edge still survives -- there is no edge gate
    on props (and none on game picks any more either), only the price
    window and market gradeability."""
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 0.1, -110, 0.51)], pick_type="prop")
    s.query(PickModel).filter_by(pick_value="P1-0").update({"prop_market": "player_pass_yds"})
    s.commit()
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert [p.pick_value for p in sections[0].props] == ["P1-0"]


def test_the_sixth_prop_is_cut():
    s = _session()
    d = date(2026, 11, 1)
    picks = [(3, 5.0, -110, 0.55)] * 6
    _mk_priced(s, "nfl", 1, 1, 2, d, picks, pick_type="prop")
    for i in range(6):
        s.query(PickModel).filter_by(pick_value=f"P1-{i}").update(
            {"prop_market": "player_pass_yds"})
    s.commit()
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=_bar(max_props=5))
    assert len(sections[0].props) == 5


def test_props_sort_by_start_time_then_id():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 5.0, -110, 0.55)], pick_type="prop",
              start_time=datetime(2026, 11, 1, 20, 0))
    _mk_priced(s, "nfl", 2, 3, 4, d, [(3, 5.0, -110, 0.55)], pick_type="prop",
              start_time=datetime(2026, 11, 1, 13, 0))
    for pv in ("P1-0", "P2-0"):
        s.query(PickModel).filter_by(pick_value=pv).update({"prop_market": "player_pass_yds"})
    s.commit()
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert [p.pick_value for p in sections[0].props] == ["P2-0", "P1-0"]


# --- diagnostics -------------------------------------------------------------

def test_diagnostics_report_generated_and_survived_and_lambda():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 10.0, -110, 0.6), (3, 10.0, 500, 0.6)])  # second fails price window
    sections = select_digest(
        s, d, ["nfl"], SEASONS,
        send_bar=_bar(min_odds=-150, max_odds=150, blend_weight={"nfl": 0.5}))
    diag = sections[0].diagnostics
    assert diag.games == 1
    assert diag.generated == 2
    assert diag.survived_price == 1
    assert diag.lambda_used == 0.5


# --- price ceiling props: the old ceiling-agnostic-props test --------------

def test_ceiling_does_not_touch_game_picks_of_a_different_sport():
    """Sanity: the price window is per-call, not global state leaking across
    sports in the same select_digest call."""
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 10.0, -110, 0.6)])
    _mk_priced(s, "nba", 2, 3, 4, d, [(3, 10.0, -110, 0.6)])
    sections = select_digest(
        s, d, ["nfl", "nba"], SEASONS,
        send_bar=_bar(blend_weight={"nfl": 1.0, "nba": 1.0}))
    by_sport = {sec.sport: sec for sec in sections}
    assert len(by_sport["nfl"].picks) == 1
    assert len(by_sport["nba"].picks) == 1


# --- trailing record / suppression (unaffected by the send bar) ------------

def _grade(session, pick_id, result):
    session.add(PickResult(pick_id=pick_id, result=result, payout=0.91 if result == "win" else 0.0))
    session.commit()


def _pick_ids(session, gid):
    return [p.id for p in
            session.query(PickModel).filter(PickModel.game_id == gid).order_by(PickModel.id).all()]


def test_digest_pick_carries_model_and_price_probability():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 4.0, -150, 0.62)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    picks = sections[0].picks
    assert picks[0].model_prob == 0.62
    assert abs(picks[0].price_prob - 0.6) < 1e-9


def test_trailing_record_counts_decided_game_picks_only():
    s = _session()
    d = date(2026, 11, 1)
    # Live pick for the section itself, on the target date -- never graded.
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    # Game 10 days before d: three graded moneyline picks and one graded prop.
    d_recent = d - timedelta(days=10)
    _mk_priced(s, "nfl", 2, 3, 4, d_recent,
              [(5, 9.0, -110, 0.6), (4, 8.0, -110, 0.55), (3, 5.0, -110, 0.52)])
    s.add(PickModel(game_id=2, strategy_id=1, pick_type="prop",
                    pick_value="Prop", confidence=4, edge_pct=10.0, odds_at_pick=-110))
    s.commit()
    ids = _pick_ids(s, 2)
    _grade(s, ids[0], "win")
    _grade(s, ids[1], "loss")
    _grade(s, ids[2], "push")
    _grade(s, ids[3], "win")  # the prop -- must not be counted
    # Game 40 days before d: one graded win, outside the trailing window.
    d_old = d - timedelta(days=40)
    _mk_priced(s, "nfl", 3, 5, 6, d_old, [(5, 9.0, -110, 0.6)])
    _grade(s, _pick_ids(s, 3)[0], "win")

    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].record == (1, 1)


def test_no_graded_history_gives_no_record():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].record is None


def test_trailing_record_excludes_today():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    _mk_priced(s, "nfl", 2, 3, 4, d, [(5, 9.0, -110, 0.6)])
    _grade(s, _pick_ids(s, 2)[0], "win")

    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert sections[0].record is None


def test_suppression_is_off_by_default():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    d_recent = d - timedelta(days=5)
    _mk_priced(s, "nfl", 2, 3, 4, d_recent,
              [(5, 9.0, -110, 0.6)] * 25)
    for pid in _pick_ids(s, 2):
        _grade(s, pid, "loss")

    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR)
    assert len(sections) == 1


def test_suppression_needs_the_minimum_sample():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    d_recent = d - timedelta(days=5)
    _mk_priced(s, "nfl", 2, 3, 4, d_recent, [(5, 9.0, -110, 0.6)] * 5)
    for pid in _pick_ids(s, 2):
        _grade(s, pid, "loss")

    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR, min_trailing_win_pct=0.5)
    assert len(sections) == 1


def test_suppression_fires_below_the_floor():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    d_recent = d - timedelta(days=5)
    _mk_priced(s, "nfl", 2, 3, 4, d_recent, [(5, 9.0, -110, 0.6)] * 20)
    ids = _pick_ids(s, 2)
    for pid in ids[:15]:
        _grade(s, pid, "loss")
    for pid in ids[15:]:
        _grade(s, pid, "win")

    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=OPEN_BAR, min_trailing_win_pct=0.5)
    assert sections == []


# --- the send bar fails closed, not open ------------------------------------

def test_the_real_config_send_bar_passes_zero_game_picks_at_lambda_zero():
    """Loads config.yaml's actual digest.send_bar through backend.config's
    real loader -- the same one backend/digest/job.py uses. Since the
    shrunk-edge gate was removed 2026-09-30, lambda=0.00 (recorded, not
    gating) no longer excludes anything: a slate with a +20, a +5 and a -20
    edge_pct produces all three picks, ranked by raw edge descending."""
    real_send_bar = load_config(REAL_CONFIG_PATH)["digest"]["send_bar"]
    assert real_send_bar["blend_weight"].get("nfl") == 0.0
    assert "min_shrunk_edge_pp" not in real_send_bar

    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
              [(3, 20.0, -110, 0.6), (3, 5.0, -110, 0.55), (3, -20.0, -110, 0.3)])
    sections = select_digest(s, d, ["nfl"], SEASONS, send_bar=real_send_bar)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0", "P1-1", "P1-2"]
    edges = [p.edge_pct for p in sections[0].picks]
    assert edges == [20.0, 5.0, -20.0]


@pytest.mark.parametrize("key", ["min_odds", "max_odds",
                                 "max_game_picks", "max_props"])
def test_each_required_send_bar_key_is_required(key):
    broken_bar = {k: v for k, v in OPEN_BAR.items() if k != key}
    with pytest.raises(ValueError, match=f"digest.send_bar.{key} is required"):
        select_digest(_session(), date(2026, 11, 1), ["nfl"], SEASONS, send_bar=broken_bar)


@pytest.mark.parametrize("bad_value", [None, "150", True, [150]])
def test_a_non_numeric_send_bar_value_raises_valueerror_not_typeerror(bad_value):
    broken_bar = {**OPEN_BAR, "max_odds": bad_value}
    with pytest.raises(ValueError, match="digest.send_bar.max_odds is required"):
        select_digest(_session(), date(2026, 11, 1), ["nfl"], SEASONS, send_bar=broken_bar)
