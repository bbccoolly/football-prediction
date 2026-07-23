import json

import pytest

from data.history_db import load_history
from scripts.migrate_history import migrate_history


def test_migration_dry_run_does_not_create_database(tmp_path):
    source = tmp_path / "legacy.json"
    database = tmp_path / "nested" / "football.db"
    source.write_text(json.dumps({"matches": [{
        "match_id": "one",
        "home_team": "拜仁",
        "away_team": "多特蒙德",
        "home_goals": 2,
        "away_goals": 1,
        "league": "德甲",
        "date": "2025-01-01",
        "team_type": "club",
    }]}, ensure_ascii=False), encoding="utf-8")

    report = migrate_history(source, database, apply=False)

    assert report["inserted"] == 1
    assert not database.exists()


def test_apply_migration_is_idempotent_and_load_history_prefers_sqlite(tmp_path):
    source = tmp_path / "legacy.json"
    database = tmp_path / "football.db"
    source.write_text(json.dumps({"matches": [{
        "match_id": "one",
        "home_team": "FC Bayern München",
        "away_team": "Borussia Dortmund",
        "home_goals": 2,
        "away_goals": 1,
        "league": "德甲",
        "date": "2025-01-01",
    }]}, ensure_ascii=False), encoding="utf-8")

    first = migrate_history(source, database, apply=True)
    second = migrate_history(source, database, apply=True)
    loaded = load_history(database_path=database, legacy_path=tmp_path / "missing.json")

    assert first["inserted"] == 1
    assert second["skipped"] == 1
    assert len(loaded) == 1
    assert loaded[0]["home_team"] == "拜仁"
    assert loaded[0]["home_team_id"]


def test_load_history_without_database_is_read_only(tmp_path, monkeypatch):
    database = tmp_path / "not-created" / "football.db"
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"matches": [{"home_team": "甲"}]}), encoding="utf-8")

    monkeypatch.setattr("data.history_db._legacy_warning_emitted", False)
    with pytest.warns(DeprecationWarning, match="旧版比赛 JSON"):
        loaded = load_history(database_path=database, legacy_path=legacy)

    assert loaded == [{"home_team": "甲"}]
    assert not database.exists()


def test_migration_counts_invalid_score_as_rejected(tmp_path):
    source = tmp_path / "invalid.json"
    source.write_text(json.dumps({"matches": [{
        "home_team": "拜仁",
        "away_team": "多特蒙德",
        "home_goals": "invalid",
        "away_goals": 1,
        "league": "德甲",
        "date": "2025-01-01",
        "team_type": "club",
    }]}, ensure_ascii=False), encoding="utf-8")

    report = migrate_history(source, tmp_path / "unused.db", apply=False)

    assert report["rejected"] == 1
    assert report["match_count"] == 0
