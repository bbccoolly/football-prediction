"""Public contracts for the shared prediction runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from ensemble.prediction_contract import NoAvailableModelsError


class PredictionRuntimeError(RuntimeError):
    code = "PREDICTION_RUNTIME_ERROR"


class InvalidPredictionRequestError(PredictionRuntimeError, ValueError):
    def __init__(self, message: str, code: str = "INVALID_PREDICTION_REQUEST"):
        super().__init__(message)
        self.code = code


class RuntimeNotReadyError(PredictionRuntimeError):
    code = "RUNTIME_NOT_READY"


class SnapshotTimeMismatchError(PredictionRuntimeError):
    code = "SNAPSHOT_TIME_MISMATCH"


class RuntimeRefreshInProgressError(PredictionRuntimeError):
    code = "RUNTIME_REFRESH_IN_PROGRESS"


class ModelExecutionError(PredictionRuntimeError):
    code = "MODEL_EXECUTION_FAILED"


def ensure_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InvalidPredictionRequestError(
                "预测时点必须是有效的 ISO-8601 时间", "INVALID_PREDICTED_AT"
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidPredictionRequestError(
            "预测时点必须包含时区", "INVALID_PREDICTED_AT"
        )
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class OddsSnapshot:
    home_odds: float
    draw_odds: float
    away_odds: float
    captured_at: datetime
    source: str = "manual"

    def __post_init__(self):
        import math

        for value in (self.home_odds, self.draw_odds, self.away_odds):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 1.0
            ):
                raise InvalidPredictionRequestError(
                    "赔率必须是大于 1 的有限数字", "INVALID_ODDS"
                )
        object.__setattr__(self, "captured_at", ensure_utc(self.captured_at))


@dataclass(frozen=True)
class PredictionRequest:
    home_team_id: str
    away_team_id: str
    competition_id: str
    predicted_at: datetime
    neutral: bool = False
    match_id: str | None = None
    odds: OddsSnapshot | None = None
    home_missing: tuple[str, ...] = ()
    away_missing: tuple[str, ...] = ()

    def __post_init__(self):
        for field_name in ("home_team_id", "away_team_id", "competition_id"):
            if not str(getattr(self, field_name) or "").strip():
                raise InvalidPredictionRequestError(
                    "主队、客队和赛事 ID 不能为空", "MISSING_IDENTIFIERS"
                )
        if self.home_team_id == self.away_team_id:
            raise InvalidPredictionRequestError("主客队不能相同", "SAME_TEAM")
        if not isinstance(self.neutral, bool):
            raise InvalidPredictionRequestError(
                "neutral 必须是布尔值", "INVALID_NEUTRAL"
            )
        object.__setattr__(self, "predicted_at", ensure_utc(self.predicted_at))
        object.__setattr__(self, "home_missing", tuple(self.home_missing))
        object.__setattr__(self, "away_missing", tuple(self.away_missing))
        if self.odds and self.odds.captured_at > self.predicted_at:
            raise InvalidPredictionRequestError(
                "赔率采集时间不能晚于预测时点", "INVALID_ODDS_TIME"
            )


@dataclass(frozen=True)
class ModelArtifactMetadata:
    artifact_schema_version: int
    model_id: str
    model_version: str
    feature_version: str
    feature_names: tuple[str, ...]
    trained_from: str
    trained_until: str
    training_sample_count: int
    training_data_fingerprint: str
    parameter_fingerprint: str
    code_commit: str
    artifact_format: str
    files: tuple[Mapping[str, str], ...]


@dataclass(frozen=True)
class ModelStatus:
    model_id: str
    status: str
    reason: str | None = None
    model_version: str = "1"
    feature_version: str | None = None
    training_sample_count: int = 0
    trained_from: str | None = None
    trained_until: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return {
            "model_id": self.model_id,
            "status": self.status,
            "reason": self.reason,
            "model_version": self.model_version,
            "feature_version": self.feature_version,
            "training_sample_count": self.training_sample_count,
            "trained_from": self.trained_from,
            "trained_until": self.trained_until,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TeamTypeRuntime:
    team_type: str
    models: Mapping[str, Any]
    team_match_counts: Mapping[str, int]
    matches: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class CompetitionRuntime:
    competition_id: str
    competition_name: str
    models: Mapping[str, Any]
    team_match_counts: Mapping[str, int]
    massey_components: Mapping[str, int]
    knn_sample_count: int
    matches: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ModelRuntimeSnapshot:
    snapshot_id: str
    built_at: str
    data_as_of: str
    trained_from: str | None
    trained_until: str | None
    training_sample_count: int
    data_fingerprint: str
    runtime_config_fingerprint: str
    weights_fingerprint: str
    weights_source: str
    code_commit: str
    feature_version: str
    feature_names: tuple[str, ...]
    team_type_models: Mapping[str, TeamTypeRuntime]
    competition_models: Mapping[str, CompetitionRuntime]
    model_statuses: Mapping[str, ModelStatus]
    weights: Mapping[str, float]
    data_quality: Mapping[str, Any]
    warnings: tuple[str, ...] = ()

    def public_metadata(self):
        return {
            "runtime_snapshot_id": self.snapshot_id,
            "built_at": self.built_at,
            "data_as_of": self.data_as_of,
            "trained_from": self.trained_from,
            "trained_until": self.trained_until,
            "training_sample_count": self.training_sample_count,
            "data_fingerprint": self.data_fingerprint,
            "runtime_config_fingerprint": self.runtime_config_fingerprint,
            "weights_fingerprint": self.weights_fingerprint,
            "weights_source": self.weights_source,
            "code_commit": self.code_commit,
            "feature_version": self.feature_version,
            "data_quality": dict(self.data_quality),
            "models": {
                key: status.to_dict() for key, status in self.model_statuses.items()
            },
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PredictionResult:
    predictions: Mapping[str, Any]
    ensemble: Mapping[str, Any]
    simulation: Mapping[str, Any]
    model_agreement: float
    model_summary: Mapping[str, Any]
    warnings: tuple[str, ...]
    data_quality: Mapping[str, Any]
    squad_info: Mapping[str, Any]
    htft: Mapping[str, Any]
    handicap: Mapping[str, Any]
    prediction_run_id: str
    snapshot: ModelRuntimeSnapshot
    trace: Mapping[str, Any] | None = None

    def to_dict(self):
        result = {
            "predictions": dict(self.predictions),
            "ensemble": dict(self.ensemble),
            "simulation": dict(self.simulation),
            "model_agreement": self.model_agreement,
            "confidence": self.model_agreement,
            "model_summary": dict(self.model_summary),
            "warnings": list(self.warnings),
            "data_quality": dict(self.data_quality),
            "squad_info": dict(self.squad_info),
            "htft": dict(self.htft),
            "handicap": dict(self.handicap),
            "prediction_run_id": self.prediction_run_id,
            "runtime_snapshot_id": self.snapshot.snapshot_id,
            "data_fingerprint": self.snapshot.data_fingerprint,
            "runtime_config_fingerprint": self.snapshot.runtime_config_fingerprint,
            "weights_fingerprint": self.snapshot.weights_fingerprint,
            "feature_version": self.snapshot.feature_version,
            "runtime": self.snapshot.public_metadata(),
        }
        if self.trace is not None:
            result["trace"] = dict(self.trace)
        return result
