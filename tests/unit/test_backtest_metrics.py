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
