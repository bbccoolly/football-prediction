"""Read-only model artifact manifest validation."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from features.builder import FeatureBuilder
from prediction.contracts import ModelArtifactMetadata


REQUIRED_FIELDS = {
    "artifact_schema_version", "model_id", "model_version", "feature_version",
    "feature_names", "trained_from", "trained_until", "training_sample_count",
    "training_data_fingerprint", "parameter_fingerprint", "code_commit",
    "artifact_format", "files",
}
SUPPORTED_MODEL_VERSIONS = {
    "xgboost": "1",
    "neural_net": "1",
    "stacking": "1",
}
LEGACY_NAMES = {
    "xgboost": ("xgboost*.pkl",),
    "neural_net": ("neural_net*.npz",),
    "stacking": ("stacker*.pkl", "stacking*.pkl"),
}
SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True)
class ArtifactInspection:
    model_id: str
    status: str
    reason: str | None = None
    metadata: ModelArtifactMetadata | None = None


class ModelArtifactInspector:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def inspect(self, model_id: str, as_of: str) -> ArtifactInspection:
        model_dir = self.root / model_id
        metadata_path = model_dir / "metadata.json"
        if not metadata_path.exists():
            legacy = []
            for pattern in LEGACY_NAMES.get(
                model_id, (f"{model_id}*.pkl", f"{model_id}*.npz")
            ):
                legacy.extend(self.root.glob(pattern))
                legacy.extend(model_dir.glob(pattern))
            return ArtifactInspection(
                model_id,
                "legacy_artifact_unsupported" if legacy else "artifact_missing",
                "metadata_missing",
            )
        try:
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            missing = REQUIRED_FIELDS - set(raw)
            if missing:
                raise ValueError(f"missing fields: {', '.join(sorted(missing))}")
            metadata = ModelArtifactMetadata(
                artifact_schema_version=int(raw["artifact_schema_version"]),
                model_id=str(raw["model_id"]),
                model_version=str(raw["model_version"]),
                feature_version=str(raw["feature_version"]),
                feature_names=tuple(raw["feature_names"]),
                trained_from=str(raw["trained_from"]),
                trained_until=str(raw["trained_until"]),
                training_sample_count=int(raw["training_sample_count"]),
                training_data_fingerprint=str(raw["training_data_fingerprint"]),
                parameter_fingerprint=str(raw["parameter_fingerprint"]),
                code_commit=str(raw["code_commit"]),
                artifact_format=str(raw["artifact_format"]),
                files=tuple(raw["files"]),
            )
            self._validate(model_dir, model_id, metadata, as_of)
            return ArtifactInspection(
                model_id, "disabled_pending_admission", None, metadata
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return ArtifactInspection(model_id, "invalid_artifact", str(exc))

    @staticmethod
    def _validate(model_dir, model_id, metadata, as_of):
        if metadata.artifact_schema_version != 1:
            raise ValueError("unsupported artifact_schema_version")
        if metadata.model_id != model_id:
            raise ValueError("model_id mismatch")
        if metadata.model_version != SUPPORTED_MODEL_VERSIONS.get(model_id, "1"):
            raise ValueError("model_version mismatch")
        if metadata.feature_version != FeatureBuilder.FEATURE_VERSION:
            raise ValueError("feature_version mismatch")
        if metadata.feature_names != FeatureBuilder.FEATURE_NAMES:
            raise ValueError("feature_names mismatch")
        if metadata.training_sample_count < 1:
            raise ValueError("training_sample_count must be positive")
        for field_name, value in (
            ("training_data_fingerprint", metadata.training_data_fingerprint),
            ("parameter_fingerprint", metadata.parameter_fingerprint),
        ):
            if not SHA256_PATTERN.fullmatch(value):
                raise ValueError(f"{field_name} must be a SHA-256 digest")
        if not metadata.artifact_format.strip():
            raise ValueError("artifact_format is required")
        trained_from = datetime.fromisoformat(metadata.trained_from.replace("Z", "+00:00"))
        trained_until = datetime.fromisoformat(metadata.trained_until.replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if trained_from > trained_until:
            raise ValueError("trained_from must not be after trained_until")
        if trained_until >= cutoff:
            raise ValueError("artifact trained_until must be before prediction cutoff")
        root = model_dir.resolve()
        if not metadata.files:
            raise ValueError("artifact files are required")
        for item in metadata.files:
            if not isinstance(item, Mapping):
                raise ValueError("artifact file entry must be an object")
            relative = Path(str(item.get("path", "")))
            expected_hash = str(item.get("sha256", ""))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                raise ValueError("artifact path must stay within model directory")
            if not SHA256_PATTERN.fullmatch(expected_hash):
                raise ValueError("artifact sha256 must be a SHA-256 digest")
            path = (model_dir / relative).resolve()
            if root not in path.parents or not path.is_file():
                raise ValueError("artifact file missing or outside model directory")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != expected_hash.lower():
                raise ValueError("artifact checksum mismatch")
