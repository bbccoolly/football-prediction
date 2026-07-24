"""Persistent cross-process state for background backtest jobs."""

from __future__ import annotations

import ctypes
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from backtest.storage import atomic_write_json


ACTIVE_STATES = {"queued", "running"}


class BacktestAlreadyRunningError(RuntimeError):
    def __init__(self, run_id):
        super().__init__("已有回测任务正在运行")
        self.run_id = run_id


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def process_identity(pid):
    if not pid or pid <= 0:
        return None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return {"pid": pid, "created_at": None}
        except (OSError, ProcessLookupError, PermissionError):
            return None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        if exit_code.value != 259:
            return None
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            return {"pid": pid, "created_at": None}
        unix_seconds = creation.value / 10_000_000 - 11_644_473_600
        created_at = datetime.fromtimestamp(unix_seconds, timezone.utc).isoformat()
        return {"pid": pid, "created_at": created_at}
    finally:
        kernel32.CloseHandle(handle)


class BacktestTaskStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock_path = self.root / ".active.json"

    def run_dir(self, run_id):
        return self.root / run_id

    def status_path(self, run_id):
        return self.run_dir(run_id) / "status.json"

    def read_status(self, run_id):
        path = self.status_path(run_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _read_lock(self):
        try:
            return json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def reserve(self, run_id):
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "pid": None,
            "process_created_at": None,
            "reserved_at": utc_now(),
        }
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        try:
            descriptor = os.open(
                self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            current = self._read_lock() or {}
            if current.get("run_id") != run_id:
                raise BacktestAlreadyRunningError(current.get("run_id"))
            return current
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        self.update_status(run_id, {
            "schema_version": 1,
            "run_id": run_id,
            "state": "queued",
            "outcome": None,
            "pid": None,
            "process_created_at": None,
            "queued_at": utc_now(),
            "started_at": None,
            "heartbeat_at": utc_now(),
            "finished_at": None,
            "exit_code": None,
            "progress": {
                "phase": "queued", "current_batch": 0, "total_batches": 0,
                "processed_matches": 0, "total_matches": 0, "percent": 0,
            },
            "error": None,
        }, replace=True)
        return payload

    def claim(self, run_id, pid):
        current = self._read_lock()
        if current is None:
            self.reserve(run_id)
            current = self._read_lock()
        if current.get("run_id") != run_id:
            raise BacktestAlreadyRunningError(current.get("run_id"))
        identity = process_identity(pid) or {"pid": pid, "created_at": None}
        lock = {
            **current,
            "pid": pid,
            "process_created_at": identity.get("created_at"),
        }
        atomic_write_json(self.lock_path, lock)
        self.update_status(run_id, {
            "state": "running",
            "pid": pid,
            "process_created_at": identity.get("created_at"),
            "started_at": utc_now(),
            "heartbeat_at": utc_now(),
        })
        return lock

    def update_status(self, run_id, changes, *, replace=False):
        current = {} if replace else (self.read_status(run_id) or {})
        current.update(changes)
        atomic_write_json(self.status_path(run_id), current)
        return current

    def update_progress(self, run_id, **progress):
        return self.update_status(run_id, {
            "heartbeat_at": utc_now(),
            "progress": progress,
        })

    def complete(self, run_id, exit_code, outcome):
        return self.update_status(run_id, {
            "state": "completed",
            "outcome": outcome,
            "heartbeat_at": utc_now(),
            "finished_at": utc_now(),
            "exit_code": exit_code,
            "error": None,
        })

    def fail(self, run_id, error_code, message, exit_code=1):
        return self.update_status(run_id, {
            "state": "failed",
            "outcome": "failed",
            "heartbeat_at": utc_now(),
            "finished_at": utc_now(),
            "exit_code": exit_code,
            "error": {"code": error_code, "message": str(message)[:500]},
        })

    def release(self, run_id):
        current = self._read_lock()
        if current and current.get("run_id") == run_id:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def latest_run_id(self, *, completed_only=False):
        if not self.root.exists():
            return None
        candidates = []
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            status = self.read_status(path.name)
            if not status:
                continue
            if completed_only and status.get("state") != "completed":
                continue
            timestamp = (
                status.get("finished_at") or status.get("started_at")
                or status.get("queued_at") or ""
            )
            candidates.append((timestamp, path.name))
        return max(candidates)[1] if candidates else None

    def recover(self):
        recovered = []
        if not self.root.exists():
            return recovered
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            status = self.read_status(path.name)
            if not status or status.get("state") not in ACTIVE_STATES:
                continue
            identity = process_identity(status.get("pid"))
            expected_created_at = status.get("process_created_at")
            same_process = bool(identity)
            if same_process and expected_created_at and identity.get("created_at"):
                same_process = identity["created_at"] == expected_created_at
            if not same_process:
                self.update_status(path.name, {
                    "state": "interrupted",
                    "outcome": "interrupted",
                    "finished_at": utc_now(),
                    "heartbeat_at": utc_now(),
                    "exit_code": None,
                    "error": {
                        "code": "BACKTEST_INTERRUPTED",
                        "message": "回测进程已不存在",
                    },
                })
                self.release(path.name)
                recovered.append(path.name)
        lock = self._read_lock()
        if lock:
            status = self.read_status(lock.get("run_id"))
            if not status or status.get("state") not in ACTIVE_STATES:
                self.release(lock.get("run_id"))
        return recovered
