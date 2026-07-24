import json

import pytest

from backtest.tasks import BacktestAlreadyRunningError, BacktestTaskStore


def test_task_store_reservation_is_mutually_exclusive(tmp_path):
    store = BacktestTaskStore(tmp_path)
    store.reserve("bt-first")

    with pytest.raises(BacktestAlreadyRunningError) as exc_info:
        store.reserve("bt-second")

    assert exc_info.value.run_id == "bt-first"


def test_exit_code_two_is_a_completed_insufficient_data_run(tmp_path):
    store = BacktestTaskStore(tmp_path)
    store.reserve("bt-research")
    store.complete("bt-research", 2, "insufficient_data")

    status = store.read_status("bt-research")

    assert status["state"] == "completed"
    assert status["outcome"] == "insufficient_data"
    assert status["exit_code"] == 2


def test_recovery_marks_missing_process_as_interrupted(tmp_path, monkeypatch):
    store = BacktestTaskStore(tmp_path)
    store.reserve("bt-interrupted")
    store.update_status("bt-interrupted", {
        "state": "running", "pid": 999999, "process_created_at": None,
    })
    monkeypatch.setattr("backtest.tasks.process_identity", lambda _pid: None)

    recovered = store.recover()

    assert recovered == ["bt-interrupted"]
    status = store.read_status("bt-interrupted")
    assert status["state"] == "interrupted"
    assert status["error"]["code"] == "BACKTEST_PROCESS_LOST"
    assert status["resumable"] is True
    assert not store.lock_path.exists()


def test_only_interrupted_runs_with_stable_codes_can_resume(tmp_path):
    store = BacktestTaskStore(tmp_path)
    store.reserve("bt-resume")
    store.interrupt(
        "bt-resume", "BACKTEST_USER_INTERRUPTED", "用户中断", exit_code=130
    )
    store.release("bt-resume")

    reservation = store.reserve("bt-resume", resume=True)

    assert reservation["attempt_id"]
    assert store.read_status("bt-resume")["state"] == "queued"
