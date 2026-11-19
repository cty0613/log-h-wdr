"""C++ CLI 호출 래퍼. Python에서 C++를 부르는 유일한 경로 (CLAUDE.md 1절)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BIN_DIR = REPO_ROOT / "build" / "release" / "apps"
DEFAULT_TEST_BIN_DIR = REPO_ROOT / "build" / "release" / "tests" / "bin"


class CppRunError(RuntimeError):
    """C++ CLI가 0이 아닌 코드로 끝났을 때. stderr를 포함한다."""

    def __init__(self, cmd: Sequence[str], returncode: int, stdout: str, stderr: str) -> None:
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"{Path(cmd[0]).name} exited with code {returncode}\n"
            f"cmd: {' '.join(cmd)}\n"
            f"stderr:\n{stderr.rstrip()}"
        )


def bin_dir() -> Path:
    """C++ CLI 위치: 환경변수 CCTVLOG_BIN, 없으면 build/release/apps."""
    env = os.environ.get("CCTVLOG_BIN")
    return Path(env).expanduser().resolve() if env else DEFAULT_BIN_DIR


def tools_bin_dir() -> Path:
    """테스트 전용 C++ 도구 위치: 환경변수 CCTVLOG_TEST_BIN, 없으면 build/release/tests/bin."""
    env = os.environ.get("CCTVLOG_TEST_BIN")
    return Path(env).expanduser().resolve() if env else DEFAULT_TEST_BIN_DIR


def app_path(app: str, directory: Path | None = None) -> Path:
    path = (directory if directory is not None else bin_dir()) / app
    if not path.is_file():
        raise FileNotFoundError(
            f"C++ CLI '{app}' not found at {path}. "
            "Build with `cmake --preset release && cmake --build --preset release` "
            "or set CCTVLOG_BIN."
        )
    return path


def _format_value(value: Any) -> str:
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    if isinstance(value, (list, tuple)):
        # 리스트는 쉼표로 이은 하나의 인자 (C++ cli::Args 규약)
        return ",".join(_format_value(v) for v in value)
    return str(value)


def build_command(
    app: str, *positional: Any, directory: Path | None = None, **args: Any
) -> list[str]:
    """`run_cpp`가 실행할 명령 줄을 만든다.

    키워드 인자 `foo_bar=v`는 `--foo-bar v`, `in_=v`는 `--in v`가 된다. `True`는 값 없는 플래그,
    `False`/`None`은 생략한다. `directory`를 주면 `bin_dir()` 대신 그 폴더에서 찾는다.
    """
    cmd = [os.fspath(app_path(app, directory))]
    cmd += [_format_value(p) for p in positional]
    for key, value in args.items():
        if value is None or value is False:
            continue
        # 예약어 회피용 끝 밑줄 제거: in_=... → --in
        flag = "--" + key.rstrip("_").replace("_", "-")
        if value is True:
            cmd.append(flag)
        else:
            cmd += [flag, _format_value(value)]
    return cmd


def run_cpp(
    app: str,
    *positional: Any,
    timeout: float | None = None,
    directory: Path | None = None,
    **args: Any,
) -> subprocess.CompletedProcess[str]:
    """C++ CLI `app`을 실행한다. 실패하면 stderr를 담은 `CppRunError`를 던진다.

    예: ``run_cpp("curve_eval", config="configs/curves.json", out=out_dir)``
    """
    cmd = build_command(app, *positional, directory=directory, **args)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise CppRunError(cmd, proc.returncode, proc.stdout, proc.stderr)
    return proc
