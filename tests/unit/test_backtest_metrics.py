from collections import defaultdict
from datetime import date

import numpy as np
import pytest

from backtest.admission import build_admission
from backtest.contracts import BacktestConfig, CANDIDATE_MODELS
from backtest.metrics import bootstrap_comparison, metric_values


def _prediction(home, draw, away):
    return {"available": True, "home_win": home, "draw": draw, "away_win": away}


def test_metric_formulas_match_fixed_multiclass_definitions():
    metrics = metric_values([("home_win", _prediction(0.5, 0.3, 0.2))])

    assert metrics["brier"] == pytest.approx(0.38)
    assert metrics["log_loss"] == pytest.approx(0.6931471805599453)
    assert metrics["rps"] == pytest.approx((0.5**2 + 0.2**2) / 2)
    assert metrics["ece"] == pytest.approx(0.5)
    assert metrics["accuracy"] == 1.0


def test_stratified_block_bootstrap_is_deterministic():
    records = []
    for index, day in enumerate(("2025-01-01", "2025-01-10", "2025-01-20")):
        records.append({
            "actual": "home_win",
            "match": {"event_date": day, "competition_id": "league"},
            "predictions": {
                "model": _prediction(0.6 + index * 0.05, 0.2, 0.2 - index * 0.05),
                "baseline": _prediction(0.4, 0.3, 0.3),
            },
        })

    first = bootstrap_comparison(records, "model", "baseline", iterations=50, seed=42)
    second = bootstrap_comparison(records, "model", "baseline", iterations=50, seed=42)

    assert first == second
    assert first["block_count"] == 3


def test_vectorized_bootstrap_matches_row_by_row_protocol():
    records = []
    for competition in ("league-a", "league-b"):
        for index, day in enumerate(("2025-01-01", "2025-01-10", "2025-01-20")):
            records.append({
                "actual": ("home_win", "draw", "away_win")[index],
                "match": {"event_date": day, "competition_id": competition},
                "predictions": {
                    "model": _prediction(0.55, 0.25, 0.20),
                    "baseline": _prediction(0.40, 0.30, 0.30),
                },
            })
    iterations = 25
    optimized = bootstrap_comparison(
        records, "model", "baseline", iterations=iterations, seed=42
    )
    anchor = min(date.fromisoformat(row["match"]["event_date"]) for row in records)
    grouped = defaultdict(lambda: defaultdict(list))
    for row in records:
        block = (date.fromisoformat(row["match"]["event_date"]) - anchor).days // 7
        grouped[row["match"]["competition_id"]][block].append(row)
    rng = np.random.default_rng(42)
    samples = {"model": defaultdict(list), "baseline": defaultdict(list)}
    for _ in range(iterations):
        sampled = []
        for competition in sorted(grouped):
            blocks = grouped[competition]
            keys = sorted(blocks)
            for choice in rng.integers(0, len(keys), size=len(keys)):
                sampled.extend(blocks[keys[int(choice)]])
        for model_id in ("model", "baseline"):
            metrics = metric_values([
                (row["actual"], row["predictions"][model_id]) for row in sampled
            ])
            for name, value in metrics.items():
                samples[model_id][name].append(value)
    for model_id in ("model", "baseline"):
        for metric_name, values in samples[model_id].items():
            expected = {
                "mean": np.mean(values),
                "lower_95": np.percentile(values, 2.5),
                "upper_95": np.percentile(values, 97.5),
            }
            assert optimized[model_id][metric_name] == pytest.approx(expected)


def test_insufficient_formal_data_cannot_produce_admitted(tmp_path):
    config = BacktestConfig(as_of="2026-01-01T00:00:00Z", output_root=tmp_path)
    model_result = {
        "eligible_samples": 50,
        "valid_predictions": 50,
        "coverage": 1.0,
        "comparisons": {"expanding_competition_rate": {"paired_samples": 50}},
    }
    metrics = {
        "overall": {"all": {"models": {
            model_id: model_result for model_id in CANDIDATE_MODELS
        }}},
        "competition": {},
    }

    admission = build_admission(
        metrics, config,
        {"code_commit": "a" * 40, "code_dirty": False},
        accepted_count=100, holdout_count=50,
    )

    assert {
        value["status"] for value in admission["decisions"].values()
    } <= {"research_only", "not_evaluated"}
