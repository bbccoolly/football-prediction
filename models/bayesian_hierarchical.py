# models/bayesian_hierarchical.py - 贝叶斯层次模型

import numpy as np
from scipy import stats


class BayesianHierarchicalModel:
    """贝叶斯层次模型：将球队实力视为随机变量，输出带置信区间的预测"""

    def __init__(self, prior_mean: float = 0.0, prior_std: float = 2.0):
        self.prior_mean = prior_mean
        self.prior_std = prior_std
        self.team_posteriors = {}  # {team: (mean, std)}
        self.is_fitted = False

    def fit(self, matches: list, n_iter: int = 2000):
        """用 MCMC 风格的吉布斯采样拟合
        （简化实现，用解析近似代替完整 MCMC）
        matches = [{home_team, away_team, home_goals, away_goals}]
        """
        # 收集球队进球/失球数据
        team_gf = {}
        team_ga = {}
        team_games = {}

        for m in matches:
            for t in [m["home_team"], m["away_team"]]:
                if t not in team_gf:
                    team_gf[t] = 0.0
                    team_ga[t] = 0.0
                    team_games[t] = 0

            team_gf[m["home_team"]] += m["home_goals"]
            team_ga[m["home_team"]] += m["away_goals"]
            team_games[m["home_team"]] += 1

            team_gf[m["away_team"]] += m["away_goals"]
            team_ga[m["away_team"]] += m["home_goals"]
            team_games[m["away_team"]] += 1

        # 全局均值
        all_gf = [v / max(team_games[t], 1) for t, v in team_gf.items()]
        global_mean = np.mean(all_gf) if all_gf else 1.3
        global_std = np.std(all_gf) if len(all_gf) > 1 else 0.5

        # 贝叶斯更新：先验 + 数据 → 后验
        for t in team_games:
            n = max(team_games[t], 1)
            data_mean_gf = team_gf[t] / n
            data_mean_ga = team_ga[t] / n

            # 进攻后验
            attack_post_var = 1.0 / (1.0 / self.prior_std**2 + n / (global_std**2 + 1e-6))
            attack_post_mean = attack_post_var * (
                self.prior_mean / self.prior_std**2 +
                data_mean_gf * n / (global_std**2 + 1e-6)
            )
            attack_post_std = np.sqrt(attack_post_var)

            # 防守后验
            defense_post_var = 1.0 / (1.0 / self.prior_std**2 + n / (global_std**2 + 1e-6))
            defense_post_mean = defense_post_var * (
                self.prior_mean / self.prior_std**2 +
                data_mean_ga * n / (global_std**2 + 1e-6)
            )
            defense_post_std = np.sqrt(defense_post_var)

            self.team_posteriors[t] = {
                "attack": (attack_post_mean, attack_post_std),
                "defense": (defense_post_mean, defense_post_std),
                "games": n,
            }

        self.is_fitted = True
        self.global_mean = global_mean

    def predict(self, home_team: str, away_team: str,
                neutral: bool = False, n_samples: int = 5000) -> dict:
        """用后验分布采样预测"""
        if not self.is_fitted:
            return {
                "model": "bayesian",
                "home_win": 0.38, "draw": 0.28, "away_win": 0.34,
                "status": "not_fitted",
            }

        # 获取后验或先验
        h = self.team_posteriors.get(home_team, {
            "attack": (self.global_mean, self.prior_std),
            "defense": (self.global_mean, self.prior_std),
        })
        a = self.team_posteriors.get(away_team, {
            "attack": (self.global_mean, self.prior_std),
            "defense": (self.global_mean, self.prior_std),
        })

        home_bonus = 0.0 if neutral else 0.15

        rng = np.random.default_rng(42)

        # 采样
        home_atk_samples = rng.normal(h["attack"][0] + home_bonus, h["attack"][1], n_samples)
        home_def_samples = rng.normal(h["defense"][0], h["defense"][1], n_samples)
        away_atk_samples = rng.normal(a["attack"][0], a["attack"][1], n_samples)
        away_def_samples = rng.normal(a["defense"][0], a["defense"][1], n_samples)

        # 模拟比赛
        home_lam = np.clip(home_atk_samples * away_def_samples, 0.05, 5.0)
        home_goals = rng.poisson(home_lam)
        away_lam = np.clip(away_atk_samples * home_def_samples, 0.05, 5.0)
        away_goals = rng.poisson(away_lam)

        home_wins = np.sum(home_goals > away_goals)
        draws = np.sum(home_goals == away_goals)
        away_wins = np.sum(home_goals < away_goals)

        total_goals = home_goals + away_goals
        avg_total = np.mean(total_goals)

        # 置信区间（95%）
        diff = home_goals - away_goals
        ci_diff = np.percentile(diff, [2.5, 97.5])
        ci_total = np.percentile(total_goals, [2.5, 97.5])

        return {
            "model": "bayesian",
            "home_win": round(home_wins / n_samples, 4),
            "draw": round(draws / n_samples, 4),
            "away_win": round(away_wins / n_samples, 4),
            "expected_total_goals": round(float(avg_total), 2),
            "ci_goal_diff": [round(float(x), 2) for x in ci_diff],
            "ci_total_goals": [round(float(x), 2) for x in ci_total],
            "samples": n_samples,
        }
