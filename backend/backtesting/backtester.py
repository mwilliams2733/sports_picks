from backend.analysis.strategy import Strategy
from backend.analysis.odds_utils import calculate_payout
from backend.data_types import GameData

class Backtester:
    def __init__(self, strategy: Strategy):
        self.strategy = strategy

    def run(self, games_with_results: list[tuple[GameData, int, int]]) -> dict:
        wins = 0
        losses = 0
        pushes = 0
        total_profit = 0.0
        pick_details = []
        for game, home_score, away_score in games_with_results:
            picks = self.strategy.predict(game)
            for pick in picks:
                result = self._grade_pick(pick, home_score, away_score)
                if result == "win":
                    wins += 1
                    total_profit += calculate_payout(pick.odds_at_pick)
                elif result == "loss":
                    losses += 1
                    total_profit -= 1.0
                else:
                    pushes += 1
                pick_details.append({
                    "game_id": pick.game_id, "pick_type": pick.pick_type,
                    "pick_value": pick.pick_value, "confidence": pick.confidence,
                    "edge_pct": pick.edge_pct, "result": result, "odds_at_pick": pick.odds_at_pick,
                })
        total = wins + losses
        return {
            "wins": wins, "losses": losses, "pushes": pushes, "total": total,
            "win_rate": round((wins / total * 100) if total > 0 else 0, 2),
            "roi": round((total_profit / (total if total > 0 else 1)) * 100, 2),
            "total_profit": round(total_profit, 4), "picks": pick_details,
        }

    def _grade_pick(self, pick, home_score: int, away_score: int) -> str:
        if pick.pick_type == "moneyline":
            if "HOME" in pick.pick_value:
                return "win" if home_score > away_score else "loss"
            else:
                return "win" if away_score > home_score else "loss"
        return "loss"
