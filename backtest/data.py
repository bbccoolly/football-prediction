"""Input gates, temporal splits and market consensus construction."""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Iterable, Mapping

from backtest.contracts import BacktestConfig, BacktestDataError
from data.match_repository import canonical_json, fingerprint, normalize_timestamp


@dataclass(frozen=True)
class DataPartitions:
    training: tuple[dict, ...]
    validation: tuple[dict, ...]
    holdout: tuple[dict, ...]
    training_end_date: str
    validation_end_date: str

    def public_summary(self):
        total = len(self.training) + len(self.validation) + len(self.holdout)
        return {
            "training": len(self.training),
            "validation": len(self.validation),
            "holdout": len(self.holdout),
            "training_ratio": len(self.training) / total,
            "validation_ratio": len(self.validation) / total,
            "holdout_ratio": len(self.holdout) / total,
            "training_end_date": self.training_end_date,
            "validation_end_date": self.validation_end_date,
        }


@dataclass(frozen=True)
class BacktestHistoryView:
    matches: tuple[dict, ...]
    accepted: tuple[dict, ...]
    excluded: dict
    partitions: DataPartitions
    market_inputs: dict
    odds_inventory: tuple[dict, ...]
    batch_specs: tuple[tuple[str, dict], ...]
    source_matches_fingerprint: str
    accepted_matches_fingerprint: str
    odds_fingerprint: str
    membership_fingerprint: str
    run_input_fingerprint: str
    dataset: dict | None
    _training_keys: tuple[str, ...]

    @classmethod
    def load(cls, repository, config):
        dataset = None
        if config.dataset_batch_id:
            dataset = repository.get_dataset_batch(config.dataset_batch_id)
            if dataset is None:
                raise BacktestDataError("数据 batch 不存在")
            matches = repository.list_dataset_batch_matches(
                config.dataset_batch_id, as_of=config.as_of
            )
        else:
            matches = repository.list_matches(as_of=config.as_of)
        accepted, excluded = filter_eligible_matches(matches, config)
        partitions = split_by_natural_day(accepted, config)
        cutoffs = {
            match["match_id"]: (
                normalize_timestamp(match["kickoff_utc"])
                if match["time_precision"] == "minute"
                else f"{match['event_date']}T00:00:00+00:00"
            )
            for match in accepted
        }
        odds_by_match = repository.list_backtest_odds_bulk(
            cutoffs, config.dataset_batch_id
        )
        market_inputs = {
            match_id: market_consensus(rows)
            for match_id, rows in odds_by_match.items()
        }
        odds_inventory = tuple(
            {
                key: row.get(key)
                for key in (
                    "odds_snapshot_id", "match_id", "company", "captured_at",
                    "home_odds", "draw_odds", "away_odds", "source",
                    "evidence_type", "source_file_sha256", "source_record_pk",
                    "batch_id",
                )
            }
            for match_id in sorted(odds_by_match)
            for row in odds_by_match[match_id]
        )
        batch_specs = []
        for partition_name, values in (
            ("validation", partitions.validation), ("holdout", partitions.holdout)
        ):
            for batch in walk_forward_batches(values, prefix=partition_name):
                batch_specs.append((partition_name, batch))
        source_fp = accepted_data_fingerprint(matches, {})
        accepted_fp = accepted_data_fingerprint(accepted, excluded)
        odds_fp = fingerprint(odds_inventory)
        membership_fp = fingerprint([
            match["match_id"] for match in matches
        ])
        input_fp = fingerprint({
            "dataset_batch_id": config.dataset_batch_id,
            "as_of": config.as_of,
            "source_matches": source_fp,
            "accepted_matches": accepted_fp,
            "odds": odds_fp,
            "membership": membership_fp,
        })
        training_keys = tuple(_training_key(match) for match in accepted)
        return cls(
            matches=tuple(matches), accepted=tuple(accepted), excluded=excluded,
            partitions=partitions, market_inputs=market_inputs,
            odds_inventory=odds_inventory, batch_specs=tuple(batch_specs),
            source_matches_fingerprint=source_fp,
            accepted_matches_fingerprint=accepted_fp,
            odds_fingerprint=odds_fp, membership_fingerprint=membership_fp,
            run_input_fingerprint=input_fp, dataset=dict(dataset) if dataset else None,
            _training_keys=training_keys,
        )

    def history_before(self, cutoff):
        normalized = normalize_timestamp(cutoff)
        return self.accepted[:bisect_left(self._training_keys, normalized)]

    @property
    def membership_complete(self):
        return not self.dataset or self.dataset.get("membership_status") == "complete"


def _training_key(match):
    if match["time_precision"] == "minute":
        return normalize_timestamp(match["kickoff_utc"])
    return f"{match['event_date']}T00:00:00+00:00"


def _match_sort_key(match):
    return (
        match["event_date"], match.get("kickoff_utc") or "", match["match_id"]
    )


def filter_eligible_matches(matches: Iterable[Mapping], config: BacktestConfig):
    cutoff = datetime.fromisoformat(config.as_of)
    accepted = []
    excluded = Counter()
    for raw in matches:
        match = dict(raw)
        reason = None
        if match.get("status") != "finished":
            reason = "not_finished"
        elif match.get("data_quality_status", "valid") != "valid":
            reason = "data_quality_invalid"
        elif match.get("home_team_type") not in {"national", "club"}:
            reason = "invalid_home_team_type"
        elif match.get("home_team_type") != match.get("away_team_type"):
            reason = "mixed_team_types"
        elif (
            isinstance(match.get("home_goals"), bool)
            or isinstance(match.get("away_goals"), bool)
            or not isinstance(match.get("home_goals"), int)
            or not isinstance(match.get("away_goals"), int)
            or match["home_goals"] < 0 or match["away_goals"] < 0
        ):
            reason = "invalid_score"
        elif match.get("time_precision") == "minute":
            try:
                kickoff = datetime.fromisoformat(
                    str(match.get("kickoff_utc") or "").replace("Z", "+00:00")
                )
                if kickoff.tzinfo is None:
                    raise ValueError
                if kickoff.astimezone(timezone.utc) >= cutoff:
                    reason = "future_result"
            except ValueError:
                reason = "invalid_kickoff"
        elif match.get("time_precision") == "date":
            try:
                if date.fromisoformat(match["event_date"]) >= cutoff.date():
                    reason = "future_result"
            except (KeyError, TypeError, ValueError):
                reason = "invalid_event_date"
        else:
            reason = "invalid_time_precision"
        if reason:
            excluded[reason] += 1
        else:
            accepted.append(match)
    accepted.sort(key=_match_sort_key)
    return accepted, dict(sorted(excluded.items()))


def split_by_natural_day(matches, config: BacktestConfig):
    grouped = defaultdict(list)
    for match in matches:
        grouped[match["event_date"]].append(match)
    days = sorted(grouped)
    if len(matches) < config.minimum_research_matches or len(days) < 3:
        raise BacktestDataError(
            f"研究回测至少需要 {config.minimum_research_matches} 场和 3 个自然日"
        )
    cumulative = []
    count = 0
    for day_value in days:
        count += len(grouped[day_value])
        cumulative.append(count)
    total = len(matches)
    candidates = []
    for training_day_index in range(len(days) - 2):
        training_count = cumulative[training_day_index]
        for validation_day_index in range(training_day_index + 1, len(days) - 1):
            validation_end_count = cumulative[validation_day_index]
            score = (
                abs(training_count / total - config.training_ratio)
                + abs(
                    validation_end_count / total
                    - (config.training_ratio + config.validation_ratio)
                )
            )
            candidates.append((score, training_day_index, validation_day_index))
    _, training_index, validation_index = min(candidates)
    training_days = set(days[: training_index + 1])
    validation_days = set(days[training_index + 1 : validation_index + 1])
    holdout_days = set(days[validation_index + 1 :])
    return DataPartitions(
        training=tuple(m for m in matches if m["event_date"] in training_days),
        validation=tuple(m for m in matches if m["event_date"] in validation_days),
        holdout=tuple(m for m in matches if m["event_date"] in holdout_days),
        training_end_date=days[training_index],
        validation_end_date=days[validation_index],
    )


def walk_forward_batches(matches, prefix=None):
    date_only_days = {
        match["event_date"] for match in matches if match["time_precision"] == "date"
    }
    grouped = defaultdict(list)
    for match in matches:
        if match["event_date"] in date_only_days:
            key = (match["event_date"], "date")
        else:
            key = (match["event_date"], normalize_timestamp(match["kickoff_utc"]))
        grouped[key].append(match)
    result = []
    for index, key in enumerate(sorted(grouped), start=1):
        batch = tuple(sorted(grouped[key], key=_match_sort_key))
        if key[1] == "date":
            cutoff = datetime.combine(
                date.fromisoformat(key[0]), time.min, tzinfo=timezone.utc
            ).isoformat()
        else:
            cutoff = key[1]
        result.append({
            "batch_id": f"{prefix + '-' if prefix else ''}batch-{index:05d}",
            "cutoff": cutoff,
            "matches": batch,
        })
    return tuple(result)


def outcome_key(match):
    if match["home_goals"] > match["away_goals"]:
        return "home_win"
    if match["home_goals"] == match["away_goals"]:
        return "draw"
    return "away_win"


def proportion_baselines(history, competition_id):
    scoped = [m for m in history if m["competition_id"] == competition_id]
    if not scoped:
        unavailable = {"available": False, "reason": "insufficient_competition_history"}
        return {
            "expanding_competition_rate": unavailable,
            "recent_100_competition_rate": unavailable,
        }

    def probabilities(values):
        counts = Counter(outcome_key(match) for match in values)
        total = len(values) + 3
        return {
            "available": True,
            "home_win": (counts["home_win"] + 1) / total,
            "draw": (counts["draw"] + 1) / total,
            "away_win": (counts["away_win"] + 1) / total,
            "history_matches": len(values),
        }

    return {
        "expanding_competition_rate": probabilities(scoped),
        "recent_100_competition_rate": probabilities(scoped[-100:]),
    }


def market_consensus(rows):
    valid = []
    for row in rows:
        try:
            odds = tuple(float(row[key]) for key in ("home_odds", "draw_odds", "away_odds"))
        except (KeyError, TypeError, ValueError):
            continue
        if any(not math.isfinite(value) or value <= 1 for value in odds):
            continue
        implied = [1.0 / value for value in odds]
        total = sum(implied)
        valid.append((row, tuple(value / total for value in implied)))
    if not valid:
        return None
    medians = [statistics.median(values[index] for _, values in valid) for index in range(3)]
    total = sum(medians)
    probabilities = tuple(value / total for value in medians)
    selected = [row for row, _ in valid]
    evidence_types = sorted({row.get("evidence_type", "captured_at") for row in selected})
    result = {
        "probabilities": probabilities,
        "synthetic_odds": tuple(1.0 / value for value in probabilities),
        "source": "market_consensus_v2",
        "companies": [row["company"] for row in selected],
        "odds_snapshot_ids": [row["odds_snapshot_id"] for row in selected],
        "evidence_types": evidence_types,
        "source_record_pks": [row.get("source_record_pk") for row in selected if row.get("source_record_pk")],
    }
    captured = [row.get("captured_at") for row in selected if row.get("captured_at")]
    if captured:
        result["captured_at"] = max(captured)
    return result


def accepted_data_fingerprint(matches, excluded):
    canonical = [{
        key: match.get(key)
        for key in (
            "match_id", "competition_id", "season", "event_date", "kickoff_utc",
            "time_precision", "home_team_id", "away_team_id", "home_team_type",
            "away_team_type", "neutral", "status", "home_goals", "away_goals",
            "data_quality_status", "source_count", "sources",
        )
    } for match in matches]
    return fingerprint({"matches": canonical, "excluded": excluded})
