"""The feature row must carry sport, with a stable vocabulary."""

from backend.analysis.calibrated_model import SPORT_VOCAB, build_feature_row


def _row(sport):
    return build_feature_row(1.0, 2.0, 3.0, 4.0, 5.0, sport)


def test_legacy_features_come_first_and_unchanged():
    row = _row("nba")
    assert row[:5] == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_row_length_is_five_plus_the_vocabulary():
    assert len(_row("nba")) == 5 + len(SPORT_VOCAB)


def test_exactly_one_sport_slot_is_set():
    row = _row("ncaab")
    assert sum(row[5:]) == 1.0
    assert row[5 + SPORT_VOCAB.index("ncaab")] == 1.0


def test_an_unknown_sport_sets_no_slot_rather_than_raising():
    """Prediction must not crash on a sport the vocabulary omits.

    All-zero sport slots fall back to the shared intercept, which is exactly
    the old behaviour -- a safe degradation rather than an exception in the
    middle of pick generation.
    """
    row = _row("curling")
    assert sum(row[5:]) == 0.0
    assert len(row) == 5 + len(SPORT_VOCAB)


def test_vocabulary_is_fixed_not_derived_from_data():
    """Feature positions must not move when the database changes.

    A vocabulary built from whatever sports happen to be in the training set
    would silently reassign column meanings between one training run and the
    next.
    """
    assert isinstance(SPORT_VOCAB, tuple)
    assert "ncaab" in SPORT_VOCAB and "nba" in SPORT_VOCAB
    assert SPORT_VOCAB == tuple(sorted(SPORT_VOCAB)), "order must be deterministic"


def test_two_sports_produce_different_rows():
    assert _row("nba") != _row("ncaab")
