import json

import pytest

from ensemble.bma import BayesianModelAveraging
from ensemble.prediction_contract import NoAvailableModelsError


def _prediction(home, draw, away, **extra):
    return {"home_win": home, "draw": draw, "away_win": away, **extra}


def test_load_migrates_legacy_knn_key(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({"weights": {"poisson": 0.6, "knn": 0.4}}), encoding="utf-8")
    bma = BayesianModelAveraging(weights_file=path)

    assert bma.load() is True
    assert bma.get_weights()["knn_similar"] == 0.4
    assert "knn" not in bma.get_weights()
    assert "knn_key_migrated" in bma.load_warnings
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 3
    assert "knn" not in saved["weights"]


def test_new_knn_key_wins_when_both_exist(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({"weights": {"knn": 0.8, "knn_similar": 0.2}}), encoding="utf-8")
    bma = BayesianModelAveraging(weights_file=path)

    bma.load()

    assert bma.get_weights()["knn_similar"] == 0.2


def test_blend_excludes_unavailable_models_and_normalizes_weights():
    bma = BayesianModelAveraging()
    bma.weights = {"poisson": 0.6, "neural_net": 0.4}

    result = bma.blend({
        "poisson": _prediction(0.5, 0.3, 0.2, expected_total_goals=2.4),
        "neural_net": _prediction(0.38, 0.28, 0.34, status="not_trained"),
    })

    assert result["effective_weights"] == {"poisson": 1.0}
    assert result["home_win"] == 0.5
    assert result["excluded_models"][0]["model_id"] == "neural_net"


def test_invalid_model_does_not_break_valid_ensemble():
    bma = BayesianModelAveraging()
    result = bma.blend({
        "poisson": _prediction(0.5, 0.3, 0.2),
        "elo": _prediction(float("nan"), 0.4, 0.6),
    })

    assert result["effective_weights"] == {"poisson": 1.0}


def test_no_available_models_raises():
    bma = BayesianModelAveraging()

    with pytest.raises(NoAvailableModelsError):
        bma.blend({
            "neural_net": _prediction(0.38, 0.28, 0.34, status="not_trained"),
        })


def test_corrupt_weights_fall_back_without_overwriting(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text("{broken", encoding="utf-8")
    bma = BayesianModelAveraging(weights_file=path)

    assert bma.load() is False
    assert path.read_text(encoding="utf-8") == "{broken"
    assert "weights_file_invalid" in bma.load_warnings


def test_load_disables_legacy_monte_carlo_weight(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "weights": {"poisson": 0.5, "monte_carlo": 0.5},
    }), encoding="utf-8")
    bma = BayesianModelAveraging(weights_file=path)

    assert bma.load() is True
    assert bma.get_weights()["monte_carlo"] == 0.0
    assert "monte_carlo_weight_disabled" in bma.load_warnings


def test_blend_excludes_derived_monte_carlo_even_if_weight_is_positive():
    bma = BayesianModelAveraging()
    bma.weights = {"poisson": 0.5, "monte_carlo": 0.5}

    result = bma.blend({
        "poisson": _prediction(0.5, 0.3, 0.2),
        "monte_carlo": _prediction(0.7, 0.2, 0.1, role="derived"),
    })

    assert result["effective_weights"] == {"poisson": 1.0}
    assert result["home_win"] == 0.5
    assert result["excluded_models"][0]["model_id"] == "monte_carlo"
    assert result["excluded_models"][0]["reason"] == "derived_output"
