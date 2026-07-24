import json
import os
import threading
import time

import pytest

import web.app as web_app
from backtest.tasks import BacktestTaskStore
from backtest.storage import atomic_write_json, atomic_write_text
from data.match_repository import MatchRepository
from data.source_adapters import adapt_fifa_match
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


@pytest.mark.parametrize("path", [
    "/api/refresh_data",
    "/api/sync_fifa",
    "/api/calibrate/run",
])
def test_admin_write_endpoints_reject_get(client, path):
    assert client.get(path).status_code == 405


@pytest.mark.parametrize("path", [
    "/api/refresh_data",
    "/api/sync_fifa",
    "/api/calibrate/run",
    "/api/lottery",
    "/api/lottery/predict/dlt",
])
def test_admin_write_endpoints_require_configured_token(client, monkeypatch, path):
    monkeypatch.delenv("FOOTBALL_ADMIN_TOKEN", raising=False)

    response = client.post(path, json={})

    assert response.status_code == 503
    assert response.get_json()["error_code"] == "ADMIN_TOKEN_NOT_CONFIGURED"


def test_admin_write_endpoint_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setenv("FOOTBALL_ADMIN_TOKEN", "expected-token")

    response = client.post(
        "/api/refresh_data",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    assert response.get_json()["error_code"] == "ADMIN_AUTH_REQUIRED"


def test_refresh_accepts_valid_admin_token(client, monkeypatch):
    monkeypatch.setenv("FOOTBALL_ADMIN_TOKEN", "expected-token")
    monkeypatch.setattr(
        "data.fetcher.load_or_fetch",
        lambda force_refresh=False: {
            "upcoming": [{"home_team": "甲", "away_team": "乙"}],
            "errors": [],
        },
    )

    response = client.post(
        "/api/refresh_data",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_fifa_sync_requires_initialized_match_repository(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FOOTBALL_ADMIN_TOKEN", "expected-token")
    monkeypatch.setenv("FOOTBALL_DB_PATH", str(tmp_path / "missing" / "football.db"))

    response = client.post(
        "/api/sync_fifa",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert response.status_code == 503
    assert response.get_json()["error_code"] == "MATCH_REPOSITORY_NOT_INITIALIZED"


def test_fifa_sync_imports_through_repository(client, monkeypatch, tmp_path):
    database = tmp_path / "football.db"
    MatchRepository(database).initialize()
    monkeypatch.setenv("FOOTBALL_ADMIN_TOKEN", "expected-token")
    monkeypatch.setenv("FOOTBALL_DB_PATH", str(database))
    monkeypatch.setattr(web_app, "_refresh_models_after_history_change", lambda: None)
    record = adapt_fifa_match({
        "IdMatch": "fifa-api-1",
        "Date": "2026-07-20T18:00:00Z",
        "CompetitionName": [{"Description": "FIFA World Cup"}],
        "Home": {"TeamName": [{"Description": "France"}]},
        "Away": {"TeamName": [{"Description": "Spain"}]},
        "HomeTeamScore": 1,
        "AwayTeamScore": 0,
    })
    monkeypatch.setattr(
        "data.fifa_sync.fetch_recent_fifa_source_records",
        lambda days=14: {"records": [record], "fetched": 1, "errors": []},
    )

    response = client.post(
        "/api/sync_fifa",
        headers={"Authorization": "Bearer expected-token"},
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["inserted"] == 1
    assert body["total_history"] == 1
    assert MatchRepository(database).list_matches()[0]["home_team"] == "法国"


def test_fifa_sync_conflict_returns_409_without_writing(client, monkeypatch):
    monkeypatch.setenv("FOOTBALL_ADMIN_TOKEN", "expected-token")
    writes = []

    class RepositoryStub:
        def get_data_quality_report(self):
            return {}

        def create_sync_run(self, *_args, **_kwargs):
            writes.append("sync-run")
            return "sync-run"

        def import_source_records(self, *_args, **_kwargs):
            writes.append("import")
            return {}

    class RuntimeManagerStub:
        def run_update(self, _operation, _reason):
            from prediction import RuntimeRefreshInProgressError
            raise RuntimeRefreshInProgressError("已有运行时刷新任务正在执行")

    monkeypatch.setattr(web_app, "_ensure_runtime_components", lambda: None)
    monkeypatch.setattr(web_app, "_repository", RepositoryStub())
    monkeypatch.setattr(web_app, "_runtime_manager", RuntimeManagerStub())
    monkeypatch.setattr(
        "data.fifa_sync.fetch_recent_fifa_source_records",
        lambda days=14: {"records": [object()], "fetched": 1, "errors": []},
    )

    response = client.post(
        "/api/sync_fifa",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert response.status_code == 409
    assert response.get_json()["error_code"] == "RUNTIME_REFRESH_IN_PROGRESS"
    assert writes == []


def test_real_prediction_exposes_runtime_metadata(monkeypatch, tmp_path):
    database = tmp_path / "football.db"
    repository = MatchRepository(database)
    repository.initialize()
    from data.source_adapters import adapt_legacy_match
    records = [
        adapt_legacy_match({
            "home_team": "拜仁" if index % 2 == 0 else "多特蒙德",
            "away_team": "多特蒙德" if index % 2 == 0 else "拜仁",
            "home_goals": 2,
            "away_goals": 1,
            "league": "德甲",
            "date": f"2026-06-{index + 1:02d}",
        }, source="manual")
        for index in range(8)
    ]
    run_id = repository.create_sync_run("test")
    repository.import_source_records(records, run_id, sync_type="test")
    monkeypatch.setenv("FOOTBALL_DB_PATH", str(database))
    for name, value in (
        ("_initialized", False), ("_runtime_database_path", None),
        ("_repository", None), ("_runtime_manager", None),
        ("_prediction_service", None),
    ):
        monkeypatch.setattr(web_app, name, value)
    test_client = web_app.app.test_client()

    response = test_client.post(
        "/predict",
        json={"home_team": "拜仁", "away_team": "多特蒙德", "league": "德甲"},
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["runtime_snapshot_id"].startswith("runtime-")
    assert body["feature_version"] == "2"
    assert body["runtime"]["weights_source"] == "builtin_v1"
    assert body["predictions"]["xgboost"]["available"] is False
    assert "confidence" in body
    assert web_app.app.extensions["match_repository"] is web_app._repository
    assert web_app.app.extensions["runtime_manager"] is web_app._runtime_manager
    assert web_app.app.extensions["prediction_service"] is web_app._prediction_service

    monkeypatch.setattr(
        web_app, "load_history",
        lambda: (_ for _ in ()).throw(AssertionError("不应读取兼容历史层")),
    )
    h2h = test_client.get(
        "/api/history/h2h?a=拜仁&b=多特蒙德"
    ).get_json()
    assert h2h["count"] == 8

    upcoming = web_app._normalize_upcoming([{
        "home_team": "拜仁", "away_team": "多特蒙德",
        "league": "德甲", "source": "manual",
    }])[0]
    assert upcoming["predictable"] is True
    assert upcoming["home_team_id"]
    assert upcoming["competition_id"] == "bundesliga"


def test_calibration_run_starts_persistent_background_task(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FOOTBALL_ADMIN_TOKEN", "expected-token")
    store = BacktestTaskStore(tmp_path / "backtests")
    monkeypatch.setattr(web_app, "_backtest_store", store)
    run_ids = iter(("bt-web-first", "bt-web-second"))
    monkeypatch.setattr(web_app, "create_run_id", lambda: next(run_ids))
    calls = []

    class Process:
        pid = os.getpid()

    def popen(command, **options):
        calls.append((command, options))
        return Process()

    monkeypatch.setattr(web_app.subprocess, "Popen", popen)

    response = client.post(
        "/api/calibrate/run",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert response.status_code == 202
    assert response.get_json()["run_id"] == "bt-web-first"
    assert calls[0][1]["shell"] is False
    assert "--research-only" in calls[0][0]
    assert "--attempt-id" in calls[0][0]
    assert store.read_status("bt-web-first")["state"] == "running"

    conflict = client.post(
        "/api/calibrate/run",
        headers={"Authorization": "Bearer expected-token"},
    )

    assert conflict.status_code == 409
    assert conflict.get_json()["error_code"] == "BACKTEST_ALREADY_RUNNING"


def test_calibration_resume_rejects_spec_changes_and_uses_original_run(
    client, monkeypatch, tmp_path
):
    monkeypatch.setenv("FOOTBALL_ADMIN_TOKEN", "expected-token")
    store = BacktestTaskStore(tmp_path / "backtests")
    monkeypatch.setattr(web_app, "_backtest_store", store)
    run_id = "bt-interrupted"
    store.reserve(run_id)
    store.interrupt(
        run_id, "BACKTEST_USER_INTERRUPTED", "用户中断", exit_code=130
    )
    store.release(run_id)
    calls = []

    class Process:
        pid = os.getpid()

    monkeypatch.setattr(
        web_app.subprocess, "Popen",
        lambda command, **options: calls.append(command) or Process(),
    )
    invalid = client.post(
        "/api/calibrate/run",
        json={"resume_run_id": run_id, "research_only": True},
        headers={"Authorization": "Bearer expected-token"},
    )
    assert invalid.status_code == 400
    assert invalid.get_json()["error_code"] == "BACKTEST_SPEC_MISMATCH"

    response = client.post(
        "/api/calibrate/run",
        json={"resume_run_id": run_id},
        headers={"Authorization": "Bearer expected-token"},
    )
    assert response.status_code == 202
    assert calls[0][calls[0].index("--resume") + 1] == run_id
    assert "--database" not in calls[0]
    assert "--attempt-id" in calls[0]


def test_calibration_datasets_are_public_and_do_not_expose_paths(
    client, monkeypatch
):
    monkeypatch.setattr(web_app, "_ensure_runtime_components", lambda: None)
    monkeypatch.setattr(web_app._repository, "list_dataset_batches", lambda: [{
        "batch_id": "fd-example", "source": "football-data.co.uk",
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
        "member_count": 1200, "expected_member_count": 1200,
        "membership_status": "complete",
        "manifest": {
            "files": [{"division": "D1", "season_code": "2425"}],
            "internal_path": "D:/secret/data.csv",
        },
    }])
    monkeypatch.setattr(
        web_app._repository, "build_data_readiness_report",
        lambda **_kwargs: {"status": "ready"},
    )

    response = client.get("/api/calibration/datasets")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["datasets"][0]["formal_eligible"] is True
    assert "manifest" not in payload["datasets"][0]
    assert "path" not in json.dumps(payload)


def test_completed_backtest_report_is_loaded_by_run_id(client, monkeypatch, tmp_path):
    store = BacktestTaskStore(tmp_path / "backtests")
    monkeypatch.setattr(web_app, "_backtest_store", store)
    run_id = "bt-completed"
    store.reserve(run_id)
    run_dir = store.run_dir(run_id)
    atomic_write_json(run_dir / "manifest.json", {"run_id": run_id})
    atomic_write_json(run_dir / "metrics.json", {"holdout": {}})
    atomic_write_json(run_dir / "admission.json", {"decisions": {}})
    atomic_write_text(run_dir / "report.md", "# 报告\n")
    store.complete(run_id, 2, "insufficient_data")
    store.release(run_id)

    response = client.get(f"/api/calibrate/report?run_id={run_id}")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["run"]["outcome"] == "insufficient_data"
    assert "pid" not in payload["run"]
    assert payload["manifest"]["run_id"] == run_id
    assert payload["report_markdown"] == "# 报告\n"
