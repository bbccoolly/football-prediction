"""生成不触发网络或模型初始化的本地诊断报告。"""

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HISTORY_FILE = ROOT / "data" / "processed" / "match_history.json"
ELO_FILE = ROOT / "data" / "processed" / "elo_ratings.json"
WEIGHTS_FILE = ROOT / "ensemble" / "weights.json"
MODEL_DIR = ROOT / "ensemble" / "saved_models"


def _read_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"read_error": str(exc)}


def _fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git_commit():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_report():
    history_data = _read_json(HISTORY_FILE) or {}
    matches = history_data.get("matches", []) if isinstance(history_data, dict) else []
    elo_data = _read_json(ELO_FILE) or {}
    weights_data = _read_json(WEIGHTS_FILE) or {}
    weights = weights_data.get("weights", {}) if isinstance(weights_data, dict) else {}

    model_artifacts = {
        "xgboost": all((MODEL_DIR / name).exists() for name in ("xgboost_clf.pkl", "xgboost_reg.pkl")),
        "neural_net": (MODEL_DIR / "neural_net.npz").exists(),
        "stacker": (MODEL_DIR / "stacker.pkl").exists(),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "history": {
            "count": len(matches),
            "leagues": dict(Counter(m.get("league") or "未标注" for m in matches)),
            "fingerprint": _fingerprint(matches),
        },
        "elo": {
            "schema_version": elo_data.get("schema_version", 1) if isinstance(elo_data, dict) else None,
            "teams": len(elo_data.get("ratings", {})) if isinstance(elo_data, dict) else 0,
            "history_entries": len(elo_data.get("history", [])) if isinstance(elo_data, dict) else 0,
            "data_fingerprint": elo_data.get("data_fingerprint") if isinstance(elo_data, dict) else None,
            "read_error": elo_data.get("read_error") if isinstance(elo_data, dict) else None,
        },
        "weights": {
            "schema_version": weights_data.get("schema_version", 1) if isinstance(weights_data, dict) else None,
            "keys": sorted(weights),
            "values": weights,
            "has_legacy_knn_key": "knn" in weights,
            "read_error": weights_data.get("read_error") if isinstance(weights_data, dict) else None,
        },
        "trained_artifacts": model_artifacts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "processed" / "baseline-report.json"),
        help="报告输出路径",
    )
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(build_report(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(output)


if __name__ == "__main__":
    main()
