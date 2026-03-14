def calculate_confidence(edge_pct: float, models_agreeing: int) -> int:
    if edge_pct >= 12.0 and models_agreeing >= 3: return 5
    if edge_pct >= 8.0 and models_agreeing >= 2: return 4
    if edge_pct >= 5.0 and models_agreeing >= 2: return 3
    if edge_pct >= 5.0 and models_agreeing >= 1: return 2
    if edge_pct >= 3.0: return 1
    return 0
