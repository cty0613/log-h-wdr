import json

import pytest

from cctvlog import cli


def test_build_command_formats_args(tmp_path, monkeypatch):
    fake = tmp_path / "curve_eval"
    fake.write_text("")
    monkeypatch.setenv("CCTVLOG_BIN", str(tmp_path))
    cmd = cli.build_command(
        "curve_eval",
        "in.npy",
        config=tmp_path / "c.json",
        bits=[8, 10],
        dry_run=True,
        skip=None,
        in_="x",
    )
    assert cmd == [
        str(fake),
        "in.npy",
        "--config",
        str(tmp_path / "c.json"),
        "--bits",
        "8,10",
        "--dry-run",
        "--in",
        "x",
    ]


def test_missing_app_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CCTVLOG_BIN", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        cli.run_cpp("does_not_exist")


def test_curve_eval_writes_run_json(tmp_path):
    config = tmp_path / "config.json"
    curve = {"type": "rec709", "name": "rec709", "x_max": 16.0}
    config.write_text(json.dumps(curve))
    out = tmp_path / "out"
    cli.run_cpp("curve_eval", config=config, out=out, bits=10, ramp_n=100)
    run = json.loads((out / "run.json").read_text())
    assert run["app"] == "curve_eval"
    assert run["params"] == curve
    for key in ("version", "git_commit", "timings_ms", "total_ms", "libraries"):
        assert key in run
    assert run["libraries"]["x265"]


def test_cpp_failure_includes_stderr(tmp_path):
    with pytest.raises(cli.CppRunError) as exc:
        cli.run_cpp("curve_eval", out=tmp_path)  # --config 누락
    assert exc.value.returncode == 2
    assert "--config" in exc.value.stderr
    assert "--config" in str(exc.value)
