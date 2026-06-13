# models/knn_similar.py - KNN 相似比赛检索

import numpy as np
from config import KNN_K


class KNNSimilarModel:
    """K近邻：找历史上特征最相似的比赛，加权投票预测"""

    def __init__(self, k: int = None):
        self.k = k or KNN_K
        self.match_features = []   # [{features: np.array, result: {home_goals, away_goals}}]
        self.teams = set()

    def add_match(self, features: np.ndarray, home_goals: int, away_goals: int):
        """添加一场历史比赛"""
        self.match_features.append({
            "features": np.array(features, dtype=float),
            "home_goals": home_goals,
            "away_goals": away_goals,
        })

    def predict(self, query_features: np.ndarray) -> dict:
        """找到 K 个最相似比赛，加权预测"""
        query = np.array(query_features, dtype=float)

        if not self.match_features:
            return {
                "model": "knn_similar",
                "home_win": 0.35, "draw": 0.30, "away_win": 0.35,
                "neighbors_found": 0,
            }

        # 计算欧氏距离
        distances = []
        for mf in self.match_features:
            dist = np.linalg.norm(query - mf["features"])
            distances.append((dist, mf))

        distances.sort(key=lambda x: x[0])
        k = min(self.k, len(distances))
        neighbors = distances[:k]

        # 距离加权投票
        weights = []
        results = []
        for dist, mf in neighbors:
            w = 1.0 / (dist + 1e-6)  # 距离越近权重越大
            weights.append(w)
            results.append(mf)

        total_weight = sum(weights)
        prob_home = 0
        prob_draw = 0
        prob_away = 0
        total_goals = 0

        for w, r in zip(weights, results):
            total_goals += (r["home_goals"] + r["away_goals"]) * w
            if r["home_goals"] > r["away_goals"]:
                prob_home += w
            elif r["home_goals"] == r["away_goals"]:
                prob_draw += w
            else:
                prob_away += w

        prob_home /= total_weight
        prob_draw /= total_weight
        prob_away /= total_weight
        avg_goals = total_goals / total_weight

        return {
            "model": "knn_similar",
            "home_win": round(prob_home, 4),
            "draw": round(prob_draw, 4),
            "away_win": round(prob_away, 4),
            "expected_total_goals": round(avg_goals, 2),
            "neighbors_found": k,
        }

    def feature_vector(self, home_attack: float, home_defense: float,
                       away_attack: float, away_defense: float,
                       home_form: float, away_form: float,
                       home_elo: float, away_elo: float,
                       elo_diff: float) -> np.ndarray:
        """构造标准特征向量"""
        return np.array([
            home_attack, home_defense,
            away_attack, away_defense,
            home_form, away_form,
            home_elo, away_elo,
            elo_diff,
        ])
