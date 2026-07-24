"""Deterministic admission decisions based only on frozen holdout metrics."""

from __future__ import annotations

from backtest.contracts import CANDIDATE_MODELS, LEARNING_MODELS


PRIMARY_BASELINE = "expanding_competition_rate"


def _check(name, actual, threshold, passed):
    return {
        "name": name,
        "actual": actual,
        "threshold": threshold,
        "passed": bool(passed),
    }


def _decision_for_scope(model_metrics, config, formal_data_ready, minimum_scope_samples):
    comparison = model_metrics["comparisons"].get(PRIMARY_BASELINE, {})
    paired_samples = comparison.get("paired_samples", 0)
    if not formal_data_ready:
        status = (
            "research_only"
            if paired_samples >= config.minimum_research_pairs
            else "insufficient_data"
        )
        return {
            "status": status,
            "reason": "formal_data_gate_not_met",
            "checks": [],
        }
    model_values = comparison.get("model_metrics")
    baseline_values = comparison.get("baseline_metrics")
    bootstrap = comparison.get("bootstrap")
    if not model_values or not baseline_values or not bootstrap:
        return {
            "status": "rejected",
            "reason": "paired_metrics_or_bootstrap_unavailable",
            "checks": [],
        }
    checks = [
        _check(
            "scope_samples", model_metrics["eligible_samples"], minimum_scope_samples,
            model_metrics["eligible_samples"] >= minimum_scope_samples,
        ),
        _check(
            "coverage", model_metrics["coverage"], config.minimum_coverage,
            model_metrics["coverage"] >= config.minimum_coverage,
        ),
        _check(
            "log_loss_improvement",
            baseline_values["log_loss"] - model_values["log_loss"],
            config.minimum_log_loss_improvement,
            baseline_values["log_loss"] - model_values["log_loss"]
            >= config.minimum_log_loss_improvement,
        ),
        _check(
            "log_loss_delta_upper_95",
            bootstrap["delta"]["log_loss"]["upper_95"], 0.0,
            bootstrap["delta"]["log_loss"]["upper_95"] <= 0.0,
        ),
        _check(
            "brier_not_worse", model_values["brier"], baseline_values["brier"],
            model_values["brier"] <= baseline_values["brier"],
        ),
        _check(
            "rps_not_worse", model_values["rps"], baseline_values["rps"],
            model_values["rps"] <= baseline_values["rps"],
        ),
        _check(
            "ece_increase",
            model_values["ece"] - baseline_values["ece"],
            config.maximum_ece_increase,
            model_values["ece"] - baseline_values["ece"] <= config.maximum_ece_increase,
        ),
    ]
    admitted = all(check["passed"] for check in checks)
    return {
        "status": "admitted" if admitted else "rejected",
        "reason": "all_checks_passed" if admitted else "one_or_more_checks_failed",
        "checks": checks,
    }


def build_admission(metrics, config, provenance, accepted_count, holdout_count):
    provenance_ready = (
        provenance.get("code_commit") not in {None, "", "unknown"}
        and not provenance.get("code_dirty", True)
    )
    global_data_ready = (
        accepted_count >= config.minimum_formal_matches
        and holdout_count >= config.minimum_holdout_matches
        and provenance_ready
    )
    overall_models = metrics["overall"]["all"]["models"]
    competitions = metrics.get("competition", {})
    decisions = {}
    for model_id in CANDIDATE_MODELS:
        decision = _decision_for_scope(
            overall_models[model_id], config, global_data_ready,
            config.minimum_holdout_matches,
        )
        competition_decisions = {}
        for competition_id, scope in competitions.items():
            scope_ready = (
                accepted_count >= config.minimum_formal_matches
                and provenance_ready
                and scope["samples"] >= config.minimum_competition_holdout_matches
            )
            competition_decisions[competition_id] = _decision_for_scope(
                scope["models"][model_id], config, scope_ready,
                config.minimum_competition_holdout_matches,
            )
        decisions[model_id] = {
            **decision,
            "competitions": competition_decisions,
        }
    for model_id in LEARNING_MODELS:
        decisions[model_id] = {
            "status": "not_evaluated",
            "reason": "learning_model_loading_out_of_scope",
            "checks": [],
            "competitions": {},
        }
    return {
        "schema_version": 1,
        "primary_baseline": PRIMARY_BASELINE,
        "formal_data_ready": global_data_ready,
        "provenance_ready": provenance_ready,
        "decisions": decisions,
        "production_weights_changed": False,
    }
