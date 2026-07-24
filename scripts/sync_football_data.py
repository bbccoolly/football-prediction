"""Explicit acquisition, audit and import workflow for football-data.co.uk batches."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.football_data import (  # noqa: E402
    SOURCE, FootballDataError, fetch_batch, load_batch,
)
from data.match_repository import (  # noqa: E402
    DEFAULT_DATABASE_PATH, MatchRepository, canonical_json, fingerprint,
)


def _database(value):
    return Path(value or DEFAULT_DATABASE_PATH).resolve()


def _batch(value):
    path = Path(value).resolve()
    if not (path / "manifest.json").is_file():
        raise FootballDataError("batch 目录缺少 manifest.json")
    return path


def _copy_database(source: Path, destination: Path):
    if not source.exists():
        return
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _prepare_staging(database: Path, temporary: Path):
    staged = temporary / "staged.db"
    _copy_database(database, staged)
    repository = MatchRepository(staged)
    repository.initialize()
    return repository


def _audit(repository: MatchRepository, records):
    unknown = set()
    for item in records:
        for team in (item.record.home_team_raw, item.record.away_team_raw):
            if repository.resolve_team(team, SOURCE, "club") is None:
                unknown.add(team)
    return {"unknown_teams": sorted(unknown), "unknown_count": len(unknown)}


def _dry_run(batch_path: Path, database: Path):
    manifest, records = load_batch(batch_path)
    with tempfile.TemporaryDirectory(prefix="football-data-dry-run-") as directory:
        repository = _prepare_staging(database, Path(directory))
        try:
            before = repository.build_data_fingerprint({})
            audit = _audit(repository, records)
            if audit["unknown_teams"]:
                plan = {"manifest": manifest["manifest_fingerprint"], "before": before, "audit": audit}
                return {
                    "status": "not_ready", "mode": "dry-run", "batch_id": manifest["batch_id"],
                    "audit": audit, "report_fingerprint": fingerprint(plan),
                }
            run_id = repository.create_sync_run("football_data_dry_run", {"batch_id": manifest["batch_id"]})
            counts = repository.import_dataset_batch(manifest, records, run_id)
            after = repository.build_data_fingerprint({})
            report = {
                "schema_version": 1, "mode": "dry-run", "batch_id": manifest["batch_id"],
                "manifest_fingerprint": manifest["manifest_fingerprint"],
                "before_database_fingerprint": before, "after_database_fingerprint": after,
                "counts": counts, "audit": audit,
            }
            report["report_fingerprint"] = fingerprint(report)
            report["status"] = "ready" if not counts["rejected"] and not counts["unmatched"] else "not_ready"
            return report
        finally:
            repository.close()


def fetch_command(args):
    root = Path(args.output_root).resolve()
    path, manifest = fetch_batch(root)
    print(json.dumps({"status": "ok", "batch": str(path), "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


def audit_command(args):
    manifest, records = load_batch(_batch(args.batch))
    with tempfile.TemporaryDirectory(prefix="football-data-audit-") as directory:
        repository = _prepare_staging(_database(args.database), Path(directory))
        try:
            result = _audit(repository, records)
        finally:
            repository.close()
    result.update({"status": "ok" if not result["unknown_count"] else "not_ready", "batch_id": manifest["batch_id"]})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result["unknown_count"] else 2


def import_command(args):
    batch_path = _batch(args.batch)
    database = _database(args.database)
    report = _dry_run(batch_path, database)
    if not args.apply:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "ready" else 2
    if args.expect_report_fingerprint != report["report_fingerprint"]:
        raise FootballDataError("dry-run 报告指纹不匹配；请重新确认导入计划")
    if report["status"] != "ready":
        raise FootballDataError("数据审计未通过，拒绝写入数据库")
    manifest, records = load_batch(batch_path)
    repository = MatchRepository(database)
    repository.initialize()
    try:
        before = repository.build_data_fingerprint({})
        if before != report["before_database_fingerprint"]:
            raise FootballDataError("目标数据库在 dry-run 后发生变化")
        run_id = repository.create_sync_run("football_data_apply", {"batch_id": manifest["batch_id"]})
        counts = repository.import_dataset_batch(manifest, records, run_id)
        after = repository.build_data_fingerprint({})
    finally:
        repository.close()
    if after != report["after_database_fingerprint"]:
        raise FootballDataError("apply 结果与 dry-run 不一致")
    result = {**report, "mode": "apply", "counts": counts, "after_database_fingerprint": after}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def readiness_command(args):
    repository = MatchRepository(_database(args.database))
    try:
        report = repository.build_data_readiness_report(
            batch_id=args.batch_id, evaluation_as_of=args.evaluation_as_of,
        )
    finally:
        repository.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 2


def build_parser():
    parser = argparse.ArgumentParser(description="欧洲五大联赛历史数据同步")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="显式下载固定历史 CSV 范围")
    fetch.add_argument("--output-root", default=str(PROJECT_ROOT / "data/raw/football-data"))
    fetch.set_defaults(handler=fetch_command)
    for name, handler in (("audit", audit_command), ("import", import_command)):
        child = commands.add_parser(name)
        child.add_argument("--batch", required=True)
        child.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
        child.set_defaults(handler=handler)
    import_parser = commands.choices["import"]
    import_parser.add_argument("--apply", action="store_true")
    import_parser.add_argument("--expect-report-fingerprint")
    readiness = commands.add_parser("readiness", help="检查正式数据门禁")
    readiness.add_argument("--batch-id", required=True)
    readiness.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
    readiness.add_argument("--evaluation-as-of", required=True)
    readiness.set_defaults(handler=readiness_command)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (FootballDataError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
