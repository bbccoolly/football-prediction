# models/dixon_coles.py - Dixon-Coles 修正泊松模型

import math
import numpy as np
from scipy.stats import poisson
from config import DIXON_COLES_RHO, DIXON_COLES_XI, POISSON_LEAGUE_AVG_GOALS


class DixonColesModel:
    """Dixon-Coles 模型：修正泊松对低比分平局的低估"""

    MAX_GOALS = 10

    def __init__(self, league: str = "default", rho: float = None):
        self.league_avg = POISSON_LEAGUE_AVG_GOALS.get(league, 2.70)
        self.rho = rho if rho is not None else DIXON_COLES_RHO
        self.xi = DIXON_COLES_XI
        self.attack_strengths = {}
        self.defense_strengths = {}

    def set_team_strengths(self, strengths: dict):
        for team, s in strengths.items():
            self.attack_strengths[team] = s["attack"]
            self.defense_strengths[team] = s["defense"]

    def _tau(self, x: int, y: int, rho: float) -> float:
        """Dixon-Coles 修正因子 τ"""
        if x == 0 and y == 0:
            return 1.0 - rho
        elif x == 0 and y == 1:
            return 1.0 + rho
        elif x == 1 and y == 0:
            return 1.0 + rho
        elif x == 1 and y == 1:
            return 1.0 - rho
        else:
            return 1.0

    def _expected_goals(self, team_attack: float, opp_defense: float,
                        home_factor: float = 1.0) -> float:
        return (self.league_avg / 2.0) * team_attack * opp_defense * home_factor

    def predict(self, home_team: str, away_team: str,
                neutral: bool = False, home_factor: float = 1.15) -> dict:
        atk_h = self.attack_strengths.get(home_team, 1.0)
        def_h = self.defense_strengths.get(home_team, 1.0)
        atk_a = self.attack_strengths.get(away_team, 1.0)
        def_a = self.defense_strengths.get(away_team, 1.0)

        hf = home_factor if not neutral else 1.0

        lambda_home = self._expected_goals(atk_h, def_a, hf)
        lambda_away = self._expected_goals(atk_a, def_h, 1.0 / hf)

        lambda_home = max(0.3, min(3.5, max(0.5, lambda_home)))
        lambda_away = max(0.3, min(3.5, max(0.5, lambda_away)))

        # 带 Dixon-Coles 修正的比分概率
        score_matrix = {}
        total_prob = 0

        for h in range(self.MAX_GOALS + 1):
            for a in range(self.MAX_GOALS + 1):
                p_poisson = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
                tau = self._tau(h, a, self.rho)
                p = p_poisson * tau
                score_matrix[f"{h}-{a}"] = p
                total_prob += p

        # 归一化
        for score in score_matrix:
            score_matrix[score] /= total_prob

        prob_home_win = sum(p for s, p in score_matrix.items()
                           if int(s.split("-")[0]) > int(s.split("-")[1]))
        prob_draw = sum(p for s, p in score_matrix.items()
                       if int(s.split("-")[0]) == int(s.split("-")[1]))
        prob_away_win = sum(p for s, p in score_matrix.items()
                           if int(s.split("-")[0]) < int(s.split("-")[1]))

        top_scores = sorted(score_matrix.items(), key=lambda x: x[1], reverse=True)[:5]

        # 总进球分布
        total_goals_dist = {}
        for total in range(self.MAX_GOALS * 2 + 1):
            total_goals_dist[total] = sum(
                p for score, p in score_matrix.items()
                if sum(map(int, score.split("-"))) == total
            )

        under_25 = sum(p for t, p in total_goals_dist.items() if t < 3)
        over_25 = 1.0 - under_25

        return {
            "model": "dixon_coles",
            "lambda_home": round(lambda_home, 3),
            "lambda_away": round(lambda_away, 3),
            "home_win": round(prob_home_win, 4),
            "draw": round(prob_draw, 4),
            "away_win": round(prob_away_win, 4),
            "top_scores": [(s, round(p, 4)) for s, p in top_scores],
            "expected_total_goals": round(lambda_home + lambda_away, 3),
            "over_25": round(over_25, 4),
            "under_25": round(under_25, 4),
            "total_goals_dist": {k: round(v, 4) for k, v in total_goals_dist.items() if v > 0.001},
        }
