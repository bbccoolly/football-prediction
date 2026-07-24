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
    grouped_components = []
    # Draw in the same iteration/competition order as the previous implementation,
    # then aggregate immutable block statistics with NumPy instead of rescoring rows.
    choices_by_competition = []
    for competition_id in sorted(blocks_by_competition):
        blocks = blocks_by_competition[competition_id]
        keys = sorted(blocks)
        choices_by_competition.append(np.empty((iterations, len(keys)), dtype=np.int64))
    for iteration in range(iterations):
        for index, competition_id in enumerate(sorted(blocks_by_competition)):
            block_count = choices_by_competition[index].shape[1]
            choices_by_competition[index][iteration] = rng.integers(
                0, block_count, size=block_count
            )
    for competition_id in sorted(blocks_by_competition):
        blocks = blocks_by_competition[competition_id]
        keys = sorted(blocks)
        grouped_components.append((
            _bootstrap_block_components([blocks[key] for key in keys], model_id),
            _bootstrap_block_components([blocks[key] for key in keys], baseline_id),
        ))

    model_samples = {name: np.zeros(iterations, dtype=float) for name in METRIC_NAMES}
    baseline_samples = {name: np.zeros(iterations, dtype=float) for name in METRIC_NAMES}
    for components, choices in zip(grouped_components, choices_by_competition):
        for target, sample in ((model_samples, components[0]), (baseline_samples, components[1])):
            for metric_name in ("brier", "log_loss", "rps", "correct", "count"):
                target[metric_name] = target.get(metric_name, np.zeros(iterations)) + np.take(
                    sample[metric_name], choices
                ).sum(axis=1)
            for bucket in range(10):
                for prefix in ("bucket", "bucket_correct", "bucket_confidence"):
                    key = f"{prefix}_{bucket}"
                    target[key] = target.get(key, np.zeros(iterations)) + np.take(
                        sample[key], choices
                    ).sum(axis=1)
    for target in (model_samples, baseline_samples):
        count = np.maximum(target.pop("count"), 1.0)
        target["brier"] /= count
        target["log_loss"] /= count
        target["rps"] /= count
        target["accuracy"] = target.pop("correct") / count
        ece = np.zeros(iterations, dtype=float)
        for bucket in range(10):
            bucket_count = target.pop(f"bucket_{bucket}")
            bucket_correct = target.pop(f"bucket_correct_{bucket}", None)
            bucket_confidence = target.pop(f"bucket_confidence_{bucket}", None)
            if bucket_correct is not None and bucket_confidence is not None:
                ece += bucket_count / count * np.abs(
                    bucket_correct / np.maximum(bucket_count, 1.0)
                    - bucket_confidence / np.maximum(bucket_count, 1.0)
                )
        target["ece"] = ece
    delta_samples = {
        name: model_samples[name] - baseline_samples[name] for name in METRIC_NAMES
    }
    return {
        "iterations": iterations,
        "seed": seed,
        "block_count": total_blocks,
        "model": {name: _percentile_summary(model_samples[name]) for name in METRIC_NAMES},
        "baseline": {name: _percentile_summary(baseline_samples[name]) for name in METRIC_NAMES},
        "delta": {name: _percentile_summary(delta_samples[name]) for name in METRIC_NAMES},
    }


def _bootstrap_block_components(blocks, model_id):
    values = {
        name: np.zeros(len(blocks), dtype=float)
        for name in ("brier", "log_loss", "rps", "correct", "count")
    }
    values.update({
        f"bucket_{bucket}": np.zeros(len(blocks), dtype=float)
        for bucket in range(10)
    })
    values.update({
        f"bucket_correct_{bucket}": np.zeros(len(blocks), dtype=float)
        for bucket in range(10)
    })
    values.update({
        f"bucket_confidence_{bucket}": np.zeros(len(blocks), dtype=float)
        for bucket in range(10)
    })
    for block_index, block in enumerate(blocks):
        for record in block:
            prediction = record["predictions"][model_id]
            probabilities = np.asarray(
                [prediction[key] for key in OUTCOMES], dtype=float
            )
            actual_index = OUTCOMES.index(record["actual"])
            target = np.zeros(3, dtype=float)
            target[actual_index] = 1.0
            confidence = float(probabilities[np.argmax(probabilities)])
            bucket = min(int(confidence * 10), 9)
            values["brier"][block_index] += np.square(probabilities - target).sum()
            values["log_loss"][block_index] -= math.log(
                max(1e-15, min(1.0, probabilities[actual_index]))
            )
            values["rps"][block_index] += (
                np.square(np.cumsum(probabilities)[:2] - np.cumsum(target)[:2]).sum()
                / 2
            )
            values["correct"][block_index] += float(np.argmax(probabilities) == actual_index)
            values["count"][block_index] += 1
            values[f"bucket_{bucket}"][block_index] += 1
            values[f"bucket_correct_{bucket}"][block_index] += float(
                np.argmax(probabilities) == actual_index
            )
            values[f"bucket_confidence_{bucket}"][block_index] += confidence
    return values


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
