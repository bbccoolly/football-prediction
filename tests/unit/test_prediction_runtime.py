from datetime import datetime, timezone

import pytest

from data.match_repository import MatchRepository
from data.source_adapters import adapt_legacy_match
from features.builder import FeatureBuilder
from prediction import ModelRuntimeBuilder, PredictionService, RuntimeManager
from prediction.contracts import (
    InvalidPredictionRequestError,
    RuntimeRefreshInProgressError,
    SnapshotTimeMismatchError,
)
from prediction.runtime import _batch_matches, _build_rolling_knn


def _repository_with_matches(tmp_path, count=8):
    repository = MatchRepository(tmp_path / "football.db")
    repository.initialize()
    records = []
    for index in range(count):
        records.append(adapt_legacy_match({
            "home_team": "拜仁" if index % 2 == 0 else "多特蒙德",
            "away_team": "多特蒙德" if index % 2 == 0 else "拜仁",
            "home_goals": 2 if index % 3 else 1,
            "away_goals": 1,
            "league": "德甲",
            "date": f"2026-06-{index + 1:02d}",
        }, source="manual"))
    run_id = repository.create_sync_run("test")
    repository.import_source_records(records, run_id, sync_type="test")
    return repository


def test_feature_builder_uses_real_massey_difference():
    result = FeatureBuilder().build(
        elo_home=1500, elo_away=1450,
        atk_home=1, atk_away=1, def_home=1, def_away=1,
        form_home={}, form_away={}, h2h_stats={},
        squad_home=1, squad_away=1, home_adv=0.4, neutral=False,
        massey_home=1.25, massey_away=-0.25,
    )

    assert FeatureBuilder.FEATURE_VERSION == "2"
    assert isinstance(FeatureBuilder.FEATURE_NAMES, tuple)
    assert result["massey_diff"] == 1.5
    assert result["vector"].shape == (18,)
    assert result["home_ppg"] == pytest.approx(1 / 3)
    assert result["h2h_home_win_rate"] == pytest.approx(1 / 3)
    assert result["h2h_draw_rate"] == pytest.approx(1 / 3)


def test_knn_batches_follow_event_time_without_same_batch_leakage():
    matches = [
        {
            "match_id": "late",
            "event_date": "2026-06-03",
            "kickoff_utc": "2026-06-03T18:00:00+00:00",
            "time_precision": "minute",
        },
        {
            "match_id": "early-a",
            "event_date": "2026-06-01",
            "kickoff_utc": "2026-06-01T12:00:00+00:00",
            "time_precision": "minute",
        },
        {
            "match_id": "date-only",
            "event_date": "2026-06-02",
            "kickoff_utc": None,
            "time_precision": "date",
        },
        {
            "match_id": "same-day-minute",
            "event_date": "2026-06-02",
            "kickoff_utc": "2026-06-02T20:00:00+00:00",
            "time_precision": "minute",
        },
        {
            "match_id": "early-b",
            "event_date": "2026-06-01",
            "kickoff_utc": "2026-06-01T12:00:00+00:00",
            "time_precision": "minute",
        },
    ]

    batches = [
        [match["match_id"] for match in batch]
        for batch in _batch_matches(matches)
    ]

    assert batches == [
        ["early-a", "early-b"],
        ["date-only", "same-day-minute"],
        ["late"],
    ]


def test_knn_first_batch_uses_frozen_missing_value_defaults():
    knn = _build_rolling_knn([{
        "match_id": "first",
        "home_team": "拜仁",
        "away_team": "多特蒙德",
        "home_goals": 1,
        "away_goals": 0,
        "league": "德甲",
        "event_date": "2026-06-01",
        "kickoff_utc": None,
        "time_precision": "date",
        "neutral": False,
    }], FeatureBuilder())
    vector = knn.match_features[0]["features"]
    home_ppg_index = FeatureBuilder.FEATURE_NAMES.index("home_ppg")
    away_ppg_index = FeatureBuilder.FEATURE_NAMES.index("away_ppg")

    assert vector[home_ppg_index] == pytest.approx(1 / 3)
    assert vector[away_ppg_index] == pytest.approx(1 / 3)


def test_runtime_is_scoped_and_snapshot_id_is_deterministic(tmp_path):
    repository = _repository_with_matches(tmp_path)
    builder = ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)

    first = builder.build(cutoff)
    second = builder.build(cutoff)

    assert first.snapshot_id == second.snapshot_id
    assert "club" in first.team_type_models
    assert "bundesliga" in first.competition_models
    assert "world_cup" not in first.competition_models
    assert first.competition_models["bundesliga"].knn_sample_count == 8
    assert first.data_quality == {
        "finished_matches": 8,
        "accepted_training_matches": 8,
        "excluded_team_type_matches": 0,
    }


def test_runtime_excludes_matches_at_or_after_as_of(tmp_path):
    repository = _repository_with_matches(tmp_path)
    snapshot = ModelRuntimeBuilder(
        repository, artifact_root=tmp_path / "models"
    ).build(datetime(2026, 6, 5, tzinfo=timezone.utc))

    assert snapshot.training_sample_count == 4
    assert snapshot.trained_until == "2026-06-04"


def test_minimum_evidence_does_not_promote_default_features(tmp_path):
    repository = _repository_with_matches(tmp_path, count=1)
    manager = RuntimeManager(
        ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    )
    manager.initialize()
    service = PredictionService(repository, manager)
    request = service.request_from_payload({
        "home_team": "拜仁", "away_team": "多特蒙德", "league": "德甲",
    })

    result = service.predict(request)

    assert result.predictions["elo"]["available"] is True
    for model_id in ("poisson", "dixon_coles", "form", "bayesian"):
        assert result.predictions[model_id]["status"] == "insufficient_evidence"
        assert result.predictions[model_id]["available"] is False


def test_explicit_unknown_team_id_does_not_fall_back_to_name(tmp_path):
    repository = _repository_with_matches(tmp_path)
    manager = RuntimeManager(
        ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    )
    manager.initialize()
    service = PredictionService(repository, manager)

    with pytest.raises(InvalidPredictionRequestError) as exc_info:
        service.request_from_payload({
            "home_team_id": "unknown-id", "home_team": "拜仁",
            "away_team": "多特蒙德", "league": "德甲",
        })

    assert exc_info.value.code == "UNKNOWN_TEAM"


def test_prediction_uses_evidence_gates_and_builtin_weights(tmp_path):
    repository = _repository_with_matches(tmp_path)
    manager = RuntimeManager(
        ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    )
    manager.initialize()
    service = PredictionService(repository, manager)
    request = service.request_from_payload({
        "home_team": "拜仁", "away_team": "多特蒙德", "league": "德甲",
    })

    result = service.predict(request)

    assert result.snapshot.weights_source == "builtin_v1"
    assert result.predictions["poisson"]["available"] is True
    assert result.predictions["xgboost"]["available"] is False
    assert result.predictions["monte_carlo"]["status"] == "derived"
    assert result.ensemble["effective_weights"].get("xgboost") is None


def test_mixed_team_types_are_rejected(tmp_path):
    repository = _repository_with_matches(tmp_path)
    manager = RuntimeManager(
        ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    )
    manager.initialize()
    service = PredictionService(repository, manager)

    with pytest.raises(InvalidPredictionRequestError) as exc_info:
        service.request_from_payload({
            "home_team": "拜仁", "away_team": "法国", "league": "世界杯",
        })

    assert exc_info.value.code == "MIXED_TEAM_TYPES"


def test_explicit_prediction_time_cannot_precede_snapshot(tmp_path):
    repository = _repository_with_matches(tmp_path)
    manager = RuntimeManager(
        ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    )
    manager.initialize()
    service = PredictionService(repository, manager)
    request = service.request_from_payload({
        "home_team": "拜仁", "away_team": "多特蒙德", "league": "德甲",
        "predicted_at": "2026-01-01T00:00:00+00:00",
    })

    with pytest.raises(SnapshotTimeMismatchError):
        service.predict(request)


def test_failed_refresh_keeps_previous_snapshot(tmp_path, monkeypatch):
    repository = _repository_with_matches(tmp_path)
    builder = ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    manager = RuntimeManager(builder)
    previous = manager.initialize()

    def fail_build(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(builder, "build", fail_build)

    with pytest.raises(RuntimeError, match="boom"):
        manager.refresh("test")
    assert manager.current() is previous
    assert manager.status()["last_refresh_error"]["code"] == "RUNTIME_REFRESH_FAILED"


def test_successful_refresh_replaces_snapshot_as_one_unit(tmp_path):
    repository = _repository_with_matches(tmp_path)
    manager = RuntimeManager(
        ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    )
    previous = manager.initialize()
    record = adapt_legacy_match({
        "home_team": "拜仁", "away_team": "多特蒙德",
        "home_goals": 3, "away_goals": 0, "league": "德甲",
        "date": "2026-06-20",
    }, source="manual")
    run_id = repository.create_sync_run("refresh-test")
    repository.import_source_records([record], run_id, sync_type="refresh-test")

    refreshed = manager.refresh("test")

    assert refreshed.status == "ok"
    assert manager.current() is not previous
    assert manager.current().training_sample_count == 9
    assert manager.current().data_fingerprint != previous.data_fingerprint


def test_database_change_during_build_keeps_previous_snapshot(tmp_path, monkeypatch):
    repository = _repository_with_matches(tmp_path)
    builder = ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    manager = RuntimeManager(builder)
    previous = manager.initialize()
    original_build = builder.build

    def build_then_change_database(*args, **kwargs):
        snapshot = original_build(*args, **kwargs)
        record = adapt_legacy_match({
            "home_team": "拜仁", "away_team": "多特蒙德",
            "home_goals": 3, "away_goals": 2, "league": "德甲",
            "date": "2026-06-21",
        }, source="manual")
        run_id = repository.create_sync_run("race-test")
        repository.import_source_records([record], run_id, sync_type="race-test")
        return snapshot

    monkeypatch.setattr(builder, "build", build_then_change_database)

    with pytest.raises(RuntimeError, match="database_changed_during_runtime_build"):
        manager.refresh("race-test")

    assert manager.current() is previous
    assert manager.status()["runtime_stale"] is True


def test_refresh_lock_rejects_concurrent_refresh_before_update(tmp_path):
    repository = _repository_with_matches(tmp_path)
    manager = RuntimeManager(
        ModelRuntimeBuilder(repository, artifact_root=tmp_path / "models")
    )
    manager.initialize()
    manager._refresh_lock.acquire()
    called = False

    def update():
        nonlocal called
        called = True

    try:
        with pytest.raises(RuntimeRefreshInProgressError):
            manager.run_update(update, "test")
    finally:
        manager._refresh_lock.release()
    assert called is False
