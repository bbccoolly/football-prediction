import threading
import time

import pytest

import web.app as web_app
from ensemble.prediction_contract import NoAvailableModelsError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(web_app, "_init_models", lambda: None)
    return web_app.app.test_client()


def test_invalid_json_returns_stable_error(client):
    response = client.post("/predict", data="not-json", content_type="text/plain")

    assert response.status_code == 400
    assert response.get_json()["error_code"] == "INVALID_JSON"


@pytest.mark.parametrize("payload,code", [
    ({}, "MISSING_TEAMS"),
    ({"home_team": "甲", "away_team": "甲"}, "SAME_TEAM"),
    ({"home_team": "甲", "away_team": "乙", "home_odds": 2.0}, "INVALID_ODDS"),
    ({
        "home_team": "甲", "away_team": "乙",
        "home_odds": 1.0, "draw_odds": 3.0, "away_odds": 4.0,
    }, "INVALID_ODDS"),
])
def test_invalid_inputs(client, payload, code):
    response = client.post("/predict", json=payload)

    assert response.status_code == 400
    assert response.get_json()["error_code"] == code


def test_no_available_models_returns_503(client, monkeypatch):
    monkeypatch.setattr(
        web_app,
        "_run_predictions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            NoAvailableModelsError("当前没有可用预测模型")
        ),
    )

    response = client.post("/predict", json={"home_team": "甲", "away_team": "乙"})

    assert response.status_code == 503
    assert response.get_json()["error_code"] == "NO_AVAILABLE_MODELS"


def test_predict_keeps_compatibility_fields(client, monkeypatch):
    result = {
        "predictions": {},
        "ensemble": {
            "home_win": 0.4, "draw": 0.3, "away_win": 0.3,
            "expected_total_goals": 2.5, "top_scores": [],
            "weights": {}, "effective_weights": {},
        },
        "model_agreement": 82.5,
        "model_summary": {
            "total_models": 0, "available_models": 0, "excluded_models": 0,
            "unknown_quality_models": 0, "using_defaults_models": 0,
        },
        "warnings": [], "squad_info": {}, "htft": {}, "handicap": {},
    }
    monkeypatch.setattr(web_app, "_run_predictions", lambda *_args, **_kwargs: result)

    response = client.post("/predict", json={"home_team": "甲", "away_team": "乙"})
    body = response.get_json()

    assert response.status_code == 200
    assert body["model_agreement"] == 82.5
    assert body["confidence"] == 82.5
    assert "weights" in body["ensemble"]


def test_model_initialization_is_locked(monkeypatch):
    calls = []
    monkeypatch.setattr(web_app, "_initialized", False)

    def initialize():
        calls.append("called")
        time.sleep(0.05)
        web_app._initialized = True

    monkeypatch.setattr(web_app, "_initialize_models_unlocked", initialize)
    threads = [threading.Thread(target=web_app._init_models) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == ["called"]


def test_predict_and_debug_use_same_prediction_pipeline(client, monkeypatch):
    calls = []
    result = {
        "predictions": {
            "poisson": {
                "home_win": 0.4, "draw": 0.3, "away_win": 0.3,
                "available": True, "status": "ready",
            }
        },
        "ensemble": {
            "home_win": 0.4, "draw": 0.3, "away_win": 0.3,
            "expected_total_goals": 2.5, "top_scores": [],
            "weights": {"poisson": 1.0},
            "effective_weights": {"poisson": 1.0},
        },
        "model_agreement": 0.0,
        "model_summary": {
            "total_models": 1, "available_models": 1, "excluded_models": 0,
            "unknown_quality_models": 1, "using_defaults_models": 0,
        },
        "warnings": ["insufficient_models_for_agreement"],
        "squad_info": {}, "htft": {}, "handicap": {},
    }

    def run_pipeline(context, report_progress=None):
        calls.append(context["home_team"])
        return result

    monkeypatch.setattr(web_app, "_run_predictions", run_pipeline)
    payload = {"home_team": "甲", "away_team": "乙", "neutral": True}

    prediction = client.post("/predict", json=payload).get_json()
    debug = client.post("/api/debug_predict", json=payload).get_json()

    assert calls == ["甲", "甲"]
    assert prediction["ensemble"] == debug["ensemble"]
    assert debug["model_outputs"]["poisson"]["home_win"] == 0.4
