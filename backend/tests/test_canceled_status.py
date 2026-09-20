"""Guards against two spellings of the same game status.

ESPN's collector mapped STATUS_CANCELED to "cancelled"; reconciliation in
full_pipeline wrote and read "canceled", and `catch_up_finals` counts the
latter. The two never met because no row had ever carried either value, so
the divergence sat latent -- until something started writing cancellations,
at which point a game ESPN reported as cancelled would be invisible to the
restore path that un-cancels a rescheduled game, and uncounted by the
catch-up script's report.

One constant, used by both writers.
"""
from backend.collectors.espn import CANCELED, STATUS_MAP


def test_espn_maps_a_cancellation_to_the_shared_constant():
    assert STATUS_MAP["STATUS_CANCELED"] == CANCELED


def test_no_code_writes_the_other_spelling():
    """The concept has one name. A second spelling is a silent partition:
    every filter picks one and misses the other.

    Matches the string literal, not the word, so a comment may still discuss
    the spelling this replaced.
    """
    import pathlib
    offenders = []
    for path in pathlib.Path("backend").rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if '"cancelled"' in text or "'cancelled'" in text:
            offenders.append(str(path))
    assert offenders == [], f"literal 'cancelled' found in: {offenders}"


def test_the_reconciliation_path_uses_the_same_value():
    """full_pipeline decides both to cancel and to un-cancel on this value;
    if it drifted from the collector's, a rescheduled game ESPN had reported
    cancelled would never be restored."""
    import pathlib
    text = pathlib.Path("backend/pipeline/full_pipeline.py").read_text(
        encoding="utf-8")
    assert 'CANCELED' in text, "reconciliation should use the shared constant"
