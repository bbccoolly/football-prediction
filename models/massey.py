# models/massey.py - Massey 线性排名系统

import numpy as np


class MasseyRanking:
    """Massey 方法：用线性代数从净胜球推算球队实力分"""

    def __init__(self):
        self.ratings = {}
        self.offensive = {}
        self.defensive = {}

    def fit(self, matches: list):
        if not matches:
            return
        teams = set()
        for m in matches:
            teams.add(m["home_team"])
            teams.add(m["away_team"])
        teams = sorted(teams)
        n = len(teams)
        if n == 0:
            return
        team_idx = {t: i for i, t in enumerate(teams)}

        M = np.zeros((n, n))
        p = np.zeros(n)

        for m in matches:
            hi = team_idx[m["home_team"]]
            ai = team_idx[m["away_team"]]
            gd = m["home_goals"] - m["away_goals"]

            M[hi, hi] += 1
            M[ai, ai] += 1
            M[hi, ai] -= 1
            M[ai, hi] -= 1

            neutral_factor = 1.0 if m.get("neutral", False) else 0.85
            p[hi] += gd * neutral_factor
            p[ai] -= gd * neutral_factor

        M[-1, :] = 1.0
        p[-1] = 0.0

        try:
            ratings_vec = np.linalg.solve(M, p)
        except np.linalg.LinAlgError:
            ratings_vec = np.linalg.lstsq(M, p, rcond=None)[0]

        self.ratings = {t: round(float(ratings_vec[i]), 2) for t, i in team_idx.items()}
        self._compute_off_def(matches, teams, team_idx)

    def _compute_off_def(self, matches, teams, team_idx):
        team_goals_for = {t: 0.0 for t in teams}
        team_goals_against = {t: 0.0 for t in teams}
        team_games = {t: 0 for t in teams}

        for m in matches:
            team_goals_for[m["home_team"]] += m["home_goals"]
            team_goals_against[m["home_team"]] += m["away_goals"]
            team_games[m["home_team"]] += 1
            team_goals_for[m["away_team"]] += m["away_goals"]
            team_goals_against[m["away_team"]] += m["home_goals"]
            team_games[m["away_team"]] += 1

        for t in teams:
            n_games = max(team_games[t], 1)
            self.offensive[t] = round(team_goals_for[t] / n_games, 3)
            self.defensive[t] = round(team_goals_against[t] / n_games, 3)

    def predict(self, home_team: str, away_team: str, neutral: bool = False) -> dict:
        r_home = self.ratings.get(home_team, 0)
        r_away = self.ratings.get(away_team, 0)

        home_bonus = 0 if neutral else 0.35
        diff = r_home - r_away + home_bonus

        prob_home_win = 1.0 / (1.0 + np.exp(-diff * 2.5))
        prob_away_win = 1.0 / (1.0 + np.exp(diff * 2.5))

        draw_factor = 0.22
        prob_draw = draw_factor * (1.0 - abs(prob_home_win - 0.5) * 1.6)
        prob_draw = max(0.08, min(0.35, prob_draw))

        prob_home_win -= prob_draw / 2
        prob_away_win -= prob_draw / 2

        total = prob_home_win + prob_draw + prob_away_win
        prob_home_win /= total
        prob_draw /= total
        prob_away_win /= total

        return {
            "model": "massey",
            "home_win": round(max(0.01, prob_home_win), 4),
            "draw": round(prob_draw, 4),
            "away_win": round(max(0.01, prob_away_win), 4),
            "rating_home": r_home,
            "rating_away": r_away,
        }
