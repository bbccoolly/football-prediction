import json
from pathlib import Path

import pytest

from backtest import BacktestConfig, BacktestRunner
from backtest.contracts import BacktestCheckpointError
from backtest.storage import BacktestCheckpointStore
from backtest.storage import atomic_write_text
from calibrate_cli import _prepare_fixture


FIXTURE = Path(__file__).parents[1] / "fixtures" / "backtest_matches.json"


def test_repeated_fixture_backtests_have_same_scientific_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr("models.monte_carlo.MC_SIMULATIONS", 100)
    database_root = tmp_path / "database"
    database_root.mkdir()
    repository, _ = _prepare_fixture(
        FIXTURE, database_root, "2027-01-01T00:00:00+00:00"
    )
    config = BacktestConfig(
        as_of="2027-01-01T00:00:00Z",
        output_root=tmp_path / "output",
        bootstrap_iterations=10,
    )
    provenance = {
        "code_commit": "a" * 40, "code_dirty": False,
        "branch": "test", "commit_source": "test",
    }
    runner = BacktestRunner(
        repository, config, artifact_root=tmp_path / "models",
        provenance=provenance,
    )

    first = runner.run("bt-repeat-first")
    second = runner.run("bt-repeat-second")
    committed = runner.run("bt-repeat-first", resume=True)

    assert first["result_fingerprint"] == second["result_fingerprint"]
    assert committed["result_fingerprint"] == first["result_fingerprint"]
    assert first["manifest"]["data"]["partitions"]["training"] == 18
    assert first["manifest"]["data"]["partitions"]["validation"] == 6
    assert first["manifest"]["data"]["partitions"]["holdout"] == 6
    assert not first["admission"]["formal_data_ready"]
    first_prediction = json.loads(
        (first["output_dir"] / "predictions.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert first_prediction["derived"]["ensemble"]["available"] is True
    repository.close()


def test_interrupted_run_resumes_to_same_outputs_as_continuous_run(tmp_path, monkeypatch):
    database_root = tmp_path / "database"
    database_root.mkdir()
    repository, _ = _prepare_fixture(
        FIXTURE, database_root, "2027-01-01T00:00:00+00:00"
    )
    config = BacktestConfig(
        as_of="2027-01-01T00:00:00Z",
        output_root=tmp_path / "output",
        bootstrap_iterations=10,
        research_only=True,
    )
    provenance = {
        "code_commit": "a" * 40, "code_dirty": False,
        "branch": "test", "commit_source": "test",
    }
    runner = BacktestRunner(
        repository, config, artifact_root=tmp_path / "models",
        provenance=provenance,
    )
    original_write_batch = BacktestCheckpointStore.write_batch
    interrupted = {"raised": False}

    def interrupt_after_commit(self, sequence, *args, **kwargs):
        result = original_write_batch(self, sequence, *args, **kwargs)
        if sequence == 3 and not interrupted["raised"]:
            interrupted["raised"] = True
            raise RuntimeError("fault injection")
        return result

    monkeypatch.setattr(
        BacktestCheckpointStore, "write_batch", interrupt_after_commit
    )
    with pytest.raises(RuntimeError, match="fault injection"):
        runner.run("bt-resumed")
    monkeypatch.setattr(BacktestCheckpointStore, "write_batch", original_write_batch)

    resumed = runner.run("bt-resumed", resume=True)
    continuous = runner.run("bt-continuous")

    assert resumed["scoring_fingerprint"] == continuous["scoring_fingerprint"]
    assert resumed["result_fingerprint"] == continuous["result_fingerprint"]
    for name in ("predictions.jsonl", "metrics.json", "admission.json"):
        assert (
            (resumed["output_dir"] / name).read_bytes()
            == (continuous["output_dir"] / name).read_bytes()
        )
    repository.close()


def test_resume_rejects_tampered_segment(tmp_path):
    store = BacktestCheckpointStore(tmp_path / "bt-run", "bt-run")
    batches = [("validation", {
        "batch_id": "validation-batch-00001",
        "cutoff": "2026-01-01T00:00:00+00:00",
    })]
    store.write_batch(
        1, *batches[0], [{"match": "one"}],
        input_fingerprint="input", spec_fingerprint="spec",
        processed_matches=1,
    )
    segment = tmp_path / "bt-run" / "segments" / "validation-batch-00001.jsonl"
    segment.write_text('{"match":"changed"}\n', encoding="utf-8")

    with pytest.raises(BacktestCheckpointError, match="校验和"):
        store.validate(
            batches, input_fingerprint="input", spec_fingerprint="spec"
        )


def test_atomic_write_retries_transient_windows_share_violation(
    tmp_path, monkeypatch
):
    target = tmp_path / "status.json"
    original_replace = __import__("os").replace
    attempts = {"count": 0}

    def transient_replace(source, destination):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("sharing violation")
        return original_replace(source, destination)

    monkeypatch.setattr("backtest.storage.os.replace", transient_replace)

    atomic_write_text(target, "ok\n")

    assert target.read_text(encoding="utf-8") == "ok\n"
    assert attempts["count"] == 3
