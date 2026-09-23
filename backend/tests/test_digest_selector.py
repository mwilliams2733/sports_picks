from datetime import date, datetime, timedelta, timezone


from backend.database import get_engine, get_session
from backend.models import Base, Team, Game, StrategyModel, PickModel, PickResult
from backend.digest.selector import select_digest

SEASONS = {"nfl": {"start": "09-05", "end": "02-10"},
           "nba": {"start": "10-22", "end": "06-20"}}


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


def _mk_priced(session, sport, gid, tid_h, tid_a, d, picks, pick_type="moneyline"):
    """picks: list of (confidence, edge, odds, model_prob)."""
    session.add_all([
        Team(id=tid_h, name=f"H{gid}", abbreviation=f"H{gid}", sport=sport),
        Team(id=tid_a, name=f"A{gid}", abbreviation=f"A{gid}", sport=sport),
    ])
    session.flush()
    session.add(Game(id=gid, sport=sport, season="2026", date=d,
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


def test_ranks_by_confidence_then_edge():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(3, 9.0), (5, 4.0), (4, 8.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-1", "P1-2", "P1-0"]


def test_caps_at_max_per_sport_and_never_pads():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0), (5, 8.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS, max_per_sport=5)
    assert len(sections[0].picks) == 2, "must not pad to five"


def test_sport_with_no_games_is_omitted():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl", "nba"], SEASONS)
    assert [sec.sport for sec in sections] == ["nfl"]


def test_out_of_season_sport_is_omitted():
    s = _session()
    d = date(2026, 7, 1)  # NFL out of season
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections == []


def test_zero_confidence_picks_excluded():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(0, 20.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections == []


def test_matchup_reads_away_at_home():
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].picks[0].matchup == "A1 @ H1"


def test_prop_picks_never_rank_against_game_picks():
    # prop_pipeline writes analyzed props into the SAME picks table with
    # pick_type="prop". A prop's edge_pct is (prob - 0.5) * 200 and ignores
    # the prop's price, so it is not comparable to a game pick's de-vigged
    # edge. A juiced prop with a huge nominal edge must not appear in — let
    # alone top — the game-pick list.
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(3, 4.0)])
    s.add(PickModel(game_id=1, strategy_id=1, pick_type="prop",
                    pick_value="Mahomes Over 275.5 Pass Yards",
                    confidence=5, edge_pct=40.0, odds_at_pick=-300))
    s.commit()

    sections = select_digest(s, d, ["nfl"], SEASONS)
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
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].picks[0].rationale == ""


def test_rationale_json_number_degrades_to_empty_string():
    s = _session()
    d = date(2026, 11, 1)
    _mk_with_rationale(s, "nfl", 1, 1, 2, d, "5")
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].picks[0].rationale == ""


def test_rationale_json_dict_degrades_to_empty_string():
    s = _session()
    d = date(2026, 11, 1)
    _mk_with_rationale(s, "nfl", 1, 1, 2, d, '{"code":"rating_gap"}')
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].picks[0].rationale == ""


def test_rationale_json_wellformed_list_still_renders():
    s = _session()
    d = date(2026, 11, 1)
    _mk_with_rationale(
        s, "nfl", 1, 1, 2, d,
        '[{"code": "rating_gap", "side": "home", "strength": "moderate"}]',
    )
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].picks[0].rationale != ""


def _dup_setup(session, d, pick_type, pick_value):
    session.add_all([
        Team(id=1, name="H1", abbreviation="H1", sport="nfl"),
        Team(id=2, name="A1", abbreviation="A1", sport="nfl"),
    ])
    session.flush()
    session.add(Game(id=1, sport="nfl", season="2026", date=d,
                     home_team_id=1, away_team_id=2, status="scheduled"))
    session.flush()
    # _run_window regenerates picks for ALL of today's games once per window,
    # and generate_and_store_picks inserts unconditionally — so the same pick
    # lands once per window with a later created_at each time.
    session.add(PickModel(game_id=1, strategy_id=1, pick_type=pick_type,
                          pick_value=pick_value, confidence=4, edge_pct=6.0,
                          odds_at_pick=-110,
                          created_at=datetime(2026, 11, 1, 13, 0)))
    session.add(PickModel(game_id=1, strategy_id=1, pick_type=pick_type,
                          pick_value=pick_value, confidence=4, edge_pct=9.9,
                          odds_at_pick=-125,
                          created_at=datetime(2026, 11, 1, 18, 0)))
    session.commit()


def test_repeated_game_pick_appears_once_and_is_the_newest():
    s = _session()
    d = date(2026, 11, 1)
    _dup_setup(s, d, "moneyline", "HOME ML")
    sections = select_digest(s, d, ["nfl"], SEASONS)
    picks = sections[0].picks
    assert len(picks) == 1, f"three windows must not yield {len(picks)} identical rows"
    assert picks[0].edge_pct == 9.9, "must keep the most recent created_at"
    assert picks[0].odds == -125


def test_repeated_prop_appears_once_and_is_the_newest():
    s = _session()
    d = date(2026, 11, 1)
    _dup_setup(s, d, "prop", "Mahomes Over 275.5 Pass Yards")
    sections = select_digest(s, d, ["nfl"], SEASONS)
    props = sections[0].props
    assert len(props) == 1
    assert props[0].edge_pct == 9.9
    assert props[0].odds == -125


def test_dedup_keys_on_pick_value_not_just_the_game():
    """Different picks on the same game must both survive."""
    s = _session()
    d = date(2026, 11, 1)
    _mk(s, "nfl", 1, 1, 2, d, [(5, 9.0), (4, 8.0)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
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


def test_ranks_by_model_probability_over_confidence():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
               [(5, 18.0, -110, 0.40),
                (3, 4.0, -110, 0.70),
                (4, 9.0, -110, 0.55)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-1", "P1-2", "P1-0"]


def test_pick_without_probability_sorts_last():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
               [(5, 9.0, -110, None),
                (2, 3.0, -110, 0.52)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-1", "P1-0"]


def test_longshot_past_the_ceiling_is_not_emailed():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
               [(5, 18.0, 248, 0.34),
                (3, 4.0, -130, 0.60)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-1"]


def test_price_at_the_ceiling_is_kept():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
               [(3, 4.0, 150, 0.45)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0"]


def test_missing_price_is_kept():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
               [(3, 4.0, None, 0.55)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0"]


def test_ceiling_can_be_disabled():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
               [(5, 18.0, 800, 0.20)])
    sections = select_digest(s, d, ["nfl"], SEASONS, max_odds=None)
    vals = [p.pick_value for p in sections[0].picks]
    assert vals == ["P1-0"]


def test_ceiling_does_not_touch_props():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d,
               [(3, 10.0, 300, 0.6)], pick_type="prop")
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert len(sections[0].props) == 1
    assert sections[0].picks == []


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
    sections = select_digest(s, d, ["nfl"], SEASONS)
    picks = sections[0].picks
    assert picks[0].model_prob == 0.62
    assert abs(picks[0].price_prob - 0.6) < 1e-9


def test_missing_price_gives_no_price_probability():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(3, 4.0, None, 0.55)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].picks[0].price_prob is None


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

    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].record == (1, 1)


def test_no_graded_history_gives_no_record():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert sections[0].record is None


def test_trailing_record_excludes_today():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    _mk_priced(s, "nfl", 2, 3, 4, d, [(5, 9.0, -110, 0.6)])
    _grade(s, _pick_ids(s, 2)[0], "win")

    sections = select_digest(s, d, ["nfl"], SEASONS)
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

    sections = select_digest(s, d, ["nfl"], SEASONS)
    assert len(sections) == 1


def test_suppression_needs_the_minimum_sample():
    s = _session()
    d = date(2026, 11, 1)
    _mk_priced(s, "nfl", 1, 1, 2, d, [(5, 9.0, -110, 0.6)])
    d_recent = d - timedelta(days=5)
    _mk_priced(s, "nfl", 2, 3, 4, d_recent, [(5, 9.0, -110, 0.6)] * 5)
    for pid in _pick_ids(s, 2):
        _grade(s, pid, "loss")

    sections = select_digest(s, d, ["nfl"], SEASONS, min_trailing_win_pct=0.5)
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

    sections = select_digest(s, d, ["nfl"], SEASONS, min_trailing_win_pct=0.5)
    assert sections == []
