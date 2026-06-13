# models/poisson.py - 泊松分布比分预测模型

import math
import numpy as np
from scipy.stats import poisson
from config import POISSON_LEAGUE_AVG_GOALS


class PoissonModel:
    """基于泊松分布的足球比分预测"""

    MAX_GOALS = 10  # 单队最大进球数

    def __init__(self, league: str = "default"):
        self.league_avg = POISSON_LEAGUE_AVG_GOALS.get(league, 2.70)
        self.attack_strengths = {}   # {team: attack_factor}
        self.defense_strengths = {}  # {team: defense_factor}

    def set_team_strengths(self, strengths: dict):
        """strengths = {team: {"attack": 1.2, "defense": 0.9}}"""
        for team, s in strengths.items():
            self.attack_strengths[team] = s["attack"]
            self.defense_strengths[team] = s["defense"]

    def _expected_goals(self, team_attack: float, opp_defense: float,
                        home_factor: float = 1.0) -> float:
        """计算预期进球数 lambda"""
        return (self.league_avg / 2.0) * team_attack * opp_defense * home_factor

    def predict(self, home_team: str, away_team: str,
                neutral: bool = False, home_factor: float = 1.15) -> dict:
        """预测比赛，返回比分概率矩阵和汇总"""
        atk_h = self.attack_strengths.get(home_team, 1.0)
        def_h = self.defense_strengths.get(home_team, 1.0)
        atk_a = self.attack_strengths.get(away_team, 1.0)
        def_a = self.defense_strengths.get(away_team, 1.0)

        hf = home_factor if not neutral else 1.0

        lambda_home = self._expected_goals(atk_h, def_a, hf)
        lambda_away = self._expected_goals(atk_a, def_h, 1.0 / hf)

        lambda_home = max(0.3, min(3.5, max(0.5, lambda_home)))
        lambda_away = max(0.3, min(3.5, max(0.5, lambda_away)))

        # 比分概率矩阵
        score_matrix = {}
        prob_home_win = 0
        prob_draw = 0
        prob_away_win = 0

        for h in range(self.MAX_GOALS + 1):
            for a in range(self.MAX_GOALS + 1):
                p = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
                score_matrix[f"{h}-{a}"] = p
                if h > a:
                    prob_home_win += p
                elif h == a:
                    prob_draw += p
                else:
                    prob_away_win += p

        # 总进球分布
        total_goals_dist = {}
        for total in range(self.MAX_GOALS * 2 + 1):
            total_goals_dist[total] = sum(
                p for score, p in score_matrix.items()
                if sum(map(int, score.split("-"))) == total
            )

        # 最可能比分 TOP 5
        top_scores = sorted(score_matrix.items(), key=lambda x: x[1], reverse=True)[:5]

        # Over/Under 2.5
        under_25 = sum(p for t, p in total_goals_dist.items() if t < 3)
        over_25 = 1.0 - under_25

        return {
            "model": "poisson",
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

    def predict_htft(self, home_team: str, away_team: str,
                     neutral: bool = False, home_factor: float = 1.15) -> dict:
        """预测半全场 (Half-Time / Full-Time) 9种结果"""
        atk_h = self.attack_strengths.get(home_team, 1.0)
        def_h = self.defense_strengths.get(home_team, 1.0)
        atk_a = self.attack_strengths.get(away_team, 1.0)
        def_a = self.defense_strengths.get(away_team, 1.0)

        hf = home_factor if not neutral else 1.0

        # Full-time lambda
        lam_h_ft = self._expected_goals(atk_h, def_a, hf)
        lam_a_ft = self._expected_goals(atk_a, def_h, 1.0 / hf)
        lam_h_ft = max(0.5, min(3.5, lam_h_ft))
        lam_a_ft = max(0.5, min(3.5, lam_a_ft))

        # First half: ~42% of full-time goals, second half: ~58%
        lam_h_ht = lam_h_ft * 0.42
        lam_a_ht = lam_a_ft * 0.42
        lam_h_sh = lam_h_ft * 0.58
        lam_a_sh = lam_a_ft * 0.58

        # 9 outcomes: H/H, H/D, H/A, D/H, D/D, D/A, A/H, A/D, A/A
        labels = ["\u80dc/\u80dc", "\u80dc/\u5e73", "\u80dc/\u8d1f", "\u5e73/\u80dc", "\u5e73/\u5e73", "\u5e73/\u8d1f", "\u8d1f/\u80dc", "\u8d1f/\u5e73", "\u8d1f/\u8d1f"]
        htft_probs = {label: 0.0 for label in labels}

        max_g = 6  # cap for computational efficiency
        total_prob = 0.0

        for h_ht in range(max_g + 1):
            for a_ht in range(max_g + 1):
                p_ht_h = poisson.pmf(h_ht, lam_h_ht)
                p_ht_a = poisson.pmf(a_ht, lam_a_ht)
                p_ht = p_ht_h * p_ht_a
                if p_ht < 1e-8: continue

                # Determine HT result
                if h_ht > a_ht: ht_result = 0  # H
                elif h_ht == a_ht: ht_result = 1  # D
                else: ht_result = 2  # A

                for h_sh in range(max_g + 1):
                    for a_sh in range(max_g + 1):
                        p_sh_h = poisson.pmf(h_sh, lam_h_sh)
                        p_sh_a = poisson.pmf(a_sh, lam_a_sh)
                        p_sh = p_sh_h * p_sh_a
                        if p_sh < 1e-8: continue

                        # Full-time score
                        h_ft = h_ht + h_sh
                        a_ft = a_ht + a_sh

                        # Determine FT result
                        if h_ft > a_ft: ft_result = 0
                        elif h_ft == a_ft: ft_result = 1
                        else: ft_result = 2

                        joint_p = p_ht * p_sh
                        total_prob += joint_p

                        # Map to label
                        idx = ht_result * 3 + ft_result
                        htft_probs[labels[idx]] += joint_p

        # Normalize
        if total_prob > 0:
            for k in htft_probs:
                htft_probs[k] = round(float(htft_probs[k] / total_prob), 4)

        # Sort by probability
        sorted_htft = sorted(htft_probs.items(), key=lambda x: x[1], reverse=True)

        return {
            "model": "poisson_htft",
            "lambda_ht_home": round(float(lam_h_ht), 3),
            "lambda_ht_away": round(float(lam_a_ht), 3),
            "lambda_sh_home": round(float(lam_h_sh), 3),
            "lambda_sh_away": round(float(lam_a_sh), 3),
            "htft": htft_probs,
            "top": sorted_htft,
        }

    def predict_handicap(self, home_team: str, away_team: str, neutral: bool = False) -> dict:
        """预测让球胜负概率（常见盘口）"""
        atk_h = self.attack_strengths.get(home_team, 1.0)
        def_h = self.defense_strengths.get(home_team, 1.0)
        atk_a = self.attack_strengths.get(away_team, 1.0)
        def_a = self.defense_strengths.get(away_team, 1.0)
        hf = 1.0 if neutral else 1.15

        lam_h = self._expected_goals(atk_h, def_a, hf)
        lam_a = self._expected_goals(atk_a, def_h, 1.0 / hf)
        lam_h = max(0.3, min(3.5, max(0.5, lam_h)))
        lam_a = max(0.3, min(3.5, max(0.5, lam_a)))

        # 常见让球盘口（从主队视角：负数=主队让球）
        spreads = [-2.0, -1.5, -1.0, 0, 1.0, 1.5, 2.0]
        result = {}

        for spread in spreads:
            win, draw_s, lose = 0.0, 0.0, 0.0
            for h in range(self.MAX_GOALS + 1):
                for a in range(self.MAX_GOALS + 1):
                    p = poisson.pmf(h, lam_h) * poisson.pmf(a, lam_a)
                    diff = h - a + spread
                    if diff > 0:
                        win += p
                    elif diff == 0:
                        draw_s += p
                    else:
                        lose += p

            label = f"主队{'受' if spread > 0 else '让'}{abs(spread)}球"
            if spread == int(spread):
                # 整数盘口有走水
                result[label] = {
                    "spread": spread,
                    "win": round(win, 4),
                    "draw": round(draw_s, 4),
                    "lose": round(lose, 4),
                }
            else:
                # 半球盘口无走水
                result[label] = {
                    "spread": spread,
                    "win": round(win + draw_s, 4),  # 半球盘走水归入胜
                    "draw": 0,
                    "lose": round(lose, 4),
                }

        return result



def build_strengths_from_results(matches: list, league: str = "default") -> dict:
    """从历史比赛结果推算攻防强度
    matches = [{home_team, away_team, home_goals, away_goals}]
    """
    league_avg = POISSON_LEAGUE_AVG_GOALS.get(league, 2.70) / 2.0

    team_goals_for = {}
    team_goals_against = {}
    team_matches = {}

    for m in matches:
        home = m["home_team"]
        away = m["away_team"]

        for t in [home, away]:
            if t not in team_goals_for:
                team_goals_for[t] = 0
                team_goals_against[t] = 0
                team_matches[t] = 0

        team_goals_for[home] += m["home_goals"]
        team_goals_against[home] += m["away_goals"]
        team_matches[home] += 1

        team_goals_for[away] += m["away_goals"]
        team_goals_against[away] += m["home_goals"]
        team_matches[away] += 1

    strengths = {}
    for t in team_matches:
        n = max(team_matches[t], 1)
        atk = (team_goals_for[t] / n) / league_avg
        defs = (team_goals_against[t] / n) / league_avg
        strengths[t] = {"attack": round(atk, 3), "defense": round(defs, 3)}

    return strengths
