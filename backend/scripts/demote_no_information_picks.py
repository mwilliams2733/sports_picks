"""One-off (owner, 2026-10-09): move published no-information picks on games
not yet started to tracking-only.

The pick generator now stores a pick at exactly model_prob 0.5 as
tracking-only (pick_generator.is_no_information); this applies the same rule
to the picks already published for games still to play, so they leave the
board and the email at once instead of at their next window refresh. Graded
history is left exactly as it was emailed.

    python -m backend.scripts.demote_no_information_picks            # dry run
    python -m backend.scripts.demote_no_information_picks --apply    # one transaction
"""
import sys
from datetime import datetime, timezone

from backend.config import load_config
from backend.database import get_engine, get_session
from backend.models import Game, PickModel
from backend.paper.pricing import open_for_betting
from backend.pipeline.pick_generator import NO_INFORMATION_PROB, is_no_information
from backend.pipeline.pick_versions import record_pick_version


def candidates(session, now: datetime) -> list[PickModel]:
    rows = (session.query(PickModel, Game).join(Game, Game.id == PickModel.game_id)
            .filter(PickModel.published(), PickModel.by_model(),
                    PickModel.pick_type != "prop",
                    PickModel.model_prob == NO_INFORMATION_PROB)
            .order_by(PickModel.id).all())
    return [p for p, g in rows if is_no_information(p.model_prob) and open_for_betting(g, now)]


def main(argv: list[str]) -> int:
    config = load_config("config.yaml")
    session = get_session(get_engine(config["database_path"]))
    try:
        picks = candidates(session, datetime.now(timezone.utc))
        for p in picks:
            print(f"pick {p.id} game {p.game_id} {p.pick_type} {p.pick_value} "
                  f"edge {p.edge_pct} model_prob {p.model_prob}")
        print(f"{len(picks)} pick(s) to move to tracking-only")
        if "--apply" in argv and picks:
            for p in picks:
                p.tracking_only = True
                record_pick_version(session, p, "no_information")
            session.commit()
            print("applied")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
