from data.match_repository import MatchRepository
from data.source_adapters import adapt_legacy_match


def _repository(tmp_path):
    repository = MatchRepository(tmp_path / "source.db")
    repository.initialize()
    record = adapt_legacy_match({
        "match_id": "source-1",
        "home_team": "拜仁", "away_team": "多特蒙德",
        "home_goals": 2, "away_goals": 1,
        "league": "德甲", "date_time": "2025-01-01T18:00:00Z",
    }, source="fixture")
    run_id = repository.create_sync_run("fixture")
    repository.import_source_records([record], run_id, sync_type="fixture")
    return repository


def test_repository_backup_preserves_logical_data(tmp_path):
    source = _repository(tmp_path)
    destination = tmp_path / "snapshot.db"

    source.backup_to(destination)
    snapshot = MatchRepository(destination)

    assert snapshot.list_matches() == source.list_matches()


def test_latest_pre_match_odds_selects_one_strict_snapshot_per_company(tmp_path):
    repository = _repository(tmp_path)
    match_id = repository.list_matches()[0]["match_id"]
    for company, captured_at, home_odds in (
        ("A", "2025-01-01T10:00:00Z", 2.0),
        ("A", "2025-01-01T12:00:00Z", 1.9),
        ("B", "2025-01-01T11:00:00Z", 2.1),
        ("C", "2025-01-01T18:00:00Z", 1.8),
    ):
        repository.save_odds_snapshot({
            "match_id": match_id, "company": company,
            "captured_at": captured_at, "home_odds": home_odds,
            "draw_odds": 3.2, "away_odds": 4.0, "source": "fixture",
        })

    rows = repository.list_latest_pre_match_odds(
        match_id, "2025-01-01T18:00:00Z"
    )

    assert [(row["company"], row["home_odds"]) for row in rows] == [
        ("A", 1.9), ("B", 2.1),
    ]

    expected = repository.list_backtest_odds(
        match_id, "2025-01-01T18:00:00Z"
    )
    bulk = repository.list_backtest_odds_bulk({
        match_id: "2025-01-01T18:00:00Z",
    })
    assert bulk[match_id] == expected
