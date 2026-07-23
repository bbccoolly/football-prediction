"""统一模型预测结果，隔离不可用或非法模型输出。"""

import math


PROBABILITY_FIELDS = ("home_win", "draw", "away_win")
NORMALIZATION_TOLERANCE = 0.02


class NoAvailableModelsError(RuntimeError):
    """没有任何可参与融合的模型。"""


def _append_warning(result, warning):
    warnings = result.setdefault("warnings", [])
    if warning not in warnings:
        warnings.append(warning)


def normalize_prediction(model_id, raw_result):
    raw_result = raw_result if isinstance(raw_result, dict) else {}
    result = dict(raw_result)
    result["model_id"] = model_id
    result.setdefault("model", model_id)
    result.setdefault("model_version", "1")
    result.setdefault("status", "ready")
    result.setdefault("data_quality", None)
    result["warnings"] = list(result.get("warnings") or [])

    explicitly_unavailable = (
        result.get("available") is False
        or result.get("data_valid") is False
        or result.get("status") != "ready"
        or (model_id == "knn_similar" and result.get("neighbors_found") == 0)
    )
    if explicitly_unavailable:
        result["available"] = False
        if result.get("status") == "ready":
            result["status"] = "unavailable"
        _append_warning(result, "model_unavailable")
        return result

    values = []
    for field in PROBABILITY_FIELDS:
        value = result.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            result["available"] = False
            result["status"] = "invalid_probabilities"
            result[field] = None
            _append_warning(result, "invalid_probabilities")
            return result
        values.append(float(value))

    total = sum(values)
    if total <= 0 or abs(total - 1.0) > NORMALIZATION_TOLERANCE + 1e-9:
        result["available"] = False
        result["status"] = "invalid_probabilities"
        _append_warning(result, "invalid_probability_sum")
        return result

    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        for field, value in zip(PROBABILITY_FIELDS, values):
            result[field] = value / total
        _append_warning(result, "probabilities_normalized")

    result["available"] = True
    result["status"] = "ready"
    return result


def available_predictions(predictions):
    normalized = {
        model_id: normalize_prediction(model_id, prediction)
        for model_id, prediction in predictions.items()
    }
    return {
        model_id: prediction
        for model_id, prediction in normalized.items()
        if prediction["available"]
    }, normalized
