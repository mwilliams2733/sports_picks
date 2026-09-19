from backend.analysis.strategy import consensus_moneyline


def test_probability_space_average_of_straddling_books():
    # -150 -> p 0.600, +140 -> p 0.4167; mean 0.5083 -> -103
    assert consensus_moneyline([-150, 140]) == -103


def test_arithmetic_mean_would_be_invalid_here():
    # The same inputs averaged arithmetically give -5, not a price at all.
    assert not (-100 < consensus_moneyline([-150, 140]) < 100)


def test_divisor_is_the_number_of_prices_not_the_caller_list_length():
    # The production defect: books with no moneyline diluted the mean toward
    # zero. None entries must be dropped, not counted.
    assert consensus_moneyline([-142, -137, -150, -145, None, None, None, None]) == -143


def test_single_book_round_trips():
    assert consensus_moneyline([-110]) == -110


def test_no_usable_prices_returns_none():
    assert consensus_moneyline([]) is None
    assert consensus_moneyline([None, None]) is None


def test_invalid_book_prices_are_skipped():
    assert consensus_moneyline([-110, 50]) == -110
