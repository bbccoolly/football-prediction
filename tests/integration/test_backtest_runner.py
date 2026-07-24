import json
from pathlib import Path

from backtest import BacktestConfig, BacktestRunner
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

    assert first["result_fingerprint"] == second["result_fingerprint"]
    assert first["manifest"]["data"]["partitions"]["training"] == 18
    assert first["manifest"]["data"]["partitions"]["validation"] == 6
    assert first["manifest"]["data"]["partitions"]["holdout"] == 6
    assert not first["admission"]["formal_data_ready"]
    first_prediction = json.loads(
        (first["output_dir"] / "predictions.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert first_prediction["derived"]["ensemble"]["available"] is True
    repository.close()
