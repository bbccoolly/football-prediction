"""Offline Walk-forward backtest and admission command line interface."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from backtest import BacktestConfig, BacktestError, BacktestRunner
from backtest.contracts import (
    DEFAULT_OUTPUT_ROOT,
    BacktestConfigurationError,
    BacktestDataError,
)
from backtest.storage import create_run_id
from backtest.tasks import BacktestAlreadyRunningError, BacktestTaskStore
from data.match_repository import MatchRepository
from data.source_adapters import adapt_legacy_match


PROJECT_ROOT = Path(__file__).resolve().parent
RUN_ID_PATTERN = re.compile(r"bt-[A-Za-z0-9._-]+$")


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _output_root(value):
    path = Path(value or DEFAULT_OUTPUT_ROOT)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _validate_run_id(value):
    if not RUN_ID_PATTERN.fullmatch(value):
        raise BacktestConfigurationError("run_id 格式无效")
    return value


def _fixture_path(value):
    path = Path(value).resolve()
    fixture_root = (PROJECT_ROOT / "tests/fixtures").resolve()
    if not path.is_relative_to(fixture_root) or path.suffix.lower() != ".json":
        raise BacktestConfigurationError("fixture 必须是 tests/fixtures 下的 JSON")
    if not path.is_file():
        raise BacktestConfigurationError("fixture 文件不存在")
    return path


def _prepare_fixture(path, temporary_directory, as_of):
    raw = json.loads(path.read_text(encoding="utf-8"))
    matches = raw.get("matches", []) if isinstance(raw, dict) else raw
    if not isinstance(matches, list):
        raise BacktestDataError("fixture 必须是比赛数组或包含 matches 数组")
    database = Path(temporary_directory) / "fixture.db"
    repository = MatchRepository(database)
    repository.initialize()
    records = [
        adapt_legacy_match(
            match, source="backtest_fixture", fetched_at=as_of
        )
        for match in matches
    ]
    sync_run_id = repository.create_sync_run("backtest_fixture", {"fixture": path.name})
    counts = repository.import_source_records(
        records, sync_run_id, sync_type="backtest_fixture"
    )
    if counts["rejected"] or counts["unmatched"]:
        raise BacktestDataError(
            "fixture 包含无法导入的比赛："
            f"rejected={counts['rejected']}, unmatched={counts['unmatched']}"
        )
    return repository, {
        "kind": "fixture", "name": path.name, "import_counts": counts,
    }


def _prepare_database(path, temporary_directory):
    source_path = Path(path).resolve()
    if not source_path.is_file():
        raise BacktestConfigurationError("数据库文件不存在")
    source_repository = MatchRepository(source_path)
    snapshot_path = Path(temporary_directory) / "database-snapshot.db"
    source_repository.backup_to(snapshot_path)
    return MatchRepository(snapshot_path), {
        "kind": "database", "name": source_path.name,
    }


def run_backtest_command(args):
    if args.allow_insufficient_data and not args.fixture:
        raise BacktestConfigurationError(
            "--allow-insufficient-data 只能与 --fixture 一起使用"
        )
    run_id = _validate_run_id(args.run_id or create_run_id())
    output_root = _output_root(args.output_root)
    config = BacktestConfig(
        as_of=args.as_of or _utc_now(), output_root=output_root,
        dataset_batch_id=args.dataset_batch_id,
    )
    store = BacktestTaskStore(output_root)
    store.recover()
    store.reserve(run_id)
    store.claim(run_id, os.getpid())
    try:
        with tempfile.TemporaryDirectory(prefix="football-backtest-") as temporary:
            if args.fixture:
                repository, source = _prepare_fixture(
                    _fixture_path(args.fixture), temporary, config.as_of
                )
            else:
                repository, source = _prepare_database(args.database, temporary)
            try:
                runner = BacktestRunner(repository, config)
                result = runner.run(
                    run_id, source=source,
                    progress=lambda **values: store.update_progress(run_id, **values),
                )
            finally:
                repository.close()
        insufficient = result["insufficient_data"]
        exit_code = 0 if not insufficient or args.allow_insufficient_data else 2
        outcome = "insufficient_data" if insufficient else "ok"
        store.complete(run_id, exit_code, outcome)
        print(json.dumps({
            "status": "ok",
            "outcome": outcome,
            "run_id": run_id,
            "result_fingerprint": result["result_fingerprint"],
            "output": str(result["output_dir"]),
            "exit_code": exit_code,
        }, ensure_ascii=False, indent=2))
        return exit_code
    except Exception as exc:
        code = exc.code if isinstance(exc, BacktestError) else "BACKTEST_FAILED"
        store.fail(run_id, code, str(exc))
        print(json.dumps({
            "status": "error", "error_code": code,
            "message": str(exc), "run_id": run_id,
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        store.release(run_id)


def _resolve_existing_run(args, completed_only=False):
    store = BacktestTaskStore(_output_root(args.output_root))
    run_id = args.run_id or store.latest_run_id(completed_only=completed_only)
    if not run_id:
        raise BacktestConfigurationError("没有可用的回测运行")
    return store, _validate_run_id(run_id)


def report_command(args):
    store, run_id = _resolve_existing_run(args, completed_only=True)
    path = store.run_dir(run_id) / "report.md"
    if not path.is_file():
        raise BacktestConfigurationError("回测报告不存在")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def admission_command(args):
    store, run_id = _resolve_existing_run(args, completed_only=True)
    path = store.run_dir(run_id) / "admission.json"
    if not path.is_file():
        raise BacktestConfigurationError("准入清单不存在")
    print(json.dumps(
        json.loads(path.read_text(encoding="utf-8")),
        ensure_ascii=False, indent=2,
    ))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="足球预测可信 Walk-forward 回测")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backtest_parser = subparsers.add_parser("backtest", help="运行离线回测")
    source = backtest_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database")
    source.add_argument("--fixture")
    backtest_parser.add_argument("--as-of")
    backtest_parser.add_argument("--dataset-batch-id")
    backtest_parser.add_argument("--run-id")
    backtest_parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    backtest_parser.add_argument("--allow-insufficient-data", action="store_true")
    backtest_parser.set_defaults(handler=run_backtest_command)
    for name, handler in (("report", report_command), ("admission", admission_command)):
        child = subparsers.add_parser(name)
        child.add_argument("--run-id")
        child.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
        child.set_defaults(handler=handler)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except BacktestAlreadyRunningError as exc:
        print(json.dumps({
            "status": "error", "error_code": "BACKTEST_ALREADY_RUNNING",
            "message": str(exc), "run_id": exc.run_id,
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    except (BacktestError, OSError, ValueError, json.JSONDecodeError) as exc:
        code = exc.code if isinstance(exc, BacktestError) else "BACKTEST_INVALID_INPUT"
        print(json.dumps({
            "status": "error", "error_code": code, "message": str(exc),
        }, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
