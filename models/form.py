# models/form.py - 近期状态动量模型

import math
from config import FORM_MATCHES, FORM_DECAY


class FormModel:
    """基于近期比赛结果的动量分析"""

    def __init__(self, num_matches: int = None, decay: float = None):
        self.num_matches = num_matches or FORM_MATCHES
        self.decay = decay or FORM_DECAY
        # {team: [{date, opponent, goals_for, goals_against, venue, result}]}
        self.team_history = {}

    def load_history(self, matches: list):
        """加载比赛历史：matches 按日期排序（最早→最晚）"""
        for m in matches:
            home = m["home_team"]
            away = m["away_team"]

            if home not in self.team_history:
                self.team_history[home] = []
            if away not in self.team_history:
                self.team_history[away] = []

            self.team_history[home].append({
                "date": m.get("date", ""),
                "opponent": away,
                "goals_for": m["home_goals"],
                "goals_against": m["away_goals"],
                "venue": "home",
                "result": "W" if m["home_goals"] > m["away_goals"]
                          else "D" if m["home_goals"] == m["away_goals"]
                          else "L",
            })

            self.team_history[away].append({
                "date": m.get("date", ""),
                "opponent": home,
                "goals_for": m["away_goals"],
                "goals_against": m["home_goals"],
                "venue": "away",
                "result": "W" if m["away_goals"] > m["home_goals"]
                          else "D" if m["away_goals"] == m["home_goals"]
                          else "L",
            })

    def get_form_score(self, team: str) -> dict:
        """计算球队近期状态得分"""
        if team not in self.team_history:
            return {"ppg": 1.0, "goal_diff_avg": 0.0, "form_score": 0.5, "matches_used": 0}

        recent = self.team_history[team][-self.num_matches:]
        if not recent:
            return {"ppg": 1.0, "goal_diff_avg": 0.0, "form_score": 0.5, "matches_used": 0}

        n = len(recent)
        total_points = 0
        total_gd = 0
        weight_sum = 0

        for i, m in enumerate(recent):
            w = self.decay ** (n - 1 - i)  # 越近权重越大
            pts = 3 if m["result"] == "W" else 1 if m["result"] == "D" else 0
            total_points += pts * w
            total_gd += (m["goals_for"] - m["goals_against"]) * w
            weight_sum += w

        ppg = total_points / (weight_sum * 3)  # 归一化到 0-1
        gd_avg = total_gd / weight_sum

        # 综合状态分（0-1）
        form_score = ppg * 0.6 + self._sigmoid(gd_avg, 0.5) * 0.4

        return {
            "ppg": round(ppg, 3),
            "goal_diff_avg": round(gd_avg, 3),
            "form_score": round(form_score, 3),
            "matches_used": n,
        }

    def _sigmoid(self, x: float, scale: float = 1.0) -> float:
        return 1.0 / (1.0 + math.exp(-x * scale * 3))

    def predict(self, home_team: str, away_team: str, neutral: bool = False) -> dict:
        home_form = self.get_form_score(home_team)
        away_form = self.get_form_score(away_team)

        fs_home = home_form["form_score"]
        fs_away = away_form["form_score"]
        home_boost = 0.05 if not neutral else 0.0

        # 状态分差映射概率
        diff = (fs_home + home_boost) - fs_away
        prob_home_win = 0.33 + diff * 0.5
        prob_away_win = 0.33 - diff * 0.5
        prob_draw = 1.0 - prob_home_win - prob_away_win

        prob_home_win = max(0.05, min(0.85, prob_home_win))
        prob_away_win = max(0.05, min(0.85, prob_away_win))

        total = prob_home_win + prob_draw + prob_away_win
        prob_home_win /= total
        prob_draw /= total
        prob_away_win /= total

        return {
            "model": "form",
            "home_win": round(prob_home_win, 4),
            "draw": round(prob_draw, 4),
            "away_win": round(prob_away_win, 4),
            "home_form": home_form,
            "away_form": away_form,
        }
