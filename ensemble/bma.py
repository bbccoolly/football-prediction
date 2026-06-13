# ensemble/bma.py - 贝叶斯模型平均动态加权

import json
import os
import math
from config import WEIGHTS_FILE, INITIAL_WEIGHTS, BMA_WINDOW


class BayesianModelAveraging:
    """贝叶斯模型平均：根据各模型近期预测准确率动态调整权重"""

    def __init__(self):
        self.weights = dict(INITIAL_WEIGHTS)
        self.performance_log = {name: [] for name in INITIAL_WEIGHTS}

    def update(self, predictions: dict, actual_result: str):
        for model_name, probs in predictions.items():
            if model_name not in self.performance_log:
                self.performance_log[model_name] = []

            target = {"H": [1, 0, 0], "D": [0, 1, 0], "A": [0, 0, 1]}[actual_result]
            pred = [probs.get("home_win", 0.33),
                    probs.get("draw", 0.34),
                    probs.get("away_win", 0.33)]
            brier = sum((p - t) ** 2 for p, t in zip(pred, target))

            self.performance_log[model_name].append({
                "brier": brier, "probs": pred, "actual": actual_result,
            })

            if len(self.performance_log[model_name]) > BMA_WINDOW * 2:
                self.performance_log[model_name] = \
                    self.performance_log[model_name][-BMA_WINDOW:]

        self._recompute_weights()

    def _recompute_weights(self):
        model_scores = {}
        for name, log in self.performance_log.items():
            recent = log[-BMA_WINDOW:]
            if not recent:
                model_scores[name] = 0.5
            else:
                avg_brier = sum(r["brier"] for r in recent) / len(recent)
                model_scores[name] = max(0.1, 2.0 - avg_brier)

        total = sum(model_scores.values())
        if total > 0:
            for name in self.weights:
                self.weights[name] = model_scores.get(name, 0.5) / total

    def get_weights(self) -> dict:
        return dict(self.weights)

    def blend(self, predictions: dict) -> dict:
        home = 0.0
        draw = 0.0
        away = 0.0
        total_weight = 0.0

        for model_name, probs in predictions.items():
            w = self.weights.get(model_name, 0.05)
            home += probs.get("home_win", 0.33) * w
            draw += probs.get("draw", 0.34) * w
            away += probs.get("away_win", 0.33) * w
            total_weight += w

        if total_weight > 0:
            home /= total_weight
            draw /= total_weight
            away /= total_weight

        # 收集预期进球（过滤异常值 0.5~8.0）
        expected_goals_list = []
        for probs in predictions.values():
            if "expected_total_goals" in probs:
                eg = probs["expected_total_goals"]
                if 0.5 <= eg <= 8.0:
                    expected_goals_list.append(eg)
        avg_goals = sum(expected_goals_list) / len(expected_goals_list) if expected_goals_list else 2.7

        # 收集比分预测
        score_probs = {}
        n_preds = len(predictions)
        for probs in predictions.values():
            for score, p in probs.get("top_scores", []):
                score_probs[score] = score_probs.get(score, 0) + p / n_preds
        top_scores = sorted(score_probs.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "home_win": round(home, 4),
            "draw": round(draw, 4),
            "away_win": round(away, 4),
            "expected_total_goals": round(avg_goals, 2),
            "top_scores": [(s, round(p, 4)) for s, p in top_scores],
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
        }

    def save(self):
        os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
        with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "weights": self.weights,
                "log_count": {k: len(v) for k, v in self.performance_log.items()},
            }, f, ensure_ascii=False, indent=2)

    def load(self):
        if os.path.exists(WEIGHTS_FILE):
            with open(WEIGHTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.weights = data.get("weights", dict(INITIAL_WEIGHTS))
