"""Atomic local persistence for ignored backtest artifacts."""

from __future__ import annotations

import json
import hashlib
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from data.match_repository import canonical_json, fingerprint
from backtest.contracts import (
    BACKTEST_PROTOCOL_VERSION,
    BACKTEST_SCHEMA_VERSION,
    BacktestCheckpointError,
    BacktestSpecMismatchError,
)


def create_run_id(now=None):
    now = now or datetime.now(timezone.utc)
    timestamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"bt-{timestamp}-{secrets.token_hex(4)}"


def json_value(value):
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return value


def atomic_write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 7:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path, value):
    content = json.dumps(
        json_value(value), ensure_ascii=False, sort_keys=True, indent=2,
        allow_nan=False,
    ) + "\n"
    atomic_write_text(path, content)


def atomic_write_jsonl(path, values):
    lines = [
        json.dumps(json_value(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
        for value in values
    ]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))


def exclusive_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        json.dumps(
            json_value(value), ensure_ascii=False, sort_keys=True,
            indent=2, allow_nan=False,
        ) + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BacktestSpecMismatchError("运行规格已经存在") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise BacktestCheckpointError(f"文件损坏: {Path(path).name}") from exc


def read_jsonl(path):
    try:
        return [
            json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise BacktestCheckpointError(f"分段文件损坏: {Path(path).name}") from exc


class BacktestCheckpointStore:
    def __init__(self, output_dir, run_id):
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.segment_dir = self.output_dir / "segments"
        self.checkpoint_dir = self.output_dir / "checkpoints"

    @property
    def run_spec_path(self):
        return self.output_dir / "run_spec.json"

    def create_run_spec(self, value):
        exclusive_write_json(self.run_spec_path, value)

    def load_run_spec(self):
        return read_json(self.run_spec_path)

    def write_batch(
        self, sequence, partition, batch, records, *, input_fingerprint,
        spec_fingerprint, processed_matches,
    ):
        segment_rel = f"segments/{batch['batch_id']}.jsonl"
        segment_path = self.output_dir / segment_rel
        atomic_write_jsonl(segment_path, records)
        segment_hash = file_sha256(segment_path)
        previous_hash = None
        if sequence > 1:
            previous = self.checkpoint_dir / f"checkpoint-{sequence - 1:05d}.json"
            if not previous.is_file():
                raise BacktestCheckpointError("前序检查点不存在")
            previous_hash = file_sha256(previous)
        checkpoint = {
            "schema_version": BACKTEST_SCHEMA_VERSION,
            "protocol_version": BACKTEST_PROTOCOL_VERSION,
            "run_id": self.run_id,
            "sequence": sequence,
            "partition": partition,
            "batch_id": batch["batch_id"],
            "cutoff": batch["cutoff"],
            "segment": segment_rel,
            "segment_records": len(records),
            "segment_sha256": segment_hash,
            "previous_checkpoint_sha256": previous_hash,
            "run_input_fingerprint": input_fingerprint,
            "run_spec_fingerprint": spec_fingerprint,
            "processed_matches": processed_matches,
            "next_batch_index": sequence,
        }
        checkpoint_path = self.checkpoint_dir / f"checkpoint-{sequence:05d}.json"
        atomic_write_json(checkpoint_path, checkpoint)
        return checkpoint

    def validate(self, batch_specs, *, input_fingerprint, spec_fingerprint):
        if not self.checkpoint_dir.exists():
            return [], 0
        paths = sorted(self.checkpoint_dir.glob("checkpoint-*.json"))
        records = []
        previous_hash = None
        for sequence, path in enumerate(paths, start=1):
            if path.name != f"checkpoint-{sequence:05d}.json":
                raise BacktestCheckpointError("检查点序号不连续")
            if sequence > len(batch_specs):
                raise BacktestCheckpointError("检查点超过运行批次数")
            partition, batch = batch_specs[sequence - 1]
            checkpoint = read_json(path)
            expected = {
                "schema_version": BACKTEST_SCHEMA_VERSION,
                "protocol_version": BACKTEST_PROTOCOL_VERSION,
                "run_id": self.run_id,
                "sequence": sequence,
                "partition": partition,
                "batch_id": batch["batch_id"],
                "cutoff": batch["cutoff"],
                "previous_checkpoint_sha256": previous_hash,
                "run_input_fingerprint": input_fingerprint,
                "run_spec_fingerprint": spec_fingerprint,
            }
            if any(checkpoint.get(key) != value for key, value in expected.items()):
                raise BacktestCheckpointError("检查点契约不匹配")
            segment_rel = checkpoint.get("segment")
            if not isinstance(segment_rel, str) or Path(segment_rel).is_absolute() or ".." in Path(segment_rel).parts:
                raise BacktestCheckpointError("检查点分段路径无效")
            segment_path = self.output_dir / segment_rel
            try:
                segment_hash = file_sha256(segment_path)
            except OSError as exc:
                raise BacktestCheckpointError("检查点分段文件缺失") from exc
            if segment_hash != checkpoint.get("segment_sha256"):
                raise BacktestCheckpointError("检查点分段校验和不匹配")
            segment_records = read_jsonl(segment_path)
            if len(segment_records) != checkpoint.get("segment_records"):
                raise BacktestCheckpointError("检查点分段记录数不匹配")
            records.extend(segment_records)
            previous_hash = file_sha256(path)
        return records, len(paths)


def scientific_fingerprint(predictions, metrics, admission, protocol):
    normalized_predictions = []
    for record in predictions:
        normalized_predictions.append({
            key: record[key]
            for key in (
                "prediction_record_id", "partition", "batch_id", "batch_cutoff",
                "match", "actual", "predictions", "market", "derived", "runtime",
            )
        })
    return fingerprint({
        "protocol": protocol,
        "predictions": normalized_predictions,
        "metrics": metrics,
        "admission": admission,
    })


def scoring_fingerprint(predictions):
    return fingerprint([{
        "match_id": record["match"]["match_id"],
        "actual": record["actual"],
        "predictions": {
            key: value for key, value in record["predictions"].items()
            if key != "monte_carlo"
        },
        "ensemble": record["derived"].get("ensemble"),
    } for record in predictions])
