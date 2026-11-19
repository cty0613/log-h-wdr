"""실험 실행 공통부: config 로드, 결과 폴더 생성, 환경 기록 (CLAUDE.md 8절)."""

from __future__ import annotations

import datetime as dt
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

import yaml

from cctvlog import cli

RESULTS_ROOT = cli.REPO_ROOT / "results"


@dataclass(frozen=True)
class Experiment:
    name: str
    config: dict[str, Any]
    config_path: Path
    out_dir: Path


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        if path.suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(f)
        elif path.suffix == ".json":
            data = json.load(f)
        else:
            raise ValueError(f"unsupported config type: {path}")
    if not isinstance(data, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return data


def git_info(repo: Path = cli.REPO_ROOT) -> dict[str, Any]:
    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=repo, capture_output=True, text=True, check=True
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.stdout.strip()

    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {"commit": commit, "dirty": bool(status) if status is not None else None}


def environment_info() -> dict[str, Any]:
    packages = {}
    for name in ("cctvlog", "numpy", "pyyaml", "matplotlib"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "packages": packages,
        "git": git_info(),
        "cctvlog_bin": str(cli.bin_dir()),
    }


def start_experiment(config_path: str | Path, results_root: str | Path | None = None) -> Experiment:
    """config를 읽고 `results/<name>/<timestamp>/`를 만든 뒤 config 사본·환경 정보를 남긴다.

    실험 이름은 config의 `name` 키, 없으면 config 파일 이름.
    """
    config_path = Path(config_path).resolve()
    config = load_config(config_path)
    name = str(config.get("name", config_path.stem))
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    root = Path(results_root) if results_root is not None else RESULTS_ROOT
    out_dir = root / name / stamp
    suffix = 1
    while out_dir.exists():
        out_dir = root / name / f"{stamp}_{suffix}"
        suffix += 1
    out_dir.mkdir(parents=True)
    shutil.copy2(config_path, out_dir / f"config{config_path.suffix}")
    (out_dir / "env.json").write_text(
        json.dumps(environment_info(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return Experiment(name=name, config=config, config_path=config_path, out_dir=out_dir)
