import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from features.builder import FeatureBuilder
from prediction.artifacts import ModelArtifactInspector


def _metadata(cutoff, path, digest):
    return {
        "artifact_schema_version": 1,
        "model_id": "xgboost",
        "model_version": "1",
        "feature_version": FeatureBuilder.FEATURE_VERSION,
        "feature_names": list(FeatureBuilder.FEATURE_NAMES),
        "trained_from": (cutoff - timedelta(days=20)).isoformat(),
        "trained_until": (cutoff - timedelta(days=1)).isoformat(),
        "training_sample_count": 100,
        "training_data_fingerprint": hashlib.sha256(b"training-data").hexdigest(),
        "parameter_fingerprint": hashlib.sha256(b"parameters").hexdigest(),
        "code_commit": "abc1234",
        "artifact_format": "xgboost-json",
        "files": [{"role": "classifier", "path": path, "sha256": digest}],
    }


def test_legacy_pickle_is_rejected_without_deserialization(tmp_path):
    (tmp_path / "xgboost_clf.pkl").write_bytes(b"legacy")

    result = ModelArtifactInspector(tmp_path).inspect(
        "xgboost", datetime.now(timezone.utc).isoformat()
    )

    assert result.status == "legacy_artifact_unsupported"
    assert result.metadata is None


def test_valid_manifest_is_checked_but_not_loaded(tmp_path):
    model_dir = tmp_path / "xgboost"
    model_dir.mkdir()
    payload = model_dir / "classifier.json"
    payload.write_text("{}", encoding="utf-8")
    cutoff = datetime.now(timezone.utc)
    metadata = _metadata(
        cutoff, payload.name, hashlib.sha256(payload.read_bytes()).hexdigest()
    )
    (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    result = ModelArtifactInspector(tmp_path).inspect("xgboost", cutoff.isoformat())

    assert result.status == "disabled_pending_admission"
    assert result.metadata.model_id == "xgboost"


def test_manifest_path_cannot_escape_model_directory(tmp_path):
    model_dir = tmp_path / "xgboost"
    model_dir.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"payload")
    cutoff = datetime.now(timezone.utc)
    metadata = _metadata(
        cutoff, "../outside.bin", hashlib.sha256(outside.read_bytes()).hexdigest()
    )
    (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    result = ModelArtifactInspector(tmp_path).inspect("xgboost", cutoff.isoformat())

    assert result.status == "invalid_artifact"
    assert "within model directory" in result.reason


def test_manifest_rejects_unsupported_model_version(tmp_path):
    model_dir = tmp_path / "xgboost"
    model_dir.mkdir()
    payload = model_dir / "classifier.json"
    payload.write_text("{}", encoding="utf-8")
    cutoff = datetime.now(timezone.utc)
    metadata = _metadata(
        cutoff, payload.name, hashlib.sha256(payload.read_bytes()).hexdigest()
    )
    metadata["model_version"] = "2"
    (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    result = ModelArtifactInspector(tmp_path).inspect("xgboost", cutoff.isoformat())

    assert result.status == "invalid_artifact"
    assert result.reason == "model_version mismatch"


@pytest.mark.parametrize(
    "field_name",
    ["training_data_fingerprint", "parameter_fingerprint"],
)
def test_manifest_rejects_invalid_fingerprint(tmp_path, field_name):
    model_dir = tmp_path / "xgboost"
    model_dir.mkdir()
    payload = model_dir / "classifier.json"
    payload.write_text("{}", encoding="utf-8")
    cutoff = datetime.now(timezone.utc)
    metadata = _metadata(
        cutoff, payload.name, hashlib.sha256(payload.read_bytes()).hexdigest()
    )
    metadata[field_name] = "not-a-sha256"
    (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    result = ModelArtifactInspector(tmp_path).inspect("xgboost", cutoff.isoformat())

    assert result.status == "invalid_artifact"
    assert field_name in result.reason


def test_manifest_rejects_payload_checksum_mismatch(tmp_path):
    model_dir = tmp_path / "xgboost"
    model_dir.mkdir()
    payload = model_dir / "classifier.json"
    payload.write_text("{}", encoding="utf-8")
    cutoff = datetime.now(timezone.utc)
    metadata = _metadata(cutoff, payload.name, "0" * 64)
    (model_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    result = ModelArtifactInspector(tmp_path).inspect("xgboost", cutoff.isoformat())

    assert result.status == "invalid_artifact"
    assert result.reason == "artifact checksum mismatch"
