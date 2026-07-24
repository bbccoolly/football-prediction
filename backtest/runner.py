"""Strict expanding-window execution over immutable historical snapshots."""

from __future__ import annotations

import os
import platform
import subprocess
import time
import ctypes
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from backtest.admission import build_admission
from backtest.contracts import (
    BACKTEST_PROTOCOL_VERSION,
    BACKTEST_SCHEMA_VERSION,
    CANDIDATE_MODELS,
    LEARNING_MODELS,
    MODEL_BASELINES,
    BacktestConfigurationError,
    BacktestCheckpointError,
    BacktestConfig,
    BacktestDataError,
    BacktestExecutionError,
    BacktestInputChangedError,
    BacktestSpecMismatchError,
)
from backtest.data import (
    BacktestHistoryView,
    outcome_key,
    proportion_baselines,
)
from backtest.metrics import build_metrics
from backtest.storage import (
    BacktestCheckpointStore,
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    scientific_fingerprint,
    scoring_fingerprint,
    read_json,
    read_jsonl,
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


def _peak_memory_bytes():
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ProcessMemoryCounters), ctypes.c_ulong,
        ]
        get_process_memory_info.restype = ctypes.c_bool
        process = get_current_process()
        if get_process_memory_info(
            process, ctypes.byref(counters), counters.cb
        ):
            return int(counters.PeakWorkingSetSize)
        return None
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if platform.system() == "Darwin" else peak * 1024)
    except (ImportError, OSError):
        return None


def _research_only_admission(admission):
    def downgrade(decision):
        if decision.get("status") == "admitted":
            decision["original_status"] = "admitted"
            decision["status"] = "research_only"
            decision["reason"] = "scientific_scoring_mode"
        for scoped in decision.get("competitions", {}).values():
            downgrade(scoped)
    for decision in admission["decisions"].values():
        downgrade(decision)
    admission["research_only"] = True
    return admission


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
        has_source and match.get("season") and consensus
    ):
        return "full"
    if has_source:
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

    def run(self, run_id, *, source=None, progress=None, resume=False):
        progress = progress or (lambda **_values: None)
        started = time.perf_counter()
        timing = {
            "data_loading_seconds": 0.0,
            "feature_index_seconds": 0.0,
            "runtime_build_seconds": 0.0,
            "base_scoring_seconds": 0.0,
            "metrics_seconds": 0.0,
            "persistence_seconds": 0.0,
        }
        output_dir = self.config.output_root / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        stage_started = time.perf_counter()
        view = BacktestHistoryView.load(self.repository, self.config)
        timing["data_loading_seconds"] += time.perf_counter() - stage_started
        if source and source.get("kind") == "database" and not self.config.research_only:
            if self.provenance.get("code_commit") in {None, "", "unknown"} or self.provenance.get("code_dirty", True):
                raise BacktestConfigurationError("正式数据库回测要求已提交且干净的代码")
            if not view.membership_complete:
                raise BacktestConfigurationError("数据 batch 成员不完整，不能正式回测")
            if not self.config.dataset_batch_id:
                raise BacktestConfigurationError("正式数据库回测必须指定数据 batch")
            readiness = self.repository.build_data_readiness_report(
                batch_id=self.config.dataset_batch_id,
                evaluation_as_of=self.config.as_of,
            )
            if readiness.get("status") != "ready":
                raise BacktestConfigurationError("数据 batch 未通过正式数据门禁")
        artifact_catalog = FrozenArtifactInspector.capture(self.artifact_root)
        stage_started = time.perf_counter()
        feature_index = HistoricalFeatureIndex.build(view.accepted)
        timing["feature_index_seconds"] += time.perf_counter() - stage_started
        builder = ModelRuntimeBuilder(
            self.repository, artifact_inspector=artifact_catalog,
            historical_feature_index=feature_index,
            code_commit=self.provenance["code_commit"],
        )
        batch_specs = list(view.batch_specs)
        if not batch_specs:
            raise BacktestDataError("没有可执行的 validation/holdout 批次")
        stage_started = time.perf_counter()
        first_snapshot = builder.build_from_matches(
            view.accepted, batch_specs[0][1]["cutoff"]
        )
        timing["runtime_build_seconds"] += time.perf_counter() - stage_started
        if not _snapshot_time_is_valid(first_snapshot, batch_specs[0][1]["cutoff"]):
            raise BacktestExecutionError("运行时快照包含批次时点之后的数据")
        expected_config_fingerprint = first_snapshot.runtime_config_fingerprint
        expected_weights_fingerprint = first_snapshot.weights_fingerprint
        spec_basis = {
            "protocol_version": BACKTEST_PROTOCOL_VERSION,
            "schema_version": BACKTEST_SCHEMA_VERSION,
            "config": {key: str(value) if isinstance(value, Path) else value for key, value in asdict(self.config).items()},
            "run_input_fingerprint": view.run_input_fingerprint,
            "runtime_config_fingerprint": expected_config_fingerprint,
            "weights_fingerprint": expected_weights_fingerprint,
            "feature_index_fingerprint": feature_index.data_fingerprint,
            "artifact_inventory": artifact_catalog.public_inventory(),
            "provenance": self.provenance,
            "source_kind": (source or {}).get("kind", "repository"),
            "source_locator_fingerprint": fingerprint(
                str((source or {}).get("locator") or "")
            ),
            "allow_insufficient_data": bool(
                (source or {}).get("allow_insufficient_data", False)
            ),
        }
        spec_fingerprint = fingerprint(spec_basis)
        run_spec = {
            **spec_basis,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_input_fingerprint": view.run_input_fingerprint,
            "run_spec_fingerprint": spec_fingerprint,
            "dataset": {
                "batch_id": self.config.dataset_batch_id,
                "membership_status": (view.dataset or {}).get("membership_status", "not_selected"),
                "membership_fingerprint": view.membership_fingerprint,
            },
            "python_version": platform.python_version(),
            "source": source or {"kind": "repository"},
        }
        checkpoint_store = BacktestCheckpointStore(output_dir, run_id)
        if resume:
            existing = checkpoint_store.load_run_spec()
            if existing.get("run_spec_fingerprint") != spec_fingerprint:
                raise BacktestSpecMismatchError("运行规格指纹不一致")
            if existing.get("run_input_fingerprint") != view.run_input_fingerprint:
                raise BacktestInputChangedError("回测输入数据已经变化")
        else:
            checkpoint_store.create_run_spec(run_spec)
        manifest_path = output_dir / "manifest.json"
        if resume and manifest_path.is_file():
            manifest = read_json(manifest_path)
            metrics = read_json(output_dir / "metrics.json")
            admission = read_json(output_dir / "admission.json")
            prediction_records = read_jsonl(output_dir / "predictions.jsonl")
            if (
                manifest.get("schema_version") != BACKTEST_SCHEMA_VERSION
                or manifest.get("run_id") != run_id
                or manifest.get("data", {}).get("fingerprint") != view.run_input_fingerprint
                or manifest.get("scoring_fingerprint") != scoring_fingerprint(prediction_records)
                or manifest.get("result_fingerprint") != scientific_fingerprint(
                    prediction_records, metrics, admission, manifest.get("protocol")
                )
            ):
                raise BacktestCheckpointError("完整运行提交标记校验失败")
            return {
                "run_id": run_id, "output_dir": output_dir,
                "manifest": manifest, "metrics": metrics, "admission": admission,
                "result_fingerprint": manifest["result_fingerprint"],
                "scoring_fingerprint": manifest["scoring_fingerprint"],
                "insufficient_data": not admission["formal_data_ready"],
            }
        prediction_records, completed_batches = checkpoint_store.validate(
            batch_specs, input_fingerprint=view.run_input_fingerprint,
            spec_fingerprint=spec_fingerprint,
        )
        total_matches = len(view.partitions.validation) + len(view.partitions.holdout)
        processed = len(prediction_records)
        progress(
            phase="running", current_batch=completed_batches,
            total_batches=len(batch_specs), processed_matches=processed,
            total_matches=total_matches,
            percent=round(processed / max(total_matches, 1) * 80, 2),
        )
        for batch_number, (partition_name, batch) in enumerate(batch_specs[completed_batches:], start=completed_batches + 1):
            if batch_number == 1:
                snapshot = first_snapshot
            else:
                stage_started = time.perf_counter()
                snapshot = builder.build_from_matches(view.accepted, batch["cutoff"])
                timing["runtime_build_seconds"] += time.perf_counter() - stage_started
            if not _snapshot_time_is_valid(snapshot, batch["cutoff"]):
                raise BacktestExecutionError("运行时快照包含批次时点之后的数据")
            if snapshot.runtime_config_fingerprint != expected_config_fingerprint or snapshot.weights_fingerprint != expected_weights_fingerprint:
                raise BacktestExecutionError("批次间运行时配置或权重指纹发生变化")
            service = PredictionService(self.repository, FixedRuntimeProvider(snapshot))
            history = view.history_before(batch["cutoff"])
            batch_records = []
            stage_started = time.perf_counter()
            for match in batch["matches"]:
                consensus = view.market_inputs.get(match["match_id"])
                odds = None
                predicted_at = batch["cutoff"]
                if match["time_precision"] == "minute":
                    predicted_at = match["kickoff_utc"]
                    if consensus and consensus.get("evidence_types") == ["captured_at"]:
                        odds = OddsSnapshot(
                            *consensus["synthetic_odds"],
                            captured_at=ensure_utc(consensus["captured_at"]),
                            source=consensus["source"],
                        )
                request = PredictionRequest(
                    home_team_id=match["home_team_id"], away_team_id=match["away_team_id"],
                    competition_id=match["competition_id"], predicted_at=ensure_utc(predicted_at),
                    neutral=bool(match["neutral"]), odds=odds,
                )
                simple = proportion_baselines(history, match["competition_id"])
                try:
                    result = service.evaluate(request)
                    outputs = {
                        model_id: _public_prediction(value)
                        for model_id, value in result.predictions.items()
                        if model_id != "monte_carlo"
                    }
                    derived = {
                        "ensemble": _public_prediction(result.ensemble),
                        "monte_carlo": result.simulation,
                    }
                except NoAvailableModelsError:
                    outputs = {
                        model_id: {"available": False, "status": "insufficient_evidence", "reason": "no_available_models"}
                        for model_id in ("poisson", "elo", "market_odds", *CANDIDATE_MODELS)
                    }
                    derived = {"status": "no_available_models"}
                if consensus:
                    outputs["market_odds"] = {
                        "available": True, "status": "ready",
                        "home_win": consensus["probabilities"][0],
                        "draw": consensus["probabilities"][1],
                        "away_win": consensus["probabilities"][2],
                        "evidence": {"evidence_types": consensus["evidence_types"], "companies": consensus["companies"]},
                    }
                outputs.update(simple)
                for model_id in LEARNING_MODELS:
                    outputs[model_id] = {"available": False, "status": "not_evaluated", "reason": "learning_model_loading_out_of_scope"}
                match_metadata = {
                    "match_id": match["match_id"], "competition_id": match["competition_id"],
                    "season": match.get("season"), "event_date": match["event_date"],
                    "kickoff_utc": match.get("kickoff_utc"), "time_precision": match["time_precision"],
                    "home_team_id": match["home_team_id"], "away_team_id": match["away_team_id"],
                    "team_type": match["home_team_type"], "neutral": bool(match["neutral"]),
                    "home_goals": match["home_goals"], "away_goals": match["away_goals"],
                    "coverage_grade": _coverage_grade(match, consensus),
                }
                batch_records.append({
                    "prediction_record_id": fingerprint({"protocol_version": BACKTEST_PROTOCOL_VERSION, "match_id": match["match_id"], "batch_cutoff": batch["cutoff"], "snapshot_id": snapshot.snapshot_id}),
                    "partition": partition_name, "batch_id": batch["batch_id"], "batch_cutoff": batch["cutoff"],
                    "match": match_metadata, "actual": outcome_key(match), "predictions": outputs,
                    "market": consensus, "derived": derived,
                    "runtime": {"snapshot_id": snapshot.snapshot_id, "data_fingerprint": snapshot.data_fingerprint, "runtime_config_fingerprint": snapshot.runtime_config_fingerprint, "weights_fingerprint": snapshot.weights_fingerprint, "trained_until": snapshot.trained_until, "training_sample_count": snapshot.training_sample_count},
                })
            timing["base_scoring_seconds"] += time.perf_counter() - stage_started
            prediction_records.extend(batch_records)
            stage_started = time.perf_counter()
            checkpoint_store.write_batch(
                batch_number, partition_name, batch, batch_records,
                input_fingerprint=view.run_input_fingerprint,
                spec_fingerprint=spec_fingerprint,
                processed_matches=len(prediction_records),
            )
            timing["persistence_seconds"] += time.perf_counter() - stage_started
            progress(
                phase="running", current_batch=batch_number,
                current_batch_id=batch["batch_id"],
                latest_checkpoint=f"checkpoint-{batch_number:05d}.json",
                total_batches=len(batch_specs),
                processed_matches=len(prediction_records),
                total_matches=total_matches,
                percent=round(
                    len(prediction_records) / max(total_matches, 1) * 80, 2
                ),
            )
        validation_records = [
            record for record in prediction_records if record["partition"] == "validation"
        ]
        holdout_records = [
            record for record in prediction_records if record["partition"] == "holdout"
        ]
        phase_dir = output_dir / "phases"
        validation_phase = phase_dir / "validation-metrics.json"
        holdout_phase = phase_dir / "holdout-metrics.json"

        def phase_metrics(path, records, phase, enable_bootstrap):
            if resume and path.is_file():
                payload = read_json(path)
                if payload.get("run_spec_fingerprint") != spec_fingerprint or payload.get("records_fingerprint") != scoring_fingerprint(records):
                    raise BacktestExecutionError(f"{phase} 指标阶段指纹不一致")
                return payload["metrics"]
            stage_started = time.perf_counter()
            metrics_value = build_metrics(
                records, SCIENTIFIC_MODELS, MODEL_BASELINES,
                iterations=self.config.bootstrap_iterations,
                seed=self.config.random_seed,
                enable_bootstrap=enable_bootstrap,
                progress=lambda completed, total, **_values: progress(
                    phase=f"{phase}_metrics", current_batch=len(batch_specs),
                    total_batches=len(batch_specs), processed_matches=len(prediction_records),
                    total_matches=total_matches,
                    percent=round((80 if phase == "validation" else 85) + completed / max(total, 1) * (5 if phase == "validation" else 14), 2),
                ),
            )
            atomic_write_json(path, {
                "schema_version": BACKTEST_SCHEMA_VERSION,
                "run_spec_fingerprint": spec_fingerprint,
                "records_fingerprint": scoring_fingerprint(records),
                "metrics": metrics_value,
            })
            timing["metrics_seconds"] += time.perf_counter() - stage_started
            return metrics_value

        validation_metrics = phase_metrics(
            validation_phase, validation_records, "validation", False
        )
        holdout_metrics = phase_metrics(
            holdout_phase, holdout_records, "holdout", True
        )
        metrics = {
            "schema_version": BACKTEST_SCHEMA_VERSION,
            "validation": validation_metrics,
            "holdout": holdout_metrics,
        }
        admission = build_admission(
            holdout_metrics, self.config, self.provenance,
            len(view.accepted), len(view.partitions.holdout),
        )
        if self.config.research_only:
            admission = _research_only_admission(admission)
        score_fingerprint = scoring_fingerprint(prediction_records)
        protocol = {
            "version": BACKTEST_PROTOCOL_VERSION,
            "random_seed": self.config.random_seed,
            "bootstrap_iterations": self.config.bootstrap_iterations,
            "runtime_config_fingerprint": expected_config_fingerprint,
            "weights_fingerprint": expected_weights_fingerprint,
            "feature_index_fingerprint": feature_index.data_fingerprint,
            "artifact_inventory": artifact_catalog.public_inventory(),
            "code_commit": self.provenance["code_commit"],
            "data_fingerprint": view.run_input_fingerprint,
            "dataset_batch_id": self.config.dataset_batch_id,
            "scoring_fingerprint": score_fingerprint,
        }
        result_fingerprint = scientific_fingerprint(
            prediction_records, metrics, admission, protocol
        )
        manifest = {
            "schema_version": BACKTEST_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "as_of": self.config.as_of,
            "dataset_batch_id": self.config.dataset_batch_id,
            "source": {
                key: value for key, value in (source or {"kind": "repository"}).items()
                if key not in {"locator", "path"}
            },
            "provenance": self.provenance,
            "protocol": protocol,
            "data": {
                "fingerprint": view.run_input_fingerprint,
                "source_matches_fingerprint": view.source_matches_fingerprint,
                "accepted_matches_fingerprint": view.accepted_matches_fingerprint,
                "odds_fingerprint": view.odds_fingerprint,
                "membership_fingerprint": view.membership_fingerprint,
                "fetched_matches": len(view.matches),
                "accepted_matches": len(view.accepted),
                "excluded": view.excluded,
                "partitions": view.partitions.public_summary(),
            },
            "result_fingerprint": result_fingerprint,
            "scoring_fingerprint": score_fingerprint,
        }
        performance = {
            "schema_version": 1,
            "run_id": run_id,
            "run_input_fingerprint": view.run_input_fingerprint,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "matches": len(prediction_records),
            "batches": len(batch_specs),
            "resumed_from_batch": completed_batches,
            "timings": {
                key: round(value, 6) for key, value in timing.items()
            },
            "peak_memory_bytes": _peak_memory_bytes(),
            "total_seconds": round(time.perf_counter() - started, 6),
        }
        performance["target_seconds"] = 900
        performance["target_met"] = performance["total_seconds"] <= 900
        stage_started = time.perf_counter()
        atomic_write_jsonl(output_dir / "predictions.jsonl", prediction_records)
        atomic_write_json(output_dir / "metrics.json", metrics)
        atomic_write_json(output_dir / "admission.json", admission)
        atomic_write_text(output_dir / "report.md", _render_report(manifest, metrics, admission))
        timing["persistence_seconds"] += time.perf_counter() - stage_started
        performance["timings"]["persistence_seconds"] = round(
            timing["persistence_seconds"], 6
        )
        atomic_write_json(output_dir / "performance.json", performance)
        atomic_write_json(output_dir / "manifest.json", manifest)
        progress(
            phase="completed", current_batch=len(batch_specs),
            total_batches=len(batch_specs), processed_matches=len(prediction_records),
            total_matches=total_matches, percent=100,
        )
        return {
            "run_id": run_id,
            "output_dir": output_dir,
            "manifest": manifest,
            "metrics": metrics,
            "admission": admission,
            "result_fingerprint": result_fingerprint,
            "scoring_fingerprint": score_fingerprint,
            "insufficient_data": not admission["formal_data_ready"],
        }
