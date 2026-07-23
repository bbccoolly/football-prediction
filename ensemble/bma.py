# ensemble/bma.py - 基于近期 Brier Score 的启发式动态加权

import json
import math
import os
from pathlib import Path

from config import WEIGHTS_FILE, INITIAL_WEIGHTS, BMA_WINDOW
from ensemble.prediction_contract import NoAvailableModelsError, normalize_prediction


class BayesianModelAveraging:
    """根据各模型近期 Brier Score 动态调整权重。"""

    SCHEMA_VERSION = 3
    DERIVED_MODEL_IDS = frozenset({"monte_carlo"})

    def __init__(self, weights_file=None):
        self.weights_file = str(weights_file or WEIGHTS_FILE)
        self.weights = dict(INITIAL_WEIGHTS)
        self.performance_log = {name: [] for name in INITIAL_WEIGHTS}
        self.load_warnings = []
        self.file_metadata = {}

    def update(self, predictions: dict, actual_result: str):
        for model_name, raw_prediction in predictions.items():
            if model_name in self.DERIVED_MODEL_IDS or raw_prediction.get("role") == "derived":
                continue
            prediction = normalize_prediction(model_name, raw_prediction)
            if not prediction["available"]:
                continue
            if model_name not in self.performance_log:
                self.performance_log[model_name] = []

            target = {"H": [1, 0, 0], "D": [0, 1, 0], "A": [0, 0, 1]}[actual_result]
            predicted = [prediction["home_win"], prediction["draw"], prediction["away_win"]]
            brier = sum((value - expected) ** 2 for value, expected in zip(predicted, target))
            self.performance_log[model_name].append({
                "brier": brier, "probs": predicted, "actual": actual_result,
            })
            if len(self.performance_log[model_name]) > BMA_WINDOW * 2:
                self.performance_log[model_name] = self.performance_log[model_name][-BMA_WINDOW:]

        self._recompute_weights()

    def _recompute_weights(self):
        model_scores = {}
        for name, log in self.performance_log.items():
            recent = log[-BMA_WINDOW:]
            if not recent:
                model_scores[name] = 0.5
            else:
                avg_brier = sum(row["brier"] for row in recent) / len(recent)
                model_scores[name] = max(0.1, 2.0 - avg_brier)

        independent_names = [
            name for name in self.weights if name not in self.DERIVED_MODEL_IDS
        ]
        total = sum(model_scores.get(name, 0.5) for name in independent_names)
        if total > 0:
            for name in independent_names:
                self.weights[name] = model_scores.get(name, 0.5) / total
        for name in self.DERIVED_MODEL_IDS:
            if name in self.weights:
                self.weights[name] = 0.0

    def get_weights(self) -> dict:
        weights = dict(self.weights)
        for name in self.DERIVED_MODEL_IDS:
            if name in weights:
                weights[name] = 0.0
        return weights

    def _effective_predictions(self, predictions):
        normalized = {}
        excluded = []
        candidates = {}

        for model_name, raw_prediction in predictions.items():
            prediction = normalize_prediction(model_name, raw_prediction)
            normalized[model_name] = prediction
            configured_weight = self.weights.get(model_name, 0.0)
            if model_name in self.DERIVED_MODEL_IDS or raw_prediction.get("role") == "derived":
                excluded.append({
                    "model_id": model_name,
                    "status": "derived",
                    "reason": "derived_output",
                })
            elif not prediction["available"]:
                excluded.append({
                    "model_id": model_name,
                    "status": prediction["status"],
                    "reason": ",".join(prediction.get("warnings", [])) or "model_unavailable",
                })
            elif not math.isfinite(configured_weight) or configured_weight <= 0:
                excluded.append({
                    "model_id": model_name,
                    "status": "no_weight",
                    "reason": "no_configured_weight",
                })
            else:
                candidates[model_name] = prediction

        total_weight = sum(self.weights[name] for name in candidates)
        if not candidates or total_weight <= 0:
            raise NoAvailableModelsError("当前没有可用预测模型")

        effective_weights = {
            name: self.weights[name] / total_weight for name in candidates
        }
        return candidates, normalized, effective_weights, excluded

    def blend(self, predictions: dict) -> dict:
        candidates, _, effective_weights, excluded = self._effective_predictions(predictions)

        home = sum(candidates[name]["home_win"] * weight for name, weight in effective_weights.items())
        draw = sum(candidates[name]["draw"] * weight for name, weight in effective_weights.items())
        away = sum(candidates[name]["away_win"] * weight for name, weight in effective_weights.items())

        goal_values = []
        for name, prediction in candidates.items():
            value = prediction.get("expected_total_goals")
            if isinstance(value, (int, float)) and math.isfinite(value) and 0.5 <= value <= 8.0:
                goal_values.append((float(value), effective_weights[name]))
        goal_weight = sum(weight for _, weight in goal_values)
        avg_goals = (
            sum(value * weight for value, weight in goal_values) / goal_weight
            if goal_weight > 0 else 2.7
        )

        score_probs = {}
        for name, prediction in candidates.items():
            for score, probability in prediction.get("top_scores", []):
                if isinstance(probability, (int, float)) and math.isfinite(probability):
                    score_probs[score] = score_probs.get(score, 0.0) + probability * effective_weights[name]
        top_scores = sorted(score_probs.items(), key=lambda item: item[1], reverse=True)[:5]

        rounded_effective = {name: round(weight, 4) for name, weight in effective_weights.items()}
        return {
            "home_win": round(home, 4),
            "draw": round(draw, 4),
            "away_win": round(away, 4),
            "expected_total_goals": round(avg_goals, 2),
            "top_scores": [(score, round(probability, 4)) for score, probability in top_scores],
            "configured_weights": {
                name: round(weight, 4) for name, weight in self.get_weights().items()
            },
            "effective_weights": rounded_effective,
            "excluded_models": excluded,
            "weights": rounded_effective,
        }

    def save(self):
        path = Path(self.weights_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        data = {
            **self.file_metadata,
            "schema_version": self.SCHEMA_VERSION,
            "weights": self.weights,
            "log_count": {name: len(log) for name, log in self.performance_log.items()},
        }
        try:
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def load(self):
        self.load_warnings = []
        path = Path(self.weights_file)
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw_weights = data.get("weights", {})
            if not isinstance(raw_weights, dict):
                raise ValueError("weights must be an object")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self.weights = dict(INITIAL_WEIGHTS)
            self.load_warnings.append("weights_file_invalid")
            return False

        migrated = dict(raw_weights)
        self.file_metadata = {
            key: value for key, value in data.items()
            if key not in {"schema_version", "weights", "log_count"}
        }
        needs_save = data.get("schema_version") != self.SCHEMA_VERSION
        if "knn" in migrated:
            if "knn_similar" not in migrated:
                migrated["knn_similar"] = migrated["knn"]
            del migrated["knn"]
            self.load_warnings.append("knn_key_migrated")
            needs_save = True

        monte_carlo_weight = migrated.get("monte_carlo", 0.0)
        if monte_carlo_weight != 0:
            self.load_warnings.append("monte_carlo_weight_disabled")
            needs_save = True
        migrated["monte_carlo"] = 0.0

        sanitized = {}
        for name, default_weight in INITIAL_WEIGHTS.items():
            value = migrated.get(name, default_weight)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                value = default_weight
                self.load_warnings.append(f"invalid_weight:{name}")
            sanitized[name] = float(value)
        sanitized["monte_carlo"] = 0.0
        self.weights = sanitized
        if needs_save:
            self.load_warnings.append("weights_migration_required")
        return True
