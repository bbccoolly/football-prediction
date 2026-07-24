from datetime import datetime, timezone

import pytest

from backtest.contracts import BacktestConfig
from backtest.data import market_consensus, split_by_natural_day, walk_forward_batches


def _match(match_id, day, precision="date", kickoff=None):
    return {
        "match_id": match_id,
        "event_date": day,
        "kickoff_utc": kickoff,
        "time_precision": precision,
    }


def test_split_jointly_selects_natural_day_boundaries(tmp_path):
    matches = []
    for day in range(1, 11):
        for index in range(3):
            matches.append(_match(f"{day}-{index}", f"2025-01-{day:02d}"))
    config = BacktestConfig(
        as_of="2026-01-01T00:00:00Z", output_root=tmp_path,
        minimum_research_matches=1,
    )

    partitions = split_by_natural_day(matches, config)

    assert len(partitions.training) == 18
    assert len(partitions.validation) == 6
    assert len(partitions.holdout) == 6
    assert {m["event_date"] for m in partitions.training}.isdisjoint(
        {m["event_date"] for m in partitions.validation}
    )


def test_date_precision_forces_the_entire_day_into_one_batch():
    matches = [
        _match("date", "2025-01-01"),
        _match("minute", "2025-01-01", "minute", "2025-01-01T18:00:00Z"),
        _match("later", "2025-01-02", "minute", "2025-01-02T18:00:00Z"),
    ]

    batches = walk_forward_batches(matches)

    assert [[m["match_id"] for m in batch["matches"]] for batch in batches] == [
        ["date", "minute"], ["later"],
    ]
    assert batches[0]["cutoff"] == "2025-01-01T00:00:00+00:00"


def test_market_consensus_uses_median_of_devigged_company_probabilities():
    rows = [
        {
            "odds_snapshot_id": "a", "company": "A",
            "captured_at": "2025-01-01T10:00:00+00:00",
            "home_odds": 2.0, "draw_odds": 4.0, "away_odds": 4.0,
        },
        {
            "odds_snapshot_id": "b", "company": "B",
            "captured_at": "2025-01-01T11:00:00+00:00",
            "home_odds": 4.0, "draw_odds": 4.0, "away_odds": 2.0,
        },
    ]

    result = market_consensus(rows)

    assert result["source"] == "market_consensus_v2"
    assert result["evidence_types"] == ["captured_at"]
    assert result["companies"] == ["A", "B"]
    assert sum(result["probabilities"]) == pytest.approx(1.0)
    assert result["probabilities"][0] == pytest.approx(result["probabilities"][2])
