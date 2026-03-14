def american_to_implied_prob(odds: int) -> float:
    if odds < 0: return abs(odds) / (abs(odds) + 100)
    else: return 100 / (odds + 100)

def calculate_payout(odds: int) -> float:
    if odds < 0: return 100 / abs(odds)
    else: return odds / 100
