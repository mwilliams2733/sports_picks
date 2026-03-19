class BudgetExhaustedError(Exception):
    """Raised when the Odds API credit budget is exhausted."""
    def __init__(self, monthly_used: int, monthly_limit: int, daily_used: int):
        self.monthly_used = monthly_used
        self.monthly_limit = monthly_limit
        self.daily_used = daily_used
        super().__init__(
            f"Budget exhausted: {monthly_used}/{monthly_limit} monthly, {daily_used} today"
        )
