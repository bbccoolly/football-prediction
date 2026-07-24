import sqlite3

import pytest

from data.football_data import FootballDataError, build_manifest, parse_csv
from data.match_repository import MatchRepository
from data.migrations import SCHEMA_V1


CSV = b"""Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,B365CH,B365CD,B365CA\nD1,23/08/2024,FC Bayern Munich,Borussia Dortmund,2,1,H,1.50,4.50,6.00\n"""


def test_parser_creates_date_precision_record_and_declared_closing_odds():
    records = parse_csv(CSV, season_code="2425", division="D1", observed_at="2026-01-01T00:00:00+00:00")

    assert len(records) == 1
    assert records[0].record.season == "2024/25"
    assert records[0].record.time_precision == "date"
    assert records[0].closing_odds[0].company == "B365"


def test_parser_rejects_score_result_mismatch():
    invalid = CSV.replace(b",H,1.50", b",A,1.50")
    with pytest.raises(FootballDataError, match="FTR"):
        parse_csv(invalid, season_code="2425", division="D1", observed_at="2026-01-01T00:00:00+00:00")


def test_v1_database_migrates_to_v3(tmp_path):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.executescript(SCHEMA_V1)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()

    repository = MatchRepository(database)
    repository.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='historical_closing_odds'"
        ).fetchone()
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dataset_batch_matches'"
        ).fetchone()


def test_batch_import_keeps_declared_odds_out_of_live_query(tmp_path):
    records = parse_csv(CSV, season_code="2425", division="D1", observed_at="2026-01-01T00:00:00+00:00")
    source_file = {"season_code": "2425", "division": "D1", "url": "https://example.invalid/D1.csv", "sha256": records[0].source_file_sha256, "bytes": len(CSV), "filename": "2425-D1.csv"}
    manifest = build_manifest([source_file])
    repository = MatchRepository(tmp_path / "matches.db")
    repository.initialize()
    run_id = repository.create_sync_run("test")
    counts = repository.import_dataset_batch(manifest, records, run_id)
    match = repository.list_matches()[0]

    assert counts["closing_odds"] == 1
    assert repository.list_latest_pre_match_odds(match["match_id"], "2026-01-01T00:00:00+00:00") == []
    rows = repository.list_backtest_odds(match["match_id"], "2024-08-24T00:00:00+00:00", manifest["batch_id"])
    assert rows[0]["evidence_type"] == "source_declared_closing"
    assert repository.list_dataset_batch_matches(manifest["batch_id"])[0]["match_id"] == match["match_id"]
    assert repository.get_dataset_batch(manifest["batch_id"])["membership_status"] == "complete"
