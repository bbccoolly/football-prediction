import math

import numpy as np

from ensemble.prediction_contract import normalize_prediction
from models.neural_net import NeuralNetModel
from models.xgboost_model import XGBoostModel


def test_valid_prediction_gets_contract_fields():
    result = normalize_prediction("poisson", {
        "home_win": 0.4, "draw": 0.3, "away_win": 0.3,
        "expected_total_goals": 2.6,
    })

    assert result["model_id"] == "poisson"
    assert result["available"] is True
    assert result["status"] == "ready"
    assert result["data_quality"] is None
    assert result["expected_total_goals"] == 2.6


def test_small_probability_drift_is_normalized():
    result = normalize_prediction("elo", {
        "home_win": 0.4, "draw": 0.3, "away_win": 0.29,
    })

    assert result["available"] is True
    assert math.isclose(
        result["home_win"] + result["draw"] + result["away_win"], 1.0
    )
    assert "probabilities_normalized" in result["warnings"]


def test_large_probability_error_is_unavailable():
    result = normalize_prediction("elo", {
        "home_win": 0.7, "draw": 0.4, "away_win": 0.3,
    })

    assert result["available"] is False
    assert result["status"] == "invalid_probabilities"


def test_non_finite_probability_is_unavailable_and_json_safe():
    result = normalize_prediction("elo", {
        "home_win": float("nan"), "draw": 0.4, "away_win": 0.6,
    })

    assert result["available"] is False
    assert result["home_win"] is None


def test_not_trained_and_empty_knn_are_unavailable():
    neural = normalize_prediction("neural_net", {
        "home_win": 0.38, "draw": 0.28, "away_win": 0.34,
        "status": "not_trained",
    })
    knn = normalize_prediction("knn_similar", {
        "home_win": 0.35, "draw": 0.30, "away_win": 0.35,
        "neighbors_found": 0,
    })

    assert neural["available"] is False
    assert knn["available"] is False


def test_incompatible_model_feature_dimensions_are_unavailable():
    neural = NeuralNetModel(input_dim=18)
    neural.is_trained = True
    neural_result = normalize_prediction("neural_net", neural.predict(np.zeros(9)))

    class Classifier:
        n_features_in_ = 18

    xgboost = XGBoostModel()
    xgboost.is_trained = True
    xgboost.classifier = Classifier()
    xgboost_result = normalize_prediction("xgboost", xgboost.predict(np.zeros(9)))

    assert neural_result["available"] is False
    assert neural_result["status"] == "incompatible_features"
    assert xgboost_result["available"] is False
    assert xgboost_result["status"] == "incompatible_features"
