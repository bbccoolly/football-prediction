"""Build immutable model snapshots and swap them atomically."""

from __future__ import annotations

import os
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from config import (
    DIXON_COLES_RHO,
    DIXON_COLES_XI,
    ELO_HOME_BONUS,
    ELO_INITIAL,
    ELO_K,
    ELO_SCALE,
    FORM_DECAY,
    FORM_MATCHES,
    H2H_MAX_MATCHES,
    H2H_YEAR_LIMIT,
    HOME_ADVANTAGE,
    INITIAL_WEIGHTS,
    KNN_K,
    MC_SIMULATIONS,
    MODEL_DIR,
    POISSON_LEAGUE_AVG_GOALS,
)
from data.match_repository import MatchRepository, canonical_json, fingerprint, normalize_timestamp
from ensemble.prediction_contract import normalize_prediction
from features.builder import FeatureBuilder
from models.bayesian_hierarchical import BayesianHierarchicalModel
from models.dixon_coles import DixonColesModel
from models.elo import EloRating
from models.form import FormModel
from models.head_to_head import HeadToHeadModel
from models.knn_similar import KNNSimilarModel
from models.massey import MasseyRanking
from models.market_odds import MarketOddsModel
from models.monte_carlo import MonteCarloModel
from models.poisson import PoissonModel, build_strengths_from_results
from prediction.artifacts import ModelArtifactInspector
from prediction.contracts import (
    CompetitionRuntime,
    ModelRuntimeSnapshot,
    ModelStatus,
    RuntimeNotReadyError,
    RuntimeRefreshInProgressError,
    TeamTypeRuntime,
)


RUNTIME_PROTOCOL_VERSION = 1
MIN_TEAM_MATCHES = {
    "elo": 1,
    "poisson": 5,
    "dixon_coles": 5,
    "form": 5,
    "bayesian": 5,
    "head_to_head": 1,
    "knn_similar": 1,
}
MIN_KNN_SAMPLES = 5


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | str) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return normalize_timestamp(value)


def _match_time(match):
    return str(match.get("kickoff_utc") or match.get("event_date") or match.get("date") or "")


def _training_data_fingerprint(matches):
    return fingerprint([{
        key: match.get(key)
        for key in (
            "match_id", "competition_id", "event_date", "kickoff_utc",
            "time_precision", "home_team_id", "away_team_id", "neutral",
            "status", "home_goals", "away_goals",
        )
    } for match in matches])


def _before_cutoff(match, cutoff):
    if match.get("kickoff_utc"):
        return normalize_timestamp(match["kickoff_utc"]) < cutoff
    return str(match.get("event_date") or "") < cutoff[:10]


def _team_counts(matches):
    counts = Counter()
    for match in matches:
        counts[match["home_team"]] += 1
        counts[match["away_team"]] += 1
    return dict(counts)


def _components(matches):
    graph: dict[str, set[str]] = defaultdict(set)
    for match in matches:
        home, away = match["home_team"], match["away_team"]
        graph[home].add(away)
        graph[away].add(home)
    result = {}
    component = 0
    for team in sorted(graph):
        if team in result:
            continue
        pending = [team]
        while pending:
            current = pending.pop()
            if current in result:
                continue
            result[current] = component
            pending.extend(graph[current] - result.keys())
        component += 1
    return result


def _h2h_feature(stats, global_average=2.7):
    if stats.get("total_matches", 0) > 0:
        return stats
    return {
        **stats,
        "a_win_rate": 1.0 / 3.0,
        "draw_rate": 1.0 / 3.0,
        "b_win_rate": 1.0 / 3.0,
        "avg_goals": global_average,
    }


def _batch_matches(matches):
    date_only_days = {
        match["event_date"] for match in matches if match.get("time_precision") == "date"
    }
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for match in matches:
        if match["event_date"] in date_only_days:
            key = (match["event_date"], "")
        else:
            key = (match["event_date"], match.get("kickoff_utc") or "")
        grouped[key].append(match)
    return [grouped[key] for key in sorted(grouped)]


def _build_rolling_knn_rows(matches, feature_builder):
    rows = []
    prefix: list[dict] = []
    elo = EloRating(storage_path=os.devnull)
    form = FormModel()
    h2h = HeadToHeadModel()
    for batch in _batch_matches(matches):
        strengths = build_strengths_from_results(prefix) if prefix else {}
        massey = MasseyRanking()
        massey.fit(prefix)
        average_goals = (
            sum(m["home_goals"] + m["away_goals"] for m in prefix) / len(prefix)
            if prefix else 2.7
        )
        pending = []
        for match in batch:
            home, away = match["home_team"], match["away_team"]
            home_strength = strengths.get(home, {"attack": 1.0, "defense": 1.0})
            away_strength = strengths.get(away, {"attack": 1.0, "defense": 1.0})
            features = feature_builder.build(
                elo_home=elo.get_rating(home),
                elo_away=elo.get_rating(away),
                atk_home=home_strength["attack"],
                atk_away=away_strength["attack"],
                def_home=home_strength["defense"],
                def_away=away_strength["defense"],
                form_home=form.get_form_score(home),
                form_away=form.get_form_score(away),
                h2h_stats=_h2h_feature(h2h.get_h2h(home, away), average_goals),
                squad_home=1.0,
                squad_away=1.0,
                home_adv=HOME_ADVANTAGE.get(match.get("league"), HOME_ADVANTAGE["default"]),
                neutral=bool(match.get("neutral")),
                massey_home=massey.ratings.get(home, 0.0),
                massey_away=massey.ratings.get(away, 0.0),
            )
            pending.append((features["vector"], match))
        for vector, match in pending:
            rows.append({
                "match_id": match.get("match_id"),
                "competition_id": match.get("competition_id"),
                "features": tuple(float(value) for value in vector),
                "home_goals": match["home_goals"],
                "away_goals": match["away_goals"],
            })
        for match in batch:
            elo.update(
                match["home_team"], match["away_team"], match["home_goals"],
                match["away_goals"], bool(match.get("neutral")),
                match_id=match.get("match_id", ""),
            )
        form.load_history(batch)
        h2h.load_history(batch)
        prefix.extend(batch)
    return tuple(rows)


def _knn_from_rows(rows):
    knn = KNNSimilarModel()
    for row in rows:
        knn.add_match(row["features"], row["home_goals"], row["away_goals"])
    return knn


def _build_rolling_knn(matches, feature_builder):
    return _knn_from_rows(_build_rolling_knn_rows(matches, feature_builder))


class HistoricalFeatureIndex:
    """Precomputed, leakage-safe KNN training rows for monotonic cutoffs."""

    def __init__(self, rows_by_competition, data_fingerprint):
        self.rows_by_competition = MappingProxyType({
            key: tuple(value) for key, value in rows_by_competition.items()
        })
        self.data_fingerprint = data_fingerprint
        self.feature_version = FeatureBuilder.FEATURE_VERSION

    @classmethod
    def build(cls, matches, feature_builder=None):
        feature_builder = feature_builder or FeatureBuilder()
        grouped: dict[str, list[dict]] = defaultdict(list)
        for match in matches:
            grouped[match["competition_id"]].append(match)
        rows = {
            competition_id: _build_rolling_knn_rows(scoped, feature_builder)
            for competition_id, scoped in grouped.items()
        }
        payload = [{
            key: match.get(key)
            for key in (
                "match_id", "competition_id", "event_date", "kickoff_utc",
                "time_precision", "home_team_id", "away_team_id",
                "home_goals", "away_goals", "neutral",
            )
        } for match in matches]
        return cls(rows, fingerprint({
            "feature_version": FeatureBuilder.FEATURE_VERSION,
            "matches": payload,
        }))

    def rows_for(self, competition_id, matches):
        match_ids = {match["match_id"] for match in matches}
        return tuple(
            row
            for row in self.rows_by_competition.get(competition_id, ())
            if row["match_id"] in match_ids
        )


@dataclass(frozen=True)
class RefreshResult:
    status: str
    reason: str
    previous_snapshot_id: str | None
    snapshot_id: str | None
    runtime_stale: bool
    error_code: str | None = None
    error: str | None = None

    def to_dict(self):
        return self.__dict__.copy()


class ModelRuntimeBuilder:
    def __init__(
        self,
        repository: MatchRepository,
        artifact_root: str | Path = MODEL_DIR,
        *,
        artifact_inspector=None,
        historical_feature_index: HistoricalFeatureIndex | None = None,
        code_commit: str | None = None,
    ):
        self.repository = repository
        self.feature_builder = FeatureBuilder()
        self.artifact_inspector = artifact_inspector or ModelArtifactInspector(artifact_root)
        self.historical_feature_index = historical_feature_index
        self.code_commit = code_commit

    def build(self, as_of: datetime | str | None = None) -> ModelRuntimeSnapshot:
        cutoff = _iso(as_of or utc_now())
        training_filters = {
            "status": "finished", "data_quality_status": "valid",
        }
        matches = self.repository.list_matches(training_filters, as_of=cutoff)
        data_fingerprint = self.repository.build_data_fingerprint(
            training_filters, as_of=cutoff
        )
        return self._build_snapshot(matches, cutoff, data_fingerprint)

    def build_from_matches(self, matches, as_of: datetime | str) -> ModelRuntimeSnapshot:
        cutoff = _iso(as_of)
        scoped = [
            dict(match) for match in matches
            if match.get("status") == "finished"
            and match.get("data_quality_status", "valid") == "valid"
            and _before_cutoff(match, cutoff)
        ]
        scoped.sort(key=lambda match: (
            match["event_date"], match.get("kickoff_utc") or "", match["match_id"]
        ))
        return self._build_snapshot(
            scoped, cutoff, _training_data_fingerprint(scoped)
        )

    def _build_snapshot(self, matches, cutoff, data_fingerprint):
        valid = [
            match for match in matches
            if match.get("home_team_type") == match.get("away_team_type")
            and match.get("home_team_type") in {"national", "club"}
        ]
        if not valid:
            raise RuntimeNotReadyError("预测运行时没有可用的完场比赛")

        team_type_models = self._build_team_type_models(valid)
        competition_models = self._build_competition_models(valid)
        weights = dict(INITIAL_WEIGHTS)
        weights["monte_carlo"] = 0.0
        weights_fingerprint = fingerprint(weights)
        code_commit = self.code_commit or os.environ.get("FOOTBALL_CODE_COMMIT", "unknown")
        model_parameters = {
            "elo": {
                "initial": ELO_INITIAL, "k": ELO_K,
                "home_bonus": ELO_HOME_BONUS, "scale": ELO_SCALE,
            },
            "poisson": {
                "league_average_goals": POISSON_LEAGUE_AVG_GOALS,
                "home_factor": 1.15, "maximum_goals": PoissonModel.MAX_GOALS,
            },
            "dixon_coles": {
                "rho": DIXON_COLES_RHO, "xi": DIXON_COLES_XI,
                "league_average_goals": POISSON_LEAGUE_AVG_GOALS,
            },
            "massey": {
                "non_neutral_factor": 0.85, "home_bonus": 0.35,
                "sigmoid_scale": 2.5, "draw_factor": 0.22,
            },
            "form": {"matches": FORM_MATCHES, "decay": FORM_DECAY},
            "head_to_head": {
                "maximum_matches": H2H_MAX_MATCHES,
                "year_limit": H2H_YEAR_LIMIT,
            },
            "knn_similar": {"neighbors": KNN_K},
            "bayesian": {
                "prior_mean": 0.0, "prior_std": 2.0,
                "prediction_samples": 5000,
            },
            "market_odds": {"minimum_decimal_odds": 1.0},
            "monte_carlo": {"simulations": MC_SIMULATIONS, "random_seed": 42},
        }
        runtime_config = {
            "runtime_protocol_version": RUNTIME_PROTOCOL_VERSION,
            "code_commit": code_commit,
            "feature_version": FeatureBuilder.FEATURE_VERSION,
            "feature_names": FeatureBuilder.FEATURE_NAMES,
            "minimum_team_matches": MIN_TEAM_MATCHES,
            "minimum_knn_samples": MIN_KNN_SAMPLES,
            "home_advantage": HOME_ADVANTAGE,
            "feature_defaults": {
                "elo": 1500.0, "massey": 0.0,
                "attack": 1.0, "defense": 1.0,
                "form": 0.5, "ppg": 1.0 / 3.0,
                "goal_difference": 0.0, "head_to_head": 1.0 / 3.0,
                "squad_completeness": 1.0,
            },
            "model_parameters": model_parameters,
        }
        runtime_config_fingerprint = fingerprint(runtime_config)
        parameter_fingerprints = {
            model_id: fingerprint(parameters)
            for model_id, parameters in model_parameters.items()
        }
        model_statuses = self._model_statuses(
            valid, team_type_models, competition_models, cutoff,
            parameter_fingerprints,
        )
        self._probe_models(team_type_models, competition_models)
        snapshot_payload = {
            "data_fingerprint": data_fingerprint,
            "runtime_config_fingerprint": runtime_config_fingerprint,
            "weights_fingerprint": weights_fingerprint,
            "models": {
                key: status.to_dict() for key, status in model_statuses.items()
            },
        }
        snapshot_id = f"runtime-{fingerprint(snapshot_payload)}"
        times = sorted(_match_time(match) for match in valid if _match_time(match))
        warnings = []
        if code_commit == "unknown":
            warnings.append("code_commit_unknown")
        invalid_count = len(matches) - len(valid)
        if invalid_count:
            warnings.append(f"invalid_team_type_matches:{invalid_count}")
        return ModelRuntimeSnapshot(
            snapshot_id=snapshot_id,
            built_at=_iso(utc_now()),
            data_as_of=cutoff,
            trained_from=times[0] if times else None,
            trained_until=times[-1] if times else None,
            training_sample_count=len(valid),
            data_fingerprint=data_fingerprint,
            runtime_config_fingerprint=runtime_config_fingerprint,
            weights_fingerprint=weights_fingerprint,
            weights_source="builtin_v1",
            code_commit=code_commit,
            feature_version=FeatureBuilder.FEATURE_VERSION,
            feature_names=FeatureBuilder.FEATURE_NAMES,
            team_type_models=MappingProxyType(team_type_models),
            competition_models=MappingProxyType(competition_models),
            model_statuses=MappingProxyType(model_statuses),
            weights=MappingProxyType(weights),
            data_quality=MappingProxyType({
                "finished_matches": len(matches),
                "accepted_training_matches": len(valid),
                "excluded_team_type_matches": invalid_count,
            }),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _build_team_type_models(matches):
        groups = {}
        for team_type in ("national", "club"):
            scoped = [m for m in matches if m["home_team_type"] == team_type]
            if not scoped:
                continue
            elo = EloRating(storage_path=os.devnull).rebuild(scoped)
            form = FormModel(); form.load_history(scoped)
            h2h = HeadToHeadModel(); h2h.load_history(scoped)
            bayesian = BayesianHierarchicalModel(); bayesian.fit(scoped)
            groups[team_type] = TeamTypeRuntime(
                team_type=team_type,
                models=MappingProxyType({
                    "elo": elo, "form": form, "head_to_head": h2h,
                    "bayesian": bayesian,
                }),
                team_match_counts=MappingProxyType(_team_counts(scoped)),
                matches=tuple(scoped),
            )
        return groups

    def _build_competition_models(self, matches):
        grouped: dict[str, list[dict]] = defaultdict(list)
        for match in matches:
            grouped[match["competition_id"]].append(match)
        result = {}
        for competition_id, scoped in grouped.items():
            name = scoped[0]["league"]
            strengths = build_strengths_from_results(scoped, name)
            poisson = PoissonModel(name); poisson.set_team_strengths(strengths)
            dixon = DixonColesModel(name); dixon.set_team_strengths(strengths)
            massey = MasseyRanking(); massey.fit(scoped)
            if self.historical_feature_index is not None:
                knn_rows = self.historical_feature_index.rows_for(competition_id, scoped)
                knn = _knn_from_rows(knn_rows)
            else:
                knn = _build_rolling_knn(scoped, self.feature_builder)
            result[competition_id] = CompetitionRuntime(
                competition_id=competition_id,
                competition_name=name,
                models=MappingProxyType({
                    "poisson": poisson, "dixon_coles": dixon,
                    "massey": massey, "knn_similar": knn,
                }),
                team_match_counts=MappingProxyType(_team_counts(scoped)),
                massey_components=MappingProxyType(_components(scoped)),
                knn_sample_count=len(knn.match_features),
                matches=tuple(scoped),
            )
        return result

    def _model_statuses(
        self, matches, team_groups, competition_groups, cutoff,
        parameter_fingerprints,
    ):
        times = sorted(_match_time(match) for match in matches if _match_time(match))
        statuses = {}
        for model_id in (
            "elo", "form", "head_to_head", "bayesian", "poisson",
            "dixon_coles", "massey", "knn_similar", "market_odds",
            "monte_carlo",
        ):
            statuses[model_id] = ModelStatus(
                model_id=model_id,
                status="ready",
                feature_version=(FeatureBuilder.FEATURE_VERSION if model_id == "knn_similar" else None),
                training_sample_count=len(matches),
                trained_from=times[0] if times else None,
                trained_until=times[-1] if times else None,
                metadata={
                    "team_type_groups": len(team_groups),
                    "competition_groups": len(competition_groups),
                    "parameter_fingerprint": parameter_fingerprints.get(model_id),
                },
            )
        for model_id in ("xgboost", "neural_net", "stacking"):
            inspection = self.artifact_inspector.inspect(model_id, cutoff)
            artifact = inspection.metadata
            statuses[model_id] = ModelStatus(
                model_id=model_id,
                status=inspection.status,
                reason=inspection.reason,
                model_version=artifact.model_version if artifact else "1",
                feature_version=(
                    artifact.feature_version if artifact
                    else FeatureBuilder.FEATURE_VERSION
                ),
                training_sample_count=(artifact.training_sample_count if artifact else 0),
                trained_from=artifact.trained_from if artifact else None,
                trained_until=artifact.trained_until if artifact else None,
                metadata={
                    "artifact_valid": artifact is not None,
                    "artifact_format": artifact.artifact_format if artifact else None,
                    "training_data_fingerprint": (
                        artifact.training_data_fingerprint if artifact else None
                    ),
                    "parameter_fingerprint": (
                        artifact.parameter_fingerprint if artifact else None
                    ),
                },
            )
        return statuses

    @staticmethod
    def _probe_models(team_groups, competition_groups):
        market_probe = normalize_prediction(
            "market_odds",
            MarketOddsModel().predict(home_odds=2.0, draw_odds=3.2, away_odds=4.0),
        )
        if not market_probe.get("available"):
            raise RuntimeError("market_odds runtime probe failed")
        simulation_probe = MonteCarloModel(simulations=100).simulate([
            {"home_win": 0.4, "draw": 0.3, "away_win": 0.3}
        ])
        if not normalize_prediction("monte_carlo_probe", simulation_probe).get("available"):
            raise RuntimeError("monte_carlo runtime probe failed")
        for group in team_groups.values():
            teams = sorted(group.team_match_counts, key=group.team_match_counts.get, reverse=True)
            if len(teams) < 2:
                continue
            home, away = teams[:2]
            probes = {
                "elo": group.models["elo"].predict_match(home, away, True),
                "form": group.models["form"].predict(home, away, True),
                "head_to_head": group.models["head_to_head"].predict(home, away, True),
                "bayesian": group.models["bayesian"].predict(home, away, True),
            }
            for model_id, output in probes.items():
                if not normalize_prediction(model_id, output).get("available"):
                    raise RuntimeError(f"{model_id} runtime probe failed")
        for group in competition_groups.values():
            sample = group.matches[0]
            home, away = sample["home_team"], sample["away_team"]
            probes = {
                "poisson": group.models["poisson"].predict(home, away, True),
                "dixon_coles": group.models["dixon_coles"].predict(home, away, True),
                "massey": group.models["massey"].predict(home, away, True),
            }
            for model_id, output in probes.items():
                if not normalize_prediction(model_id, output).get("available"):
                    raise RuntimeError(f"{model_id} runtime probe failed")
            if group.knn_sample_count >= MIN_KNN_SAMPLES:
                sample_vector = group.models["knn_similar"].match_features[0]["features"]
                output = group.models["knn_similar"].predict(sample_vector)
                if not normalize_prediction("knn_similar", output).get("available"):
                    raise RuntimeError("knn_similar runtime probe failed")


class RuntimeManager:
    def __init__(self, builder: ModelRuntimeBuilder):
        self.builder = builder
        self._state_lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._snapshot: ModelRuntimeSnapshot | None = None
        self._refreshing = False
        self._last_refresh_error: dict[str, str] | None = None

    def initialize(self):
        with self._state_lock:
            if self._snapshot is not None:
                return self._snapshot
        snapshot = self.builder.build()
        with self._state_lock:
            if self._snapshot is None:
                self._snapshot = snapshot
            return self._snapshot

    def current(self):
        with self._state_lock:
            if self._snapshot is None:
                raise RuntimeNotReadyError("预测运行时尚未初始化")
            return self._snapshot

    def refresh(self, reason="manual"):
        return self._refresh_locked(reason, update_operation=None)[1]

    def run_update(self, update_operation: Callable[[], Any], reason: str):
        return self._refresh_locked(reason, update_operation=update_operation)

    def _refresh_locked(self, reason, update_operation):
        if not self._refresh_lock.acquire(blocking=False):
            raise RuntimeRefreshInProgressError("已有运行时刷新任务正在执行")
        previous = self._snapshot
        with self._state_lock:
            self._refreshing = True
        try:
            update_result = update_operation() if update_operation else None
            snapshot = self.builder.build()
            current_fingerprint = self.builder.repository.build_data_fingerprint(
                {"status": "finished", "data_quality_status": "valid"},
                as_of=snapshot.data_as_of,
            )
            if current_fingerprint != snapshot.data_fingerprint:
                raise RuntimeError("database_changed_during_runtime_build")
            with self._state_lock:
                self._snapshot = snapshot
                self._last_refresh_error = None
            return update_result, RefreshResult(
                status="ok",
                reason=reason,
                previous_snapshot_id=previous.snapshot_id if previous else None,
                snapshot_id=snapshot.snapshot_id,
                runtime_stale=False,
            )
        except Exception as exc:
            with self._state_lock:
                self._last_refresh_error = {
                    "code": "RUNTIME_REFRESH_FAILED",
                    "message": str(exc),
                    "at": _iso(utc_now()),
                }
            if update_operation is None:
                raise
            return update_result if "update_result" in locals() else None, RefreshResult(
                status="partial" if "update_result" in locals() else "error",
                reason=reason,
                previous_snapshot_id=previous.snapshot_id if previous else None,
                snapshot_id=previous.snapshot_id if previous else None,
                runtime_stale=True,
                error_code="RUNTIME_REFRESH_FAILED",
                error="运行时刷新失败，继续使用旧快照",
            )
        finally:
            with self._state_lock:
                self._refreshing = False
            self._refresh_lock.release()

    def status(self):
        with self._state_lock:
            snapshot = self._snapshot
            refreshing = self._refreshing
            last_error = dict(self._last_refresh_error) if self._last_refresh_error else None
        database_fingerprint = None
        stale = snapshot is None
        try:
            database_fingerprint = self.builder.repository.build_data_fingerprint(
                {"status": "finished", "data_quality_status": "valid"},
                as_of=_iso(utc_now()),
            )
            stale = snapshot is None or database_fingerprint != snapshot.data_fingerprint
        except Exception:
            stale = True
        return {
            "ready": snapshot is not None,
            "refreshing": refreshing,
            "runtime_stale": stale,
            "database_fingerprint": database_fingerprint,
            "snapshot": snapshot.public_metadata() if snapshot else None,
            "last_refresh_error": last_error,
        }


class FixedRuntimeProvider:
    """Read-only provider used by historical prediction batches."""

    def __init__(self, snapshot: ModelRuntimeSnapshot):
        self._snapshot = snapshot

    def current(self):
        return self._snapshot
