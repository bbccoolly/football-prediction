# models/monte_carlo.py - 蒙特卡洛模拟

import numpy as np
from config import MC_SIMULATIONS


class MonteCarloModel:
    """蒙特卡洛模拟：基于各模型的概率分布，大量模拟比赛结果"""

    def __init__(self, simulations: int = None):
        self.simulations = simulations or MC_SIMULATIONS
        self.MAX_GOALS = 10

    def simulate(self, predictions: list, weights: list = None) -> dict:
        """基于多个模型的输出进行蒙特卡洛模拟

        predictions: [{home_win, draw, away_win, expected_total_goals?, top_scores?}]
        weights: 各模型权重
        """
        if weights is None:
            weights = [1.0 / len(predictions)] * len(predictions)
        else:
            total_w = sum(weights)
            weights = [w / total_w for w in weights]

        n = len(predictions)
        sims = self.simulations

        results = {"home_win": 0, "draw": 0, "away_win": 0}
        score_counts = {}
        total_goals_counts = {}

        rng = np.random.default_rng(42)

        for _ in range(sims):
            # 随机选一个模型（按权重）
            model_idx = rng.choice(n, p=weights)
            pred = predictions[model_idx]

            # 根据该模型的概率决定胜负
            r = rng.random()
            if r < pred.get("home_win", 0.33):
                outcome = "H"
            elif r < pred.get("home_win", 0.33) + pred.get("draw", 0.34):
                outcome = "D"
            else:
                outcome = "A"

            # 生成具体比分
            home_g, away_g = self._sample_score(pred, outcome, rng)
            score_key = f"{home_g}-{away_g}"
            total_g = home_g + away_g

            if outcome == "H":
                results["home_win"] += 1
            elif outcome == "D":
                results["draw"] += 1
            else:
                results["away_win"] += 1

            score_counts[score_key] = score_counts.get(score_key, 0) + 1
            total_goals_counts[total_g] = total_goals_counts.get(total_g, 0) + 1

        # 归一化
        prob_home = round(results["home_win"] / sims, 4)
        prob_draw = round(results["draw"] / sims, 4)
        prob_away = round(results["away_win"] / sims, 4)

        top_scores = sorted(score_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        top_scores = [(s, round(c / sims, 4)) for s, c in top_scores]

        expected_goals = sum(t * c for t, c in total_goals_counts.items()) / sims

        over_25 = sum(c for t, c in total_goals_counts.items() if t > 2) / sims
        under_25 = 1.0 - over_25

        goals_dist = {t: round(c / sims, 4) for t, c in sorted(total_goals_counts.items())}

        return {
            "model": "monte_carlo",
            "home_win": prob_home,
            "draw": prob_draw,
            "away_win": prob_away,
            "top_scores": top_scores,
            "expected_total_goals": round(expected_goals, 3),
            "over_25": round(over_25, 4),
            "under_25": round(under_25, 4),
            "total_goals_dist": goals_dist,
            "simulations": sims,
        }

    def _sample_score(self, pred: dict, outcome: str, rng: np.random.Generator) -> tuple:
        """根据预测结果采样具体比分"""
        top_scores = pred.get("top_scores", [])
        if top_scores:
            scores = [s for s, p in top_scores]
            probs = np.array([p for s, p in top_scores])
            s = probs.sum(); probs = probs / s if s > 0 else np.ones(len(probs))/len(probs)
            score = rng.choice(scores, p=probs)
            h, a = score.split("-")
            return int(h), int(a)

        # Fallback: 按预期总进球随机分配
        exp_goals = pred.get("expected_total_goals", 2.7)
        total = int(round(rng.poisson(exp_goals)))
        total = min(total, self.MAX_GOALS * 2)

        if outcome == "H":
            low_h = max(1, total // 2 + 1); home_g = rng.integers(low_h, max(low_h + 1, total + 1))
        elif outcome == "A":
            low_a = max(1, total // 2 + 1); away_g = rng.integers(low_a, max(low_a + 1, total + 1))
            home_g = total - away_g
        else:
            half = total // 2
            home_g = half
            away_g = total - half

        home_g = max(0, min(self.MAX_GOALS, home_g))
        away_g = max(0, min(self.MAX_GOALS, total - home_g))

        return home_g, away_g

    def simulate_from_distributions(self, dists: list) -> dict:
        """直接从分布列表模拟（更底层接口）
        dists: [{distribution_name: {score: prob, ...}}, ...]
        """
        # 简化实现：将所有分布的概率平均
        merged = {}
        for dist in dists:
            for score, prob in dist.items():
                merged[score] = merged.get(score, 0) + prob / len(dists)

        scores = list(merged.keys())
        probs = np.array([merged[s] for s in scores])
        s = probs.sum(); probs = probs / s if s > 0 else np.ones(len(probs))/len(probs)

        results = {"home_win": 0, "draw": 0, "away_win": 0}
        score_counts = {}
        total_goals_counts = {}

        rng = np.random.default_rng(42)
        for _ in range(self.simulations):
            score = rng.choice(scores, p=probs)
            h, a = map(int, score.split("-"))
            total = h + a

            if h > a:
                results["home_win"] += 1
            elif h == a:
                results["draw"] += 1
            else:
                results["away_win"] += 1

            score_counts[score] = score_counts.get(score, 0) + 1
            total_goals_counts[total] = total_goals_counts.get(total, 0) + 1

        return {
            "model": "monte_carlo",
            "home_win": round(results["home_win"] / self.simulations, 4),
            "draw": round(results["draw"] / self.simulations, 4),
            "away_win": round(results["away_win"] / self.simulations, 4),
            "top_scores": sorted(score_counts.items(), key=lambda x: x[1], reverse=True)[:5],
        }
