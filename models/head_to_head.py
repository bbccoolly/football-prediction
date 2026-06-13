# models/head_to_head.py - 交锋记录分析

from config import H2H_MAX_MATCHES, H2H_YEAR_LIMIT


class HeadToHeadModel:
    """历史交锋记录分析"""

    def __init__(self, max_matches: int = None, year_limit: int = None):
        self.max_matches = max_matches or H2H_MAX_MATCHES
        self.year_limit = year_limit or H2H_YEAR_LIMIT
        # {(team_a, team_b): [{date, home, away, home_goals, away_goals}]}
        self.records = {}

    def load_history(self, matches: list):
        """加载所有比赛，自动提取交锋对"""
        for m in matches:
            key = tuple(sorted([m["home_team"], m["away_team"]]))
            if key not in self.records:
                self.records[key] = []
            self.records[key].append({
                "date": m.get("date", ""),
                "home": m["home_team"],
                "away": m["away_team"],
                "home_goals": m["home_goals"],
                "away_goals": m["away_goals"],
            })

    def get_h2h(self, team_a: str, team_b: str) -> dict:
        """获取两队交锋统计"""
        key = tuple(sorted([team_a, team_b]))
        if key not in self.records:
            return {"matches": 0, "a_wins": 0, "draws": 0, "b_wins": 0,
                    "a_goals": 0, "b_goals": 0, "total_matches": 0}

        matches = self.records[key][-self.max_matches:]
        a_wins = 0
        b_wins = 0
        draws = 0
        a_goals = 0
        b_goals = 0

        for m in matches:
            # team_a 视角
            if m["home"] == team_a:
                gf_a = m["home_goals"]
                gf_b = m["away_goals"]
            else:
                gf_a = m["away_goals"]
                gf_b = m["home_goals"]

            a_goals += gf_a
            b_goals += gf_b

            if gf_a > gf_b:
                a_wins += 1
            elif gf_a == gf_b:
                draws += 1
            else:
                b_wins += 1

        n = len(matches)

        return {
            "total_matches": n,
            "a_wins": a_wins,
            "draws": draws,
            "b_wins": b_wins,
            "a_goals": a_goals,
            "b_goals": b_goals,
            "a_win_rate": round(a_wins / n, 3) if n > 0 else 0,
            "draw_rate": round(draws / n, 3) if n > 0 else 0,
            "b_win_rate": round(b_wins / n, 3) if n > 0 else 0,
            "avg_goals": round((a_goals + b_goals) / n, 2) if n > 0 else 0,
        }

    def predict(self, home_team: str, away_team: str, neutral: bool = False) -> dict:
        h2h = self.get_h2h(home_team, away_team)
        n = h2h["total_matches"]

        if n == 0:
            # 无交锋记录，返回等概率
            return {
                "model": "head_to_head",
                "home_win": 0.35,
                "draw": 0.30,
                "away_win": 0.35,
                "h2h_stats": h2h,
                "confidence": "low",
            }

        # 使用贝叶斯平滑：加入先验 (1 win + 1 draw + 1 loss)
        alpha = 1.0  # 先验强度
        prior = alpha * 3  # 均匀先验

        prob_home = (h2h["a_wins"] + alpha) / (n + prior)
        prob_draw = (h2h["draws"] + alpha) / (n + prior)
        prob_away = (h2h["b_wins"] + alpha) / (n + prior)

        confidence = "high" if n >= 5 else "medium" if n >= 2 else "low"

        return {
            "model": "head_to_head",
            "home_win": round(prob_home, 4),
            "draw": round(prob_draw, 4),
            "away_win": round(prob_away, 4),
            "h2h_stats": h2h,
            "confidence": confidence,
        }
