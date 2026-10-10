from datetime import date

from backend.collectors.wikipedia_boxing import WikiClient
from backend.models import Base, Game, PickModel, StrategyModel, Team
from backend.scripts.import_boxing_history import collect_bouts, fighters_to_fetch
from backend.scripts.import_ufc_history import coverage, import_bouts

HEAD = """==Professional boxing record==
{| class="wikitable"
|-
!No.
!Result
!Opponent
!Date
"""


def page(rows):
    body = "".join(f"|-\n|{n}\n|{res}\n|[[{opp}]]\n|{d}\n" for n, res, opp, d in rows)
    return HEAD + body + "|}\n"


class Fake:
    def __init__(self, pages):
        self.pages = pages

    def __call__(self, params):
        if params.get("action") == "parse":
            wt = self.pages.get(params["page"])
            return {"parse": {"wikitext": wt}} if wt else {"error": {}}
        return {"query": {"search": []}}


PAGES = {
    "Ann Able (boxer)": page([(2, "{{yes2}}Win", "Bea Bold", "1 Jun 2026"),
                              (1, "{{yes2}}Win", "Cat Cole", "1 Jan 2025")]),
    "Bea Bold": page([(2, "{{no2}}Loss", "Ann Able (boxer)|Ann Able", "2 Jun 2026"),   # same bout, a day off
                      (1, "{{yes2}}Win", "Dee Dunn", "1 Mar 2025")]),
    "Cat Cole": page([(1, "{{no2}}Loss", "Ann Able (boxer)|Ann Able", "1 Jan 2025")]),
}


def _db(session):
    Base.metadata.create_all(session.get_bind())
    for tid, name in [(1, "Ann Able"), (2, "Bea Bold")]:
        session.add(Team(id=tid, name=name, abbreviation=name, sport="boxing"))
    session.add(StrategyModel(id=1, name="x", config_json="{}"))
    session.flush()
    session.add(Game(id=10, sport="boxing", season="2026", date=date(2026, 6, 1), status="canceled",
                     home_team_id=1, away_team_id=2))
    session.add(Game(id=11, sport="boxing", season="2026", date=date(2026, 11, 7), status="scheduled",
                     home_team_id=2, away_team_id=1))
    session.commit()


def _bouts(session, tmp_path):
    client = WikiClient(tmp_path, getter=Fake(PAGES), sleep=lambda s: None)
    return collect_bouts(client, fighters_to_fetch(session, date(2026, 1, 1)), hops=1)


def test_one_hop_collects_both_sides_and_the_opponents_records(db_engine, db_session, tmp_path):
    _db(db_session)
    assert sorted(fighters_to_fetch(db_session, since=date(2026, 1, 1))) == ["Ann Able", "Bea Bold"]
    bouts, stats = _bouts(db_session, tmp_path)
    assert stats["pages_found"] == 2 and stats["pages_missing"] == [] and stats["opponent_pages"] == 1
    assert len(bouts) == 5          # Ann x2, Bea x2, Cat x1 (duplicates collapse at import)


def test_the_same_bout_on_two_pages_is_one_game_and_the_stuck_one_is_finalized(db_engine, db_session, tmp_path):   # Review Focus 3, 4
    _db(db_session)
    bouts, _ = _bouts(db_session, tmp_path)
    summary = import_bouts(db_session, bouts, sport="boxing", finalize_unfinished=True)
    stuck = db_session.get(Game, 10)
    assert (stuck.status, stuck.home_score, stuck.away_score) == ("final", 1, 0)     # Ann (home) won
    assert summary["finalized"] == [10]
    assert db_session.query(Game).filter(Game.sport == "boxing", Game.status == "final").count() == 3
    assert coverage(db_session, date(2026, 10, 10), days=60, sport="boxing")["games_both_known"] == 1


def test_a_stuck_game_with_a_pick_is_reported_not_finalized(db_engine, db_session, tmp_path):   # Review Focus 4
    _db(db_session)
    db_session.add(PickModel(game_id=10, strategy_id=1, pick_type="moneyline", pick_value="HOME ML",
                             confidence=3, edge_pct=5.0, odds_at_pick=100))
    db_session.commit()
    bouts, _ = _bouts(db_session, tmp_path)
    summary = import_bouts(db_session, bouts, sport="boxing", finalize_unfinished=True)
    assert db_session.get(Game, 10).status == "canceled" and summary["matched_non_final"] == [10]


def test_boxing_keeps_jr_and_sr_apart_and_the_feed_matches_the_same_way(db_engine, db_session):
    # 2026-10-10 live fetch: Floyd Mayweather Jr.'s record was filed under
    # Floyd Mayweather Sr. -- name_key drops generational suffixes (fine for
    # the UFC, wrong for boxing's fathers and sons).
    from backend.collectors.ufcstats_history import HistoricalBout
    from backend.pipeline.full_pipeline import _fighter_by_name_key
    Base.metadata.create_all(db_session.get_bind())
    ev = "wikipedia:x"
    import_bouts(db_session, [HistoricalBout(date(1978, 9, 9), ev, "Floyd Mayweather Sr.", "Sugar Ray Leonard", 0, 1),
                              HistoricalBout(date(2017, 8, 26), ev, "Floyd Mayweather Jr.", "Conor McGregor", 1, 0)],
                 sport="boxing")
    names = {t.name for t in db_session.query(Team).filter(Team.sport == "boxing")}
    assert {"Floyd Mayweather Sr.", "Floyd Mayweather Jr."} <= names
    assert _fighter_by_name_key(db_session, "boxing", "Floyd Mayweather Jr").name == "Floyd Mayweather Jr."
    # MMA is unchanged: the feed's "Jon Jones" is the history's "Jon Jones Jr."
    import_bouts(db_session, [HistoricalBout(date(2020, 1, 1), ev, "Jon Jones Jr.", "Al Bee", 1, 0)], sport="mma")
    assert _fighter_by_name_key(db_session, "mma", "Jon Jones").name == "Jon Jones Jr."
