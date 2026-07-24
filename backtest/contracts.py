"""Public contracts for reproducible walk-forward backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


BACKTEST_SCHEMA_VERSION = 1
BACKTEST_PROTOCOL_VERSION = "walk_forward_v1"
DEFAULT_OUTPUT_ROOT = Path("data/processed/backtests")
MODEL_BASELINES = (
    "expanding_competition_rate", "recent_100_competition_rate",
    "poisson", "elo", "market_odds",
)
CANDIDATE_MODELS = (
    "dixon_coles", "massey", "form", "head_to_head", "bayesian", "knn_similar",
)
LEARNING_MODELS = ("xgboost", "neural_net", "stacking")


class BacktestError(RuntimeError):
    code = "BACKTEST_FAILED"


class BacktestConfigurationError(BacktestError):
    code = "BACKTEST_INVALID_CONFIGURATION"


class BacktestDataError(BacktestError):
    code = "BACKTEST_INVALID_DATA"


class BacktestExecutionError(BacktestError):
    code = "BACKTEST_EXECUTION_FAILED"


def utc_iso(value: datetime | str) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        raise BacktestConfigurationError("时间必须包含时区")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class BacktestConfig:
    as_of: str
    output_root: Path = DEFAULT_OUTPUT_ROOT
    random_seed: int = 42
    bootstrap_iterations: int = 2000
    training_ratio: float = 0.60
    validation_ratio: float = 0.20
    holdout_ratio: float = 0.20
    minimum_research_matches: int = 30
    minimum_formal_matches: int = 1500
    minimum_holdout_matches: int = 300
    minimum_competition_holdout_matches: int = 100
    minimum_research_pairs: int = 30
    minimum_coverage: float = 0.95
    minimum_log_loss_improvement: float = 0.005
    maximum_ece_increase: float = 0.02

    def __post_init__(self):
        object.__setattr__(self, "as_of", utc_iso(self.as_of))
        object.__setattr__(self, "output_root", Path(self.output_root))
        if self.random_seed != 42:
            raise BacktestConfigurationError("PR 4 回测随机种子固定为 42")
        if self.bootstrap_iterations < 1:
            raise BacktestConfigurationError("Bootstrap 次数必须大于 0")
        ratio_sum = self.training_ratio + self.validation_ratio + self.holdout_ratio
        if abs(ratio_sum - 1.0) > 1e-12:
            raise BacktestConfigurationError("训练、验证和保留集比例之和必须为 1")
