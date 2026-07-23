import json
from pathlib import Path

import pytest

from models.elo import EloRating


@pytest.fixture
def matches():
    path = Path(__file__).parents[1] / "fixtures" / "matches.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_rebuild_is_independent_of_input_order(tmp_path, matches):
    first = EloRating(storage_path=tmp_path / "first.json")
    second = EloRating(storage_path=tmp_path / "second.json")

    first.rebuild(matches)
    second.rebuild(list(reversed(matches)))

    assert first.ratings == second.ratings
    assert first.data_fingerprint == second.data_fingerprint


def test_duplicate_record_changes_fingerprint(tmp_path, matches):
    base = EloRating(storage_path=tmp_path / "base.json")
    duplicate = EloRating(storage_path=tmp_path / "duplicate.json")

    base.rebuild(matches)
    duplicate.rebuild(matches + [matches[0]])

    assert base.data_fingerprint != duplicate.data_fingerprint


def test_save_and_load_requires_matching_fingerprint(tmp_path, matches):
    path = tmp_path / "elo.json"
    source = EloRating(storage_path=path)
    source.rebuild(matches)
    source.save()

    loaded = EloRating(storage_path=path)
    assert loaded.load(source.data_fingerprint) is True
    assert loaded.ratings == source.ratings
    assert loaded.load("wrong-fingerprint") is False


def test_legacy_file_requires_rebuild(tmp_path):
    path = tmp_path / "elo.json"
    path.write_text(json.dumps({"ratings": {"红队": 1700}, "history": []}), encoding="utf-8")

    model = EloRating(storage_path=path)

    assert model.load("expected") is False
    assert model.ratings == {}


def test_corrupt_file_is_not_overwritten_by_load(tmp_path):
    path = tmp_path / "elo.json"
    path.write_text("{broken", encoding="utf-8")
    model = EloRating(storage_path=path)

    assert model.load("expected") is False
    assert path.read_text(encoding="utf-8") == "{broken"


def test_parameter_change_invalidates_file(tmp_path, matches, monkeypatch):
    path = tmp_path / "elo.json"
    source = EloRating(storage_path=path)
    source.rebuild(matches)
    source.save()

    monkeypatch.setattr("models.elo.ELO_K", 99)
    loaded = EloRating(storage_path=path)

    assert loaded.load(source.data_fingerprint) is False
