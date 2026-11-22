"""브래킷 세트 폴더 읽기 (CLAUDE.md 5.1: bracket_KKKK/{NNNNNN.npy, NNNNNN.json, bracket.json})."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class BracketFrame:
    file: str
    meta: dict[str, Any]
    exposure_us: float
    black: float
    white: float


def frame_black(meta: dict[str, Any]) -> float:
    """프레임 JSON black_level → 숫자.

    4채널 값이 다르면 지원하지 않는다 (C++ hdr_merge와 같은 규칙).
    """
    bl = meta.get("black_level")
    if bl is None:
        raise ValueError("frame JSON has no black_level")
    if isinstance(bl, list):
        if len(set(bl)) != 1:
            raise ValueError(f"per-channel black levels differ (unsupported): {bl}")
        bl = bl[0]
    return float(bl)


def read_bracket(bdir: Path, frames: list[int] | None = None) -> list[BracketFrame]:
    """bracket.json의 files 순서(촬영 순서)대로 프레임 정보를 읽는다. frames로 일부만 고른다."""
    bdir = Path(bdir)
    bj = json.loads((bdir / "bracket.json").read_text())
    files: list[str] = bj["files"]
    idx = list(range(len(files))) if frames is None else frames
    out = []
    for i in idx:
        meta = json.loads((bdir / files[i]).with_suffix(".json").read_text())
        t = meta.get("exposure_time_us")
        if t is None:
            raise ValueError(f"{files[i]}: exposure_time_us is null")
        out.append(
            BracketFrame(
                file=files[i],
                meta=meta,
                exposure_us=float(t),
                black=frame_black(meta),
                white=float(meta["white_level"]),
            )
        )
    return out


def load_raws(bdir: Path, frames: list[BracketFrame]) -> list[np.ndarray]:
    return [np.load(Path(bdir) / f.file) for f in frames]


def write_bracket(
    bdir: Path,
    raws: list[np.ndarray],
    exposures_us: list[float],
    *,
    black: float | list[float] = 64,
    white: int = 1023,
    bayer_order: str = "BGGR",
) -> None:
    """테스트용: 합성 입력 폴더를 capture_raw.py와 같은 형식으로 쓴다."""
    bdir = Path(bdir)
    bdir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, (raw, t) in enumerate(zip(raws, exposures_us, strict=True)):
        stem = f"{i:06d}"
        np.save(bdir / f"{stem}.npy", raw.astype(np.uint16))
        h, w = raw.shape
        meta = {
            "frame_index": i,
            "timestamp_ns": None,
            "sensor_mode": {"size": [w, h], "bit_depth": 10, "hdr": False},
            "bayer_order": bayer_order,
            "exposure_time_us_requested": t,
            "exposure_time_us": t,
            "analogue_gain": 1.1228,
            "digital_gain": None,
            "colour_gains": None,
            "black_level": black,
            "white_level": white,
            "colour_temperature": None,
            "sensor_temperature": None,
            "notes": "synthetic",
        }
        (bdir / f"{stem}.json").write_text(json.dumps(meta))
        files.append(f"{stem}.npy")
    (bdir / "bracket.json").write_text(
        json.dumps(
            {
                "exposure_time_us_requested": exposures_us,
                "exposure_time_us_actual": exposures_us,
                "files": files,
            }
        )
    )
