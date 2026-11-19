import json

import yaml

from cctvlog import runner


def test_start_experiment_creates_result_dir(tmp_path):
    cfg = tmp_path / "p0_smoke.yaml"
    cfg.write_text(yaml.safe_dump({"name": "p0_smoke", "seed": 1234}))
    exp = runner.start_experiment(cfg, results_root=tmp_path / "results")

    assert exp.name == "p0_smoke"
    assert exp.config["seed"] == 1234
    assert exp.out_dir.parent == tmp_path / "results" / "p0_smoke"
    assert (exp.out_dir / "config.yaml").read_text() == cfg.read_text()
    env = json.loads((exp.out_dir / "env.json").read_text())
    assert {"python", "platform", "packages", "git", "cctvlog_bin"} <= env.keys()

    # 같은 초에 다시 실행해도 덮어쓰지 않는다
    exp2 = runner.start_experiment(cfg, results_root=tmp_path / "results")
    assert exp2.out_dir != exp.out_dir
