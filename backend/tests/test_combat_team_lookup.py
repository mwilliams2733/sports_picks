"""The odds feed finds a combat fighter the way the UFC history import does.

Final review, 2026-10-10: the feed matched fighters by exact name, the import
by name_key, so a fighter whose feed label differs ("Cong Wang" vs the
history's "Wang Cong", "Lupita" vs "Loopy") got a second, empty row -- no
history, no pick, and a later import double-counted the bout.
"""
from backend.models import Base, Team
from backend.pipeline.full_pipeline import _lookup_team, _resolve_team


def test_both_lookups_match_a_fighter_by_name_key(db_engine, db_session):
    Base.metadata.create_all(db_engine)
    db_session.add_all([Team(id=1, name="Wang Cong", abbreviation="Wang Cong", sport="mma"),
                        Team(id=2, name="Loopy Godinez", abbreviation="Loopy Godinez", sport="mma"),
                        Team(id=3, name="Cong Wang", abbreviation="Cong Wang", sport="nfl")])
    db_session.commit()
    for lookup in (_resolve_team, _lookup_team):
        assert lookup(db_session, "mma", "Cong Wang").id == 1
        assert lookup(db_session, "mma", "Lupita Godinez").id == 2
        assert lookup(db_session, "mma", "Nobody Known") is None
