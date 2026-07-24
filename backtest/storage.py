"""Atomic local persistence for ignored backtest artifacts."""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from data.match_repository import canonical_json, fingerprint


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
    os.replace(temporary, path)


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
