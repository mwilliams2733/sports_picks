def calculate_prop_confidence(edge_pct: float) -> int:
    """Prop-specific confidence tiers (no model agreement concept)."""
    if edge_pct >= 20.0: return 5
    if edge_pct >= 15.0: return 4
    if edge_pct >= 10.0: return 3
    if edge_pct >= 7.0: return 2
    if edge_pct >= 5.0: return 1
    return 0
