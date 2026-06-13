# models/elo.py - ELO 动态评级系统

import math
import json
import os
from config import ELO_INITIAL, ELO_K, ELO_HOME_BONUS, ELO_SCALE, PROCESSED_DIR


class EloRating:
    """ELO 动态评级：根据比赛结果更新球队分值，预测胜平负概率"""

    def __init__(self):
        self.ratings = {}          # {team_name: elo_score}
        self.history = []          # [{team, elo_before, elo_after, match_id}]
        self.elos_file = os.path.join(PROCESSED_DIR, "elo_ratings.json")

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, ELO_INITIAL)

    def expected_score(self, elo_a: float, elo_b: float) -> float:
        """球队 A 对 B 的预期胜率 (0-1)"""
        return 1.0 / (1.0 + math.pow(10, (elo_b - elo_a) / ELO_SCALE))

    def predict_match(self, home_team: str, away_team: str, neutral: bool = False) -> dict:
        """预测一场比赛的胜平负概率"""
        elo_home = self.get_rating(home_team) + (0 if neutral else ELO_HOME_BONUS)
        elo_away = self.get_rating(away_team)

        exp_home = self.expected_score(elo_home, elo_away)
        exp_away = 1.0 - exp_home

        # 使用经验公式将预期胜率拆分为胜平负
        draw_factor = 0.22  # 约 22% 平局率
        prob_draw = draw_factor * (1.0 - abs(exp_home - 0.5) * 1.6)
        prob_draw = max(0.10, min(0.35, prob_draw))

        prob_home_win = (exp_home - prob_draw / 2) * (1.0 - prob_draw) / (1.0 - prob_draw/2)
        prob_away_win = 1.0 - prob_home_win - prob_draw

        prob_home_win = max(0.01, min(0.95, prob_home_win))
        prob_away_win = max(0.01, min(0.95, prob_away_win))
        prob_draw = max(0.05, 1.0 - prob_home_win - prob_away_win)

        return {
            "home_win": round(prob_home_win, 4),
            "draw": round(prob_draw, 4),
            "away_win": round(prob_away_win, 4),
            "elo_home": elo_home,
            "elo_away": elo_away,
        }

    def update(self, home_team: str, away_team: str,
               home_goals: int, away_goals: int,
               neutral: bool = False, importance: float = 1.0):
        """赛后更新 ELO 分"""
        elo_home = self.get_rating(home_team) + (0 if neutral else ELO_HOME_BONUS)
        elo_away = self.get_rating(away_team)

        # 实际结果
        if home_goals > away_goals:
            actual_home, actual_away = 1.0, 0.0
        elif home_goals == away_goals:
            actual_home, actual_away = 0.5, 0.5
        else:
            actual_home, actual_away = 0.0, 1.0

        # 净胜球加成
        goal_diff = abs(home_goals - away_goals)
        goal_factor = 1.0 + math.log(max(goal_diff, 1)) * 0.5 if goal_diff > 1 else 1.0

        exp_home = self.expected_score(elo_home, elo_away)
        exp_away = 1.0 - exp_home

        k = ELO_K * importance * goal_factor

        new_home = elo_home + k * (actual_home - exp_home)
        new_away = elo_away + k * (actual_away - exp_away)

        self.ratings[home_team] = new_home - (0 if neutral else ELO_HOME_BONUS)
        self.ratings[away_team] = new_away

        self.history.append({
            "home_team": home_team,
            "away_team": away_team,
            "elo_home_before": elo_home,
            "elo_away_before": elo_away,
            "elo_home_after": new_home,
            "elo_away_after": new_away,
        })

    def batch_update(self, matches: list):
        """批量更新：matches = [{home_team, away_team, home_goals, away_goals, neutral, importance}]"""
        for m in matches:
            self.update(
                m["home_team"], m["away_team"],
                m["home_goals"], m["away_goals"],
                m.get("neutral", False),
                m.get("importance", 1.0),
            )

    def save(self):
        os.makedirs(os.path.dirname(self.elos_file), exist_ok=True)
        with open(self.elos_file, "w", encoding="utf-8") as f:
            json.dump({"ratings": self.ratings, "history": self.history[-500:]}, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(self.elos_file):
            with open(self.elos_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.ratings = data.get("ratings", {})
                self.history = data.get("history", [])

    def get_league_rankings(self) -> list:
        """返回 ELO 排名"""
        return sorted(self.ratings.items(), key=lambda x: x[1], reverse=True)
