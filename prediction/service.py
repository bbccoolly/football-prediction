"""Single prediction path shared by Web, CLI, debug and future backtests."""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from config import HOME_ADVANTAGE, SAMPLE_PLAYERS
from data.match_repository import MatchRepository
from ensemble.bma import BayesianModelAveraging
from ensemble.prediction_contract import normalize_prediction
from features.builder import FeatureBuilder
from features.player_impact import PlayerImpact
from models.market_odds import MarketOddsModel
from models.monte_carlo import MonteCarloModel
from prediction.contracts import (
    InvalidPredictionRequestError,
    ModelExecutionError,
    NoAvailableModelsError,
    OddsSnapshot,
    PredictionRequest,
    PredictionResult,
    SnapshotTimeMismatchError,
    ensure_utc,
)
from prediction.runtime import MIN_KNN_SAMPLES, MIN_TEAM_MATCHES, RuntimeManager


def _unavailable(model_id, reason, evidence=None):
    return normalize_prediction(model_id, {
        "model": model_id,
        "status": "insufficient_evidence",
        "available": False,
        "warnings": [reason],
        "evidence": evidence or {},
    })


def _agreement(predictions):
    valid = [prediction for prediction in predictions.values() if prediction.get("available")]
    if len(valid) < 2:
        return 0.0
    deviations = []
    for field in ("home_win", "draw", "away_win"):
        values = [prediction[field] for prediction in valid]
        average = sum(values) / len(values)
        deviations.append(math.sqrt(sum((value - average) ** 2 for value in values) / len(values)))
    return max(0.0, min(100.0, round(100 * (1.0 - sum(deviations) / 3 * 5), 1)))


class PredictionService:
    MODEL_STEPS = (
        ("poisson", "泊松分布"), ("dixon_coles", "Dixon-Coles"),
        ("elo", "ELO评级"), ("massey", "Massey排名"),
        ("form", "近期状态"), ("head_to_head", "交锋记录"),
        ("market_odds", "市场赔率"), ("knn_similar", "KNN相似"),
        ("xgboost", "XGBoost"), ("neural_net", "神经网络"),
        ("bayesian", "贝叶斯层次"), ("monte_carlo", "蒙特卡洛模拟"),
    )

    def __init__(self, repository: MatchRepository, runtime_manager: RuntimeManager):
        self.repository = repository
        self.runtime_manager = runtime_manager
        self.feature_builder = FeatureBuilder()

    def request_from_payload(self, payload: Mapping[str, Any], default_neutral=True):
        if not isinstance(payload, Mapping):
            raise InvalidPredictionRequestError("请求体必须是 JSON 对象", "INVALID_JSON")
        self.runtime_manager.current()

        home = self._resolve_team(payload, "home")
        away = self._resolve_team(payload, "away")
        if home["team_id"] == away["team_id"]:
            raise InvalidPredictionRequestError("主客队不能相同", "SAME_TEAM")
        if home["team_type"] != away["team_type"]:
            raise InvalidPredictionRequestError(
                "国家队和俱乐部不能组成同一场比赛", "MIXED_TEAM_TYPES"
            )

        competition_value = payload.get("competition_id") or payload.get("league") or "世界杯"
        competition = self.repository.resolve_competition(str(competition_value))
        if not competition:
            raise InvalidPredictionRequestError("赛事不存在", "UNKNOWN_COMPETITION")

        raw_predicted_at = payload.get("predicted_at")
        predicted_at = (
            ensure_utc(raw_predicted_at)
            if raw_predicted_at
            else datetime.now(timezone.utc)
        )
        neutral = payload.get("neutral", default_neutral)
        if not isinstance(neutral, bool):
            raise InvalidPredictionRequestError("neutral 必须是布尔值", "INVALID_NEUTRAL")
        home_missing = self._missing_players(payload.get("home_missing", []))
        away_missing = self._missing_players(payload.get("away_missing", []))
        odds = self._odds_from_payload(payload, predicted_at)
        return PredictionRequest(
            home_team_id=home["team_id"],
            away_team_id=away["team_id"],
            competition_id=competition["competition_id"],
            predicted_at=predicted_at,
            neutral=neutral,
            match_id=str(payload.get("match_id") or "") or None,
            odds=odds,
            home_missing=home_missing,
            away_missing=away_missing,
        )

    def _resolve_team(self, payload, side):
        team_id = str(payload.get(f"{side}_team_id") or "").strip()
        raw_name = str(payload.get(f"{side}_team") or "").strip()
        if team_id:
            team = self.repository.get_team(team_id)
        elif raw_name:
            team = self.repository.resolve_team_unique(raw_name)
        else:
            team = None
        if not team:
            code = "MISSING_TEAMS" if not team_id and not raw_name else "UNKNOWN_TEAM"
            raise InvalidPredictionRequestError("请选择有效的主队和客队", code)
        return team

    @staticmethod
    def _missing_players(value):
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise InvalidPredictionRequestError(
                "缺阵球员必须使用字符串数组", "INVALID_MISSING_PLAYERS"
            )
        return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))

    @staticmethod
    def _odds_from_payload(payload, predicted_at):
        keys = ("home_odds", "draw_odds", "away_odds")
        supplied = [payload.get(key) not in (None, "") for key in keys]
        if not any(supplied):
            return None
        if not all(supplied):
            raise InvalidPredictionRequestError(
                "赔率必须同时提供主胜、平局和客胜", "INVALID_ODDS"
            )
        try:
            values = [float(payload[key]) for key in keys]
        except (TypeError, ValueError) as exc:
            raise InvalidPredictionRequestError(
                "赔率必须是有效数字", "INVALID_ODDS"
            ) from exc
        captured_at = ensure_utc(payload.get("odds_captured_at") or predicted_at)
        return OddsSnapshot(*values, captured_at=captured_at, source="manual")

    def predict(self, request, include_trace=False, progress=None):
        if not isinstance(request, PredictionRequest):
            raise InvalidPredictionRequestError("请求类型无效")
        snapshot = self.runtime_manager.current()
        if request.predicted_at < ensure_utc(snapshot.data_as_of):
            raise SnapshotTimeMismatchError("预测时点早于活动快照的数据截止时间")

        home = self.repository.get_team(request.home_team_id)
        away = self.repository.get_team(request.away_team_id)
        competition = self.repository.get_competition(request.competition_id)
        if not home or not away:
            raise InvalidPredictionRequestError("球队不存在", "UNKNOWN_TEAM")
        if not competition:
            raise InvalidPredictionRequestError("赛事不存在", "UNKNOWN_COMPETITION")
        if home["team_type"] != away["team_type"]:
            raise InvalidPredictionRequestError(
                "国家队和俱乐部不能组成同一场比赛", "MIXED_TEAM_TYPES"
            )

        progress = progress or (lambda _done, _name: None)
        home_name, away_name = home["canonical_name"], away["canonical_name"]
        common = snapshot.team_type_models.get(home["team_type"])
        scoped = snapshot.competition_models.get(request.competition_id)
        common_counts = common.team_match_counts if common else {}
        scoped_counts = scoped.team_match_counts if scoped else {}
        evidence_failures = []

        player_impact = PlayerImpact()
        for team_name, missing in (
            (home_name, request.home_missing), (away_name, request.away_missing)
        ):
            if team_name in SAMPLE_PLAYERS:
                player_impact.set_squad(team_name, SAMPLE_PLAYERS[team_name])
            player_impact.set_injuries(team_name, list(missing))
        squad_info = player_impact.both_teams_impact(home_name, away_name)

        predictions = {}
        predictions["poisson"] = self._scoped_prediction(
            "poisson", scoped, scoped_counts, home_name, away_name,
            lambda model: model.predict(home_name, away_name, request.neutral),
        ); progress(1, "泊松分布")
        predictions["dixon_coles"] = self._scoped_prediction(
            "dixon_coles", scoped, scoped_counts, home_name, away_name,
            lambda model: model.predict(home_name, away_name, request.neutral),
        ); progress(2, "Dixon-Coles")
        predictions["elo"] = self._common_prediction(
            "elo", common, common_counts, home_name, away_name,
            lambda model: model.predict_match(home_name, away_name, request.neutral),
        ); progress(3, "ELO评级")
        predictions["massey"] = self._massey_prediction(
            scoped, home_name, away_name, request.neutral
        ); progress(4, "Massey排名")
        predictions["form"] = self._common_prediction(
            "form", common, common_counts, home_name, away_name,
            lambda model: model.predict(home_name, away_name, request.neutral),
        ); progress(5, "近期状态")
        predictions["head_to_head"] = self._h2h_prediction(
            common, home_name, away_name, request.neutral
        ); progress(6, "交锋记录")

        odds = request.odds or self._repository_odds(request)
        if odds:
            predictions["market_odds"] = self._run_model(
                "market_odds", lambda: MarketOddsModel().predict(
                    home_odds=odds.home_odds,
                    draw_odds=odds.draw_odds,
                    away_odds=odds.away_odds,
                )
            )
        else:
            predictions["market_odds"] = _unavailable("market_odds", "market_odds_missing")
        progress(7, "市场赔率")

        feature_data, feature_warnings = self._build_features(
            common, scoped, home_name, away_name, competition["canonical_name"],
            request.neutral, squad_info,
        )
        predictions["knn_similar"] = self._knn_prediction(
            scoped, scoped_counts, home_name, away_name, feature_data["vector"]
        ); progress(8, "KNN相似")
        for index, model_id in ((9, "xgboost"), (10, "neural_net")):
            status = snapshot.model_statuses[model_id]
            predictions[model_id] = normalize_prediction(model_id, {
                "model": model_id,
                "available": False,
                "status": status.status,
                "warnings": [status.reason or status.status],
            })
            progress(index, "XGBoost" if model_id == "xgboost" else "神经网络")
        predictions["bayesian"] = self._common_prediction(
            "bayesian", common, common_counts, home_name, away_name,
            lambda model: model.predict(home_name, away_name, request.neutral),
        ); progress(11, "贝叶斯层次")

        for model_id, prediction in predictions.items():
            if not prediction.get("available"):
                evidence_failures.append({
                    "model_id": model_id,
                    "reason": prediction.get("warnings", [prediction.get("status")])[0],
                })
        bma = BayesianModelAveraging()
        bma.weights = dict(snapshot.weights)
        try:
            ensemble = bma.blend(predictions)
        except NoAvailableModelsError:
            raise
        simulation = MonteCarloModel().simulate([ensemble], [1.0])
        simulation.update({"role": "derived", "source": "ensemble"})
        predictions["monte_carlo"] = normalize_prediction("monte_carlo", {
            **simulation,
            "available": False,
            "status": "derived",
            "role": "derived",
            "warnings": ["derived_output"],
        })
        progress(12, "蒙特卡洛模拟")

        independent = {
            key: value for key, value in predictions.items() if key != "monte_carlo"
        }
        agreement = _agreement(independent)
        warnings = list(snapshot.warnings) + feature_warnings
        for prediction in independent.values():
            warnings.extend(prediction.get("warnings", []))
        if sum(bool(item.get("available")) for item in independent.values()) < 2:
            warnings.append("insufficient_models_for_agreement")
        warnings = tuple(dict.fromkeys(warnings))
        model_summary = {
            "total_models": len(independent),
            "available_models": sum(bool(item.get("available")) for item in independent.values()),
            "excluded_models": sum(not item.get("available") for item in independent.values()),
            "unknown_quality_models": sum(
                item.get("available") and item.get("data_quality") is None
                for item in independent.values()
            ),
            "using_defaults_models": 0,
        }
        poisson_ready = predictions["poisson"].get("available") and scoped
        htft = (
            scoped.models["poisson"].predict_htft(home_name, away_name, request.neutral)
            if poisson_ready else {"status": "insufficient_evidence"}
        )
        handicap = (
            scoped.models["poisson"].predict_handicap(home_name, away_name, request.neutral)
            if poisson_ready else {"status": "insufficient_evidence"}
        )
        trace = None
        if include_trace:
            trace = {
                "raw_data": {
                    "elo_home": feature_data["elo_home"],
                    "elo_away": feature_data["elo_away"],
                    "massey_home": feature_data["massey_home"],
                    "massey_away": feature_data["massey_away"],
                    "attack_home": feature_data["home_attack"],
                    "defense_home": feature_data["home_defense"],
                    "attack_away": feature_data["away_attack"],
                    "defense_away": feature_data["away_defense"],
                    "form_home": feature_data["form_home"],
                    "form_away": feature_data["form_away"],
                    "h2h": feature_data["h2h"],
                },
                "features": {
                    key: value for key, value in feature_data.items()
                    if key not in {"vector", "form_home", "form_away", "h2h"}
                },
                "calculation_steps": {
                    key: {
                        "status": value.get("status"),
                        "available": value.get("available"),
                        "evidence": value.get("evidence", {}),
                    }
                    for key, value in predictions.items()
                },
            }
        return PredictionResult(
            predictions=predictions,
            ensemble=ensemble,
            simulation=simulation,
            model_agreement=agreement,
            model_summary=model_summary,
            warnings=warnings,
            data_quality={
                "evidence_failures": evidence_failures,
                "feature_warnings": feature_warnings,
                "training": dict(snapshot.data_quality),
            },
            squad_info=squad_info,
            htft=htft,
            handicap=handicap,
            prediction_run_id=str(uuid.uuid4()),
            snapshot=snapshot,
            trace=trace,
        )

    def _common_prediction(self, model_id, group, counts, home, away, operation):
        minimum = MIN_TEAM_MATCHES[model_id]
        evidence = {"home_matches": counts.get(home, 0), "away_matches": counts.get(away, 0)}
        if not group or min(evidence.values()) < minimum:
            return _unavailable(model_id, "insufficient_team_history", evidence)
        return self._run_model(model_id, lambda: operation(group.models[model_id]))

    def _scoped_prediction(self, model_id, group, counts, home, away, operation):
        minimum = MIN_TEAM_MATCHES[model_id]
        evidence = {"home_matches": counts.get(home, 0), "away_matches": counts.get(away, 0)}
        if not group or min(evidence.values()) < minimum:
            return _unavailable(model_id, "insufficient_competition_history", evidence)
        return self._run_model(model_id, lambda: operation(group.models[model_id]))

    def _massey_prediction(self, group, home, away, neutral):
        if not group:
            return _unavailable("massey", "competition_model_missing")
        components = group.massey_components
        if home not in components or away not in components or components[home] != components[away]:
            return _unavailable("massey", "teams_not_in_same_component")
        return self._run_model(
            "massey", lambda: group.models["massey"].predict(home, away, neutral)
        )

    def _h2h_prediction(self, group, home, away, neutral):
        if not group:
            return _unavailable("head_to_head", "team_type_model_missing")
        stats = group.models["head_to_head"].get_h2h(home, away)
        if stats.get("total_matches", 0) < MIN_TEAM_MATCHES["head_to_head"]:
            return _unavailable("head_to_head", "no_head_to_head_history", stats)
        return self._run_model(
            "head_to_head",
            lambda: group.models["head_to_head"].predict(home, away, neutral),
        )

    def _knn_prediction(self, group, counts, home, away, vector):
        evidence = {
            "training_samples": group.knn_sample_count if group else 0,
            "home_matches": counts.get(home, 0),
            "away_matches": counts.get(away, 0),
        }
        if (
            not group or evidence["training_samples"] < MIN_KNN_SAMPLES
            or evidence["home_matches"] < 1 or evidence["away_matches"] < 1
        ):
            return _unavailable("knn_similar", "insufficient_knn_history", evidence)
        result = self._run_model(
            "knn_similar", lambda: group.models["knn_similar"].predict(vector)
        )
        if result.get("neighbors_found", 0) < MIN_KNN_SAMPLES:
            return _unavailable("knn_similar", "insufficient_knn_neighbors", evidence)
        return result

    @staticmethod
    def _run_model(model_id, operation):
        try:
            normalized = normalize_prediction(model_id, operation())
        except Exception as exc:
            raise ModelExecutionError(f"{model_id} 模型执行失败") from exc
        if not normalized.get("available"):
            raise ModelExecutionError(f"{model_id} 返回非法预测结果")
        return normalized

    def _repository_odds(self, request):
        if not request.match_id:
            return None
        raw = self.repository.get_pre_match_odds(
            request.match_id, request.predicted_at.isoformat()
        )
        if not raw:
            return None
        return OddsSnapshot(
            raw["home_odds"], raw["draw_odds"], raw["away_odds"],
            captured_at=ensure_utc(raw["captured_at"]), source=raw["source"],
        )

    def _build_features(self, common, scoped, home, away, competition_name, neutral, squad):
        warnings = []
        common_models = common.models if common else {}
        scoped_models = scoped.models if scoped else {}
        elo_home = common_models["elo"].get_rating(home) if common else 1500.0
        elo_away = common_models["elo"].get_rating(away) if common else 1500.0
        form_home = common_models["form"].get_form_score(home) if common else {
            "form_score": 0.5, "ppg": 1.0 / 3.0, "goal_diff_avg": 0.0, "matches_used": 0,
        }
        form_away = common_models["form"].get_form_score(away) if common else {
            "form_score": 0.5, "ppg": 1.0 / 3.0, "goal_diff_avg": 0.0, "matches_used": 0,
        }
        h2h = common_models["head_to_head"].get_h2h(home, away) if common else {"total_matches": 0}
        if not h2h.get("total_matches"):
            h2h = {
                **h2h, "a_win_rate": 1.0 / 3.0, "draw_rate": 1.0 / 3.0,
                "b_win_rate": 1.0 / 3.0, "avg_goals": 2.7,
            }
            warnings.append("h2h_feature_default")
        poisson = scoped_models.get("poisson")
        massey = scoped_models.get("massey")
        home_attack = poisson.attack_strengths.get(home, 1.0) if poisson else 1.0
        home_defense = poisson.defense_strengths.get(home, 1.0) if poisson else 1.0
        away_attack = poisson.attack_strengths.get(away, 1.0) if poisson else 1.0
        away_defense = poisson.defense_strengths.get(away, 1.0) if poisson else 1.0
        massey_home = massey.ratings.get(home, 0.0) if massey else 0.0
        massey_away = massey.ratings.get(away, 0.0) if massey else 0.0
        if not common:
            warnings.append("team_type_features_default")
        if not scoped:
            warnings.append("competition_features_default")
        built = self.feature_builder.build(
            elo_home=elo_home, elo_away=elo_away,
            atk_home=home_attack, atk_away=away_attack,
            def_home=home_defense, def_away=away_defense,
            form_home=form_home, form_away=form_away, h2h_stats=h2h,
            squad_home=squad["home_completeness"],
            squad_away=squad["away_completeness"],
            home_adv=HOME_ADVANTAGE.get(competition_name, HOME_ADVANTAGE["default"]),
            neutral=neutral, massey_home=massey_home, massey_away=massey_away,
        )
        built.update({
            "elo_home": elo_home, "elo_away": elo_away,
            "massey_home": massey_home, "massey_away": massey_away,
            "form_home": form_home, "form_away": form_away, "h2h": h2h,
        })
        return built, warnings
