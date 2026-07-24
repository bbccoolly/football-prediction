"""Strict expanding-window execution over immutable historical snapshots."""

from __future__ import annotations

import os
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

from backtest.admission import build_admission
from backtest.contracts import (
    BACKTEST_PROTOCOL_VERSION,
    BACKTEST_SCHEMA_VERSION,
    CANDIDATE_MODELS,
    LEARNING_MODELS,
    MODEL_BASELINES,
    BacktestConfig,
    BacktestExecutionError,
)
from backtest.data import (
    accepted_data_fingerprint,
    filter_eligible_matches,
    market_consensus,
    outcome_key,
    proportion_baselines,
    split_by_natural_day,
    walk_forward_batches,
)
from backtest.metrics import build_metrics
from backtest.storage import (
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    scientific_fingerprint,
)
from data.match_repository import MatchRepository, fingerprint
from prediction import (
    FixedRuntimeProvider,
    HistoricalFeatureIndex,
    ModelRuntimeBuilder,
    NoAvailableModelsError,
    OddsSnapshot,
    PredictionRequest,
    PredictionService,
)
from prediction.artifacts import FrozenArtifactInspector
from prediction.contracts import ensure_utc


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCIENTIFIC_MODELS = tuple(dict.fromkeys(MODEL_BASELINES + CANDIDATE_MODELS))


def collect_code_provenance(project_root=PROJECT_ROOT):
    configured = os.environ.get("FOOTBALL_CODE_COMMIT")

    def git(*arguments):
        try:
            result = subprocess.run(
                ["git", *arguments], cwd=project_root, text=True,
                capture_output=True, check=True, timeout=10,
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    commit = configured or git("rev-parse", "HEAD") or "unknown"
    status = git("status", "--porcelain", "--untracked-files=all")
    return {
        "code_commit": commit,
        "code_dirty": bool(status) if commit != "unknown" else True,
        "branch": git("branch", "--show-current") or "unknown",
        "commit_source": "environment" if configured else "git",
    }


def _strictly_before(match, cutoff):
    cutoff_value = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    if match["time_precision"] == "minute":
        value = datetime.fromisoformat(match["kickoff_utc"].replace("Z", "+00:00"))
        return value.astimezone(timezone.utc) < cutoff_value.astimezone(timezone.utc)
    return date.fromisoformat(match["event_date"]) < cutoff_value.date()


def _snapshot_time_is_valid(snapshot, cutoff):
    if not snapshot.trained_until:
        return False
    cutoff_value = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    value = snapshot.trained_until
    if len(value) == 10:
        return date.fromisoformat(value) < cutoff_value.date()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) < cutoff_value.astimezone(timezone.utc)


def _public_prediction(value):
    if not isinstance(value, dict):
        return {"available": False, "status": "unavailable", "reason": "missing_output"}
    has_probabilities = all(
        isinstance(value.get(key), (int, float))
        for key in ("home_win", "draw", "away_win")
    )
    result = {
        "available": bool(value.get("available", has_probabilities)),
        "status": value.get("status") or (
            "ready" if value.get("available", has_probabilities) else "unavailable"
        ),
    }
    if result["available"]:
        for key in ("home_win", "draw", "away_win"):
            result[key] = float(value[key])
    else:
        warnings = value.get("warnings") or []
        result["reason"] = (
            value.get("reason") or (warnings[0] if warnings else None)
            or result["status"]
        )
    if value.get("evidence"):
        result["evidence"] = dict(value["evidence"])
    return result


def _coverage_grade(match, consensus):
    has_source = int(match.get("source_count") or 0) > 0
    if (
        match["time_precision"] == "minute" and has_source
        and match.get("season") and consensus
    ):
        return "full"
    if match["time_precision"] == "minute" and has_source:
        return "standard"
    return "limited"


def _render_report(manifest, metrics, admission):
    partition = manifest["data"]["partitions"]
    lines = [
        "# Walk-forward 回测与模型准入报告",
        "",
        f"- Run ID：`{manifest['run_id']}`",
        f"- 结果指纹：`{manifest['result_fingerprint']}`",
        f"- 数据指纹：`{manifest['data']['fingerprint']}`",
        f"- 代码提交：`{manifest['provenance']['code_commit']}`",
        f"- 工作树干净：`{not manifest['provenance']['code_dirty']}`",
        f"- 有效比赛：{manifest['data']['accepted_matches']}",
        f"- 训练/验证/保留：{partition['training']}/{partition['validation']}/{partition['holdout']}",
        "",
        "## 总体准入",
        "",
        "| 模型 | 状态 | 原因 | 保留集覆盖率 |",
        "|---|---|---|---:|",
    ]
    holdout_models = metrics["holdout"]["overall"]["all"]["models"]
    for model_id, decision in admission["decisions"].items():
        coverage = holdout_models.get(model_id, {}).get("coverage")
        coverage_text = f"{coverage:.1%}" if coverage is not None else "-"
        lines.append(
            f"| {model_id} | {decision['status']} | {decision['reason']} | {coverage_text} |"
        )
    lines.extend([
        "", "## 结论", "",
        "本报告不会修改生产融合权重。`admitted` 仅表示通过本报告冻结的统计门禁。",
    ])
    return "\n".join(lines) + "\n"


class BacktestRunner:
    def __init__(
        self,
        repository: MatchRepository,
        config: BacktestConfig,
        *,
        artifact_root=None,
        project_root=PROJECT_ROOT,
        provenance=None,
    ):
        self.repository = repository
        self.config = config
        self.project_root = Path(project_root)
        self.artifact_root = Path(artifact_root or self.project_root / "ensemble/saved_models")
        self.provenance = provenance or collect_code_provenance(self.project_root)

    def run(self, run_id, *, source=None, progress=None):
        progress = progress or (lambda **_values: None)
        output_dir = self.config.output_root / run_id
        matches = self.repository.list_matches()
        accepted, excluded = filter_eligible_matches(matches, self.config)
        partitions = split_by_natural_day(accepted, self.config)
        market_inputs = {}
        odds_inventory = []
        for match in accepted:
            if match["time_precision"] != "minute":
                continue
            rows = self.repository.list_latest_pre_match_odds(
                match["match_id"], match["kickoff_utc"]
            )
            market_inputs[match["match_id"]] = market_consensus(rows)
            odds_inventory.extend({
                key: row.get(key)
                for key in (
                    "odds_snapshot_id", "match_id", "company", "captured_at",
                    "home_odds", "draw_odds", "away_odds", "source",
                )
            } for row in rows)
        source_matches_fingerprint = accepted_data_fingerprint(matches, {})
        accepted_matches_fingerprint = accepted_data_fingerprint(accepted, excluded)
        odds_fingerprint = fingerprint(odds_inventory)
        data_fingerprint = fingerprint({
            "source_matches": source_matches_fingerprint,
            "accepted_matches": accepted_matches_fingerprint,
            "odds": odds_fingerprint,
        })
        artifact_catalog = FrozenArtifactInspector.capture(self.artifact_root)
        feature_index = HistoricalFeatureIndex.build(accepted)
        builder = ModelRuntimeBuilder(
            self.repository,
            artifact_inspector=artifact_catalog,
            historical_feature_index=feature_index,
            code_commit=self.provenance["code_commit"],
        )
        prediction_records = []
        expected_config_fingerprint = None
        expected_weights_fingerprint = None
        batch_specs = []
        for partition_name, partition_matches in (
            ("validation", partitions.validation), ("holdout", partitions.holdout)
        ):
            for batch in walk_forward_batches(partition_matches):
                batch_specs.append((partition_name, batch))
        total_matches = len(partitions.validation) + len(partitions.holdout)
        processed = 0
        progress(
            phase="running", current_batch=0, total_batches=len(batch_specs),
            processed_matches=0, total_matches=total_matches, percent=0,
        )
        for batch_number, (partition_name, batch) in enumerate(batch_specs, start=1):
            snapshot = builder.build(batch["cutoff"])
            if not _snapshot_time_is_valid(snapshot, batch["cutoff"]):
                raise BacktestExecutionError("运行时快照包含批次时点之后的数据")
            if expected_config_fingerprint is None:
                expected_config_fingerprint = snapshot.runtime_config_fingerprint
                expected_weights_fingerprint = snapshot.weights_fingerprint
            elif (
                snapshot.runtime_config_fingerprint != expected_config_fingerprint
                or snapshot.weights_fingerprint != expected_weights_fingerprint
            ):
                raise BacktestExecutionError("批次间运行时配置或权重指纹发生变化")
            service = PredictionService(
                self.repository, FixedRuntimeProvider(snapshot)
            )
            history = [
                match for match in accepted if _strictly_before(match, batch["cutoff"])
            ]
            for match in batch["matches"]:
                consensus = None
                odds = None
                predicted_at = batch["cutoff"]
                if match["time_precision"] == "minute":
                    predicted_at = match["kickoff_utc"]
                    consensus = market_inputs.get(match["match_id"])
                    if consensus:
                        odds = OddsSnapshot(
                            *consensus["synthetic_odds"],
                            captured_at=ensure_utc(consensus["captured_at"]),
                            source=consensus["source"],
                        )
                request = PredictionRequest(
                    home_team_id=match["home_team_id"],
                    away_team_id=match["away_team_id"],
                    competition_id=match["competition_id"],
                    predicted_at=ensure_utc(predicted_at),
                    neutral=bool(match["neutral"]),
                    match_id=None,
                    odds=odds,
                )
                simple = proportion_baselines(history, match["competition_id"])
                try:
                    result = service.predict(request)
                    outputs = {
                        model_id: _public_prediction(value)
                        for model_id, value in result.predictions.items()
                        if model_id != "monte_carlo"
                    }
                    derived = {
                        "ensemble": _public_prediction(result.ensemble),
                        "monte_carlo": {
                            key: result.simulation.get(key)
                            for key in ("home_win", "draw", "away_win", "simulations")
                        },
                    }
                except NoAvailableModelsError:
                    outputs = {
                        model_id: {
                            "available": False,
                            "status": "insufficient_evidence",
                            "reason": "no_available_models",
                        }
                        for model_id in (
                            "poisson", "elo", "market_odds", *CANDIDATE_MODELS
                        )
                    }
                    derived = {"status": "no_available_models"}
                outputs.update(simple)
                for model_id in LEARNING_MODELS:
                    outputs[model_id] = {
                        "available": False,
                        "status": "not_evaluated",
                        "reason": "learning_model_loading_out_of_scope",
                    }
                match_metadata = {
                    "match_id": match["match_id"],
                    "competition_id": match["competition_id"],
                    "season": match.get("season"),
                    "event_date": match["event_date"],
                    "kickoff_utc": match.get("kickoff_utc"),
                    "time_precision": match["time_precision"],
                    "home_team_id": match["home_team_id"],
                    "away_team_id": match["away_team_id"],
                    "team_type": match["home_team_type"],
                    "neutral": bool(match["neutral"]),
                    "home_goals": match["home_goals"],
                    "away_goals": match["away_goals"],
                    "coverage_grade": _coverage_grade(match, consensus),
                }
                record_id = fingerprint({
                    "protocol_version": BACKTEST_PROTOCOL_VERSION,
                    "match_id": match["match_id"],
                    "batch_cutoff": batch["cutoff"],
                    "snapshot_id": snapshot.snapshot_id,
                })
                prediction_records.append({
                    "prediction_record_id": record_id,
                    "partition": partition_name,
                    "batch_id": batch["batch_id"],
                    "batch_cutoff": batch["cutoff"],
                    "match": match_metadata,
                    "actual": outcome_key(match),
                    "predictions": outputs,
                    "market": consensus,
                    "derived": derived,
                    "runtime": {
                        "snapshot_id": snapshot.snapshot_id,
                        "data_fingerprint": snapshot.data_fingerprint,
                        "runtime_config_fingerprint": snapshot.runtime_config_fingerprint,
                        "weights_fingerprint": snapshot.weights_fingerprint,
                        "trained_until": snapshot.trained_until,
                        "training_sample_count": snapshot.training_sample_count,
                    },
                })
                processed += 1
            progress(
                phase="running", current_batch=batch_number,
                total_batches=len(batch_specs), processed_matches=processed,
                total_matches=total_matches,
                percent=round(processed / total_matches * 80, 2),
            )
        validation_records = [
            record for record in prediction_records if record["partition"] == "validation"
        ]
        holdout_records = [
            record for record in prediction_records if record["partition"] == "holdout"
        ]
        validation_metrics = build_metrics(
            validation_records, SCIENTIFIC_MODELS, MODEL_BASELINES,
            iterations=self.config.bootstrap_iterations,
            seed=self.config.random_seed,
            enable_bootstrap=False,
            progress=lambda completed, total, **_values: progress(
                phase="validation_metrics", current_batch=len(batch_specs),
                total_batches=len(batch_specs), processed_matches=processed,
                total_matches=total_matches,
                percent=round(80 + completed / max(total, 1) * 5, 2),
            ),
        )
        holdout_metrics = build_metrics(
            holdout_records, SCIENTIFIC_MODELS, MODEL_BASELINES,
            iterations=self.config.bootstrap_iterations,
            seed=self.config.random_seed,
            progress=lambda completed, total, **_values: progress(
                phase="holdout_metrics", current_batch=len(batch_specs),
                total_batches=len(batch_specs), processed_matches=processed,
                total_matches=total_matches,
                percent=round(85 + completed / max(total, 1) * 14, 2),
            ),
        )
        metrics = {
            "schema_version": BACKTEST_SCHEMA_VERSION,
            "validation": validation_metrics,
            "holdout": holdout_metrics,
        }
        admission = build_admission(
            holdout_metrics, self.config, self.provenance,
            len(accepted), len(partitions.holdout),
        )
        protocol = {
            "version": BACKTEST_PROTOCOL_VERSION,
            "random_seed": self.config.random_seed,
            "bootstrap_iterations": self.config.bootstrap_iterations,
            "runtime_config_fingerprint": expected_config_fingerprint,
            "weights_fingerprint": expected_weights_fingerprint,
            "feature_index_fingerprint": feature_index.data_fingerprint,
            "artifact_inventory": artifact_catalog.public_inventory(),
            "code_commit": self.provenance["code_commit"],
            "data_fingerprint": data_fingerprint,
        }
        result_fingerprint = scientific_fingerprint(
            prediction_records, metrics, admission, protocol
        )
        manifest = {
            "schema_version": BACKTEST_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "as_of": self.config.as_of,
            "source": source or {"kind": "repository"},
            "provenance": self.provenance,
            "protocol": protocol,
            "data": {
                "fingerprint": data_fingerprint,
                "source_matches_fingerprint": source_matches_fingerprint,
                "accepted_matches_fingerprint": accepted_matches_fingerprint,
                "odds_fingerprint": odds_fingerprint,
                "fetched_matches": len(matches),
                "accepted_matches": len(accepted),
                "excluded": excluded,
                "partitions": partitions.public_summary(),
            },
            "result_fingerprint": result_fingerprint,
        }
        atomic_write_jsonl(output_dir / "predictions.jsonl", prediction_records)
        atomic_write_json(output_dir / "metrics.json", metrics)
        atomic_write_json(output_dir / "admission.json", admission)
        atomic_write_json(output_dir / "manifest.json", manifest)
        atomic_write_text(output_dir / "report.md", _render_report(manifest, metrics, admission))
        progress(
            phase="completed", current_batch=len(batch_specs),
            total_batches=len(batch_specs), processed_matches=processed,
            total_matches=total_matches, percent=100,
        )
        return {
            "run_id": run_id,
            "output_dir": output_dir,
            "manifest": manifest,
            "metrics": metrics,
            "admission": admission,
            "result_fingerprint": result_fingerprint,
            "insufficient_data": not admission["formal_data_ready"],
        }
