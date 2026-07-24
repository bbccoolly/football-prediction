"""Dry-run and apply migration from legacy match JSON to SQLite."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.match_repository import DEFAULT_DATABASE_PATH, MatchRepository
from data.source_adapters import adapt_legacy_match


def load_legacy_matches(source_path: str | os.PathLike[str]):
    path = Path(source_path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    matches = payload.get("matches") if isinstance(payload, dict) else payload
    if not isinstance(matches, list):
        raise ValueError("迁移源必须是比赛数组或包含 matches 数组的对象")
    fetched_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    return matches, fetched_at


def migrate_history(source_path, database_path=None, *, apply=False):
    matches, fetched_at = load_legacy_matches(source_path)
    target = str(database_path or os.environ.get("FOOTBALL_DB_PATH", DEFAULT_DATABASE_PATH))
    repository = MatchRepository(target if apply else ":memory:")
    try:
        repository.initialize()
        run_id = repository.create_sync_run(
            "legacy_json_migration",
            {"source": str(Path(source_path).resolve()), "apply": apply},
        )
        records = [
            adapt_legacy_match(match, fetched_at=fetched_at)
            for match in matches
        ]
        counts = repository.import_source_records(
            records,
            run_id,
            sync_type="legacy_json_migration",
        )
        imported = repository.list_matches()
        competitions = Counter(match["league"] for match in imported)
        report = {
            "mode": "apply" if apply else "dry-run",
            "source": str(Path(source_path).resolve()),
            "database": str(Path(target).resolve()),
            **counts,
            "match_count": len(imported),
            "unmatched_alias_count": len(repository.list_unmatched_aliases()),
            "competitions": dict(sorted(competitions.items())),
        }
        return report
    finally:
        repository.close()


def build_parser():
    parser = argparse.ArgumentParser(description="迁移旧版比赛 JSON 到标准 SQLite 仓库")
    parser.add_argument("--source", required=True, help="旧版 JSON 文件路径")
    parser.add_argument("--database", help="目标数据库路径，默认读取 FOOTBALL_DB_PATH")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库；默认仅 dry-run")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        report = migrate_history(args.source, args.database, apply=args.apply)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "ok", **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
