"""Paired multiclass metrics and stratified block bootstrap."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

import numpy as np


OUTCOMES = ("home_win", "draw", "away_win")
METRIC_NAMES = ("brier", "log_loss", "rps", "ece", "accuracy")


def _valid_prediction(value):
    if not isinstance(value, dict) or not value.get("available"):
        return False
    probabilities = [value.get(key) for key in OUTCOMES]
    return (
        all(isinstance(item, (int, float)) and math.isfinite(item) for item in probabilities)
        and all(0 <= item <= 1 for item in probabilities)
        and abs(sum(probabilities) - 1.0) <= 1e-6
    )


def metric_values(items):
    if not items:
        return None
    brier_values = []
    log_loss_values = []
    rps_values = []
    correct = []
    confidences = []
    for actual, prediction in items:
        probabilities = np.array([prediction[key] for key in OUTCOMES], dtype=float)
        target = np.array([1.0 if actual == key else 0.0 for key in OUTCOMES])
        brier_values.append(float(np.square(probabilities - target).sum()))
        actual_index = OUTCOMES.index(actual)
        log_loss_values.append(-math.log(max(1e-15, min(1.0, probabilities[actual_index]))))
        rps_values.append(float(np.square(np.cumsum(probabilities)[:2] - np.cumsum(target)[:2]).sum() / 2))
        predicted_index = int(np.argmax(probabilities))
        correct.append(1.0 if predicted_index == actual_index else 0.0)
        confidences.append(float(probabilities[predicted_index]))
    ece = 0.0
    sample_count = len(items)
    for bucket in range(10):
        lower, upper = bucket / 10, (bucket + 1) / 10
        indexes = [
            index for index, confidence in enumerate(confidences)
            if lower <= confidence < upper or (bucket == 9 and confidence == 1.0)
        ]
        if indexes:
            accuracy = sum(correct[index] for index in indexes) / len(indexes)
            average_confidence = sum(confidences[index] for index in indexes) / len(indexes)
            ece += len(indexes) / sample_count * abs(accuracy - average_confidence)
    return {
        "brier": float(np.mean(brier_values)),
        "log_loss": float(np.mean(log_loss_values)),
        "rps": float(np.mean(rps_values)),
        "ece": ece,
        "accuracy": float(np.mean(correct)),
    }


def _percentile_summary(values):
    return {
        "mean": float(np.mean(values)),
        "lower_95": float(np.percentile(values, 2.5)),
        "upper_95": float(np.percentile(values, 97.5)),
    }


def bootstrap_comparison(records, model_id, baseline_id, iterations=2000, seed=42):
    if not records:
        return None
    anchor = min(date.fromisoformat(record["match"]["event_date"]) for record in records)
    blocks_by_competition = defaultdict(lambda: defaultdict(list))
    for record in records:
        day_value = date.fromisoformat(record["match"]["event_date"])
        block_index = (day_value - anchor).days // 7
        blocks_by_competition[record["match"]["competition_id"]][block_index].append(record)
    total_blocks = sum(len(blocks) for blocks in blocks_by_competition.values())
    if total_blocks < 2:
        return None
    rng = np.random.default_rng(seed)
    model_samples = {name: [] for name in METRIC_NAMES}
    baseline_samples = {name: [] for name in METRIC_NAMES}
    delta_samples = {name: [] for name in METRIC_NAMES}
    for _ in range(iterations):
        sampled = []
        for competition_id in sorted(blocks_by_competition):
            blocks = blocks_by_competition[competition_id]
            keys = sorted(blocks)
            choices = rng.integers(0, len(keys), size=len(keys))
            for choice in choices:
                sampled.extend(blocks[keys[int(choice)]])
        model_metrics = metric_values([
            (record["actual"], record["predictions"][model_id]) for record in sampled
        ])
        baseline_metrics = metric_values([
            (record["actual"], record["predictions"][baseline_id]) for record in sampled
        ])
        for metric_name in METRIC_NAMES:
            model_value = model_metrics[metric_name]
            baseline_value = baseline_metrics[metric_name]
            model_samples[metric_name].append(model_value)
            baseline_samples[metric_name].append(baseline_value)
            delta_samples[metric_name].append(model_value - baseline_value)
    return {
        "iterations": iterations,
        "seed": seed,
        "block_count": total_blocks,
        "model": {name: _percentile_summary(values) for name, values in model_samples.items()},
        "baseline": {name: _percentile_summary(values) for name, values in baseline_samples.items()},
        "delta": {name: _percentile_summary(values) for name, values in delta_samples.items()},
    }


def evaluate_model(
    records, model_id, baseline_ids, iterations=2000, seed=42,
    bootstrap_enabled=True,
    bootstrap_baseline_id="expanding_competition_rate",
):
    valid = [record for record in records if _valid_prediction(record["predictions"].get(model_id))]
    result = {
        "eligible_samples": len(records),
        "valid_predictions": len(valid),
        "coverage": len(valid) / len(records) if records else 0.0,
        "unavailable_reasons": {},
        "metrics": metric_values([
            (record["actual"], record["predictions"][model_id]) for record in valid
        ]),
        "comparisons": {},
    }
    reasons = defaultdict(int)
    for record in records:
        prediction = record["predictions"].get(model_id) or {}
        if not _valid_prediction(prediction):
            reasons[prediction.get("reason") or prediction.get("status") or "unavailable"] += 1
    result["unavailable_reasons"] = dict(sorted(reasons.items()))
    for baseline_id in baseline_ids:
        paired = [
            record for record in valid
            if _valid_prediction(record["predictions"].get(baseline_id))
        ]
        model_metrics = metric_values([
            (record["actual"], record["predictions"][model_id]) for record in paired
        ])
        baseline_metrics = metric_values([
            (record["actual"], record["predictions"][baseline_id]) for record in paired
        ])
        result["comparisons"][baseline_id] = {
            "paired_samples": len(paired),
            "model_metrics": model_metrics,
            "baseline_metrics": baseline_metrics,
            "delta": (
                {
                    name: model_metrics[name] - baseline_metrics[name]
                    for name in METRIC_NAMES
                }
                if model_metrics and baseline_metrics else None
            ),
            "bootstrap": (
                bootstrap_comparison(
                    paired, model_id, baseline_id, iterations=iterations, seed=seed
                )
                if bootstrap_enabled and baseline_id == bootstrap_baseline_id else None
            ),
        }
    return result


def _scope_groups(records):
    groups = {"overall": {"all": records}}
    fields = {
        "competition": lambda record: record["match"]["competition_id"],
        "season": lambda record: record["match"].get("season") or "unknown",
        "neutral": lambda record: "neutral" if record["match"]["neutral"] else "non_neutral",
        "team_type": lambda record: record["match"]["team_type"],
        "coverage_grade": lambda record: record["match"]["coverage_grade"],
    }
    for group_name, key_function in fields.items():
        grouped = defaultdict(list)
        for record in records:
            grouped[str(key_function(record))].append(record)
        groups[group_name] = dict(sorted(grouped.items()))
    return groups


def build_metrics(
    records, model_ids, baseline_ids, iterations=2000, seed=42,
    enable_bootstrap=True, progress=None,
):
    progress = progress or (lambda **_values: None)
    output = {}
    groups = _scope_groups(records)
    total_work = sum(len(scopes) * len(model_ids) for scopes in groups.values())
    completed_work = 0
    for group_name, scopes in groups.items():
        output[group_name] = {}
        for scope_name, scoped_records in scopes.items():
            model_results = {}
            for model_id in model_ids:
                model_results[model_id] = evaluate_model(
                    scoped_records, model_id,
                    (() if model_id in baseline_ids else baseline_ids),
                    iterations=iterations, seed=seed,
                    bootstrap_enabled=(
                        enable_bootstrap
                        and group_name in {"overall", "competition"}
                    ),
                )
                completed_work += 1
                progress(
                    completed=completed_work, total=total_work,
                    group=group_name, scope=scope_name, model_id=model_id,
                )
            output[group_name][scope_name] = {
                "samples": len(scoped_records), "models": model_results,
            }
    return output
