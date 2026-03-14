from backend.analysis.strategy import Strategy
from backend.pipeline.grader import grade_pick
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
                result, payout = grade_pick(
                    pick.pick_type, pick.pick_value, home_score, away_score, pick.odds_at_pick
                )
                if result == "win":
                    wins += 1
                    total_profit += payout
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
