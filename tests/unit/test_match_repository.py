import json
import sqlite3

import pytest

from data.match_repository import (
    MatchRepository,
    RepositoryNotInitializedError,
    RepositorySchemaError,
)
from data.source_adapters import adapt_legacy_match


def initialized_repository(tmp_path):
    repository = MatchRepository(tmp_path / "football.db")
    repository.initialize()
    return repository


def import_records(repository, records, run_type="test"):
    run_id = repository.create_sync_run(run_type)
    return repository.import_source_records(records, run_id, sync_type=run_type)


def test_repository_requires_explicit_initialization(tmp_path):
    database = tmp_path / "missing" / "football.db"
    repository = MatchRepository(database)

    with pytest.raises(RepositoryNotInitializedError):
        repository.list_matches()

    assert not database.exists()


def test_repository_initializes_schema_and_seed_aliases(tmp_path):
    repository = initialized_repository(tmp_path)

    team = repository.resolve_team("FC Bayern München", "openligadb", "club")

    assert team["canonical_name"] == "拜仁"
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"matches", "source_records", "sync_runs", "odds_snapshots"} <= tables


def test_repository_rejects_unknown_schema_version(tmp_path):
    database = tmp_path / "football.db"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(RepositorySchemaError, match="版本不兼容"):
        MatchRepository(database).list_matches()


def test_import_is_idempotent_and_source_revision_updates_match(tmp_path):
    repository = initialized_repository(tmp_path)
    first_payload = {
        "match_id": "openliga-1",
        "home_team": "FC Bayern München",
        "away_team": "Borussia Dortmund",
        "home_goals": 1,
        "away_goals": 0,
        "league": "德甲",
        "date": "2025-01-01",
        "team_type": "club",
    }
    first = adapt_legacy_match(first_payload, source="openligadb")

    inserted = import_records(repository, [first])
    repeated = import_records(repository, [first])
    corrected = import_records(
        repository,
        [adapt_legacy_match({**first_payload, "home_goals": 2}, source="openligadb")],
    )

    assert inserted["inserted"] == 1
    assert repeated["skipped"] == 1
    assert corrected["updated"] == 1
    matches = repository.list_matches()
    assert len(matches) == 1
    assert matches[0]["home_goals"] == 2
    with sqlite3.connect(repository.database_path) as connection:
        revisions = connection.execute("SELECT count(*) FROM source_records").fetchone()[0]
    assert revisions == 2


def test_unmatched_alias_is_audited_without_creating_match(tmp_path):
    repository = initialized_repository(tmp_path)
    record = adapt_legacy_match(
        {
            "home_team": "不存在甲",
            "away_team": "不存在乙",
            "home_goals": 1,
            "away_goals": 1,
            "league": "德甲",
            "date": "2025-01-01",
            "team_type": "club",
        }
    )

    result = import_records(repository, [record])

    assert result["unmatched"] == 1
    assert repository.list_matches() == []
    assert {item["raw_alias"] for item in repository.list_unmatched_aliases()} == {
        "不存在甲", "不存在乙"
    }


def test_rescheduled_record_without_source_id_becomes_duplicate_candidate(tmp_path):
    repository = initialized_repository(tmp_path)
    base = {
        "home_team": "拜仁",
        "away_team": "多特蒙德",
        "home_goals": 1,
        "away_goals": 0,
        "league": "德甲",
        "team_type": "club",
    }
    import_records(repository, [adapt_legacy_match({**base, "date": "2025-01-01"})])
    import_records(repository, [adapt_legacy_match({**base, "date": "2025-01-08"})])

    assert len(repository.list_matches()) == 2
    with sqlite3.connect(repository.database_path) as connection:
        candidates = connection.execute("SELECT count(*) FROM duplicate_candidates").fetchone()[0]
    assert candidates == 1


def test_batch_failure_rolls_back_imported_matches(tmp_path):
    repository = initialized_repository(tmp_path)
    valid = adapt_legacy_match(
        {
            "match_id": "rollback-1",
            "home_team": "拜仁",
            "away_team": "多特蒙德",
            "home_goals": 1,
            "away_goals": 0,
            "league": "德甲",
            "date": "2025-01-01",
            "team_type": "club",
        }
    )

    def broken_records():
        yield valid
        raise RuntimeError("forced rollback")

    run_id = repository.create_sync_run("rollback")
    with pytest.raises(RuntimeError, match="forced rollback"):
        repository.import_source_records(broken_records(), run_id)

    assert repository.list_matches() == []
    with sqlite3.connect(repository.database_path) as connection:
        status = connection.execute(
            "SELECT status FROM sync_runs WHERE sync_run_id=?", (run_id,)
        ).fetchone()[0]
    assert status == "failed"


def test_training_query_excludes_same_day_date_precision_matches(tmp_path):
    repository = initialized_repository(tmp_path)
    records = [
        adapt_legacy_match({
            "match_id": f"date-{day}",
            "home_team": "拜仁",
            "away_team": "多特蒙德",
            "home_goals": 1,
            "away_goals": 0,
            "league": "德甲",
            "date": f"2025-01-{day:02d}",
            "team_type": "club",
        })
        for day in (1, 2)
    ]
    import_records(repository, records)

    training = repository.get_training_matches("2025-01-02T12:00:00+00:00")

    assert [match["date"] for match in training] == ["2025-01-01"]


def test_training_query_normalizes_timezone_and_excludes_exact_kickoff(tmp_path):
    repository = initialized_repository(tmp_path)
    import_records(repository, [adapt_legacy_match({
        "match_id": "minute-match",
        "home_team": "拜仁",
        "away_team": "多特蒙德",
        "home_goals": 1,
        "away_goals": 0,
        "league": "德甲",
        "date_time": "2025-01-01T12:00:00Z",
        "team_type": "club",
    })])

    assert repository.get_training_matches("2025-01-01T13:00:00+01:00") == []
    assert len(repository.get_training_matches("2025-01-01T12:01:00Z")) == 1


def test_alias_type_isolation_uses_requested_namespace(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({
        "schema_version": 1,
        "canonical_teams": {"national": ["同名国家"], "club": ["同名俱乐部"]},
        "aliases": [
            {"canonical_name": "同名国家", "team_type": "national", "values": ["Same"]},
            {"canonical_name": "同名俱乐部", "team_type": "club", "values": ["Same"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    repository = MatchRepository(tmp_path / "football.db", aliases_path=aliases)
    repository.initialize()

    assert repository.resolve_team("Same", "fifa", "national")["canonical_name"] == "同名国家"
    assert repository.resolve_team("Same", "openligadb", "club")["canonical_name"] == "同名俱乐部"
    assert repository.resolve_team("Same", "legacy_json", "unknown") is None


def test_source_specific_alias_takes_precedence_over_wildcard(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({
        "schema_version": 1,
        "canonical_teams": {"national": [], "club": ["通用队", "来源队"]},
        "aliases": [
            {"canonical_name": "通用队", "team_type": "club", "sources": ["*"], "values": ["Shared"]},
            {"canonical_name": "来源队", "team_type": "club", "sources": ["openligadb"], "values": ["Shared"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    repository = MatchRepository(tmp_path / "football.db", aliases_path=aliases)
    repository.initialize()

    assert repository.resolve_team("Shared", "openligadb", "club")["canonical_name"] == "来源队"
    assert repository.resolve_team("Shared", "legacy_json", "club")["canonical_name"] == "通用队"


def test_reprocess_unmatched_after_alias_seed_update(tmp_path):
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({
        "schema_version": 1,
        "canonical_teams": {"national": [], "club": []},
        "aliases": [],
    }), encoding="utf-8")
    repository = MatchRepository(tmp_path / "football.db", aliases_path=aliases)
    repository.initialize()
    record = adapt_legacy_match({
        "match_id": "new-alias-match",
        "home_team": "New Home",
        "away_team": "New Away",
        "home_goals": 1,
        "away_goals": 0,
        "league": "德甲",
        "date": "2025-01-01",
        "team_type": "club",
    })
    assert import_records(repository, [record])["unmatched"] == 1
    aliases.write_text(json.dumps({
        "schema_version": 1,
        "canonical_teams": {"national": [], "club": ["新主队", "新客队"]},
        "aliases": [
            {"canonical_name": "新主队", "team_type": "club", "values": ["New Home"]},
            {"canonical_name": "新客队", "team_type": "club", "values": ["New Away"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    repository.initialize()

    result = repository.reprocess_unmatched()

    assert result["inserted"] == 1
    assert repository.list_matches()[0]["home_team"] == "新主队"
    assert all(item["status"] == "resolved" for item in repository.list_unmatched_aliases())


def test_odds_snapshot_returns_latest_value_before_prediction_time(tmp_path):
    repository = initialized_repository(tmp_path)
    record = adapt_legacy_match({
        "match_id": "odds-match",
        "home_team": "拜仁",
        "away_team": "多特蒙德",
        "home_goals": 1,
        "away_goals": 0,
        "league": "德甲",
        "date": "2025-01-03",
        "team_type": "club",
    })
    import_records(repository, [record])
    match_id = repository.list_matches()[0]["match_id"]
    for captured_at, home_odds in (
        ("2025-01-01T10:00:00+00:00", 2.1),
        ("2025-01-02T10:00:00+00:00", 1.9),
    ):
        repository.save_odds_snapshot({
            "match_id": match_id,
            "company": "test",
            "captured_at": captured_at,
            "home_odds": home_odds,
            "draw_odds": 3.2,
            "away_odds": 3.8,
            "source": "fixture",
        })

    snapshot = repository.get_pre_match_odds(match_id, "2025-01-01T12:00:00+00:00")

    assert snapshot["home_odds"] == 2.1
    assert repository.get_data_quality_report()["match_count"] == 1
