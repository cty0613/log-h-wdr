"""libcamera Raspberry Pi 튜닝 JSON에서 CCM과 ALSC 테이블을 추출한다 (Phase 0).

출력: configs/tuning_ccm.json, configs/tuning_alsc.json. Phase 4 C++ ISP가 이 파일을 읽어 적용한다.
여기서는 파라미터만 만든다. 화소 연산은 하지 않는다 (CLAUDE.md 1절).

해석 규칙의 출처는 raspberrypi/libcamera `main` (2026-11-20에 읽음)이다.
- controller.cpp: ALSC 격자 = hardware config `awbRegions` (pisp 32x32, bcm2835/vc4 16x12)
- rpi/alsc.cpp:
  - 테이블은 행 우선 (y 바깥, x 안쪽)
  - ct 보간은 K 단위 선형, 범위 밖은 끝값으로 고정
  - 적응 단계를 끈 보정 이득: R = calR/min(calR)·L', G = L', B = calB/min(calB)·L',
    L' = (L-1)·luminance_strength + 1. ct 보간을 먼저 하고, 그다음 min으로 정규화한다
  - 테이블은 센서 원래 방향이다. 센서 transform에 HFlip/VFlip이 있으면 실행 중에 x/y를 뒤집는다
- rpi/ccm.cpp:
  - ccm은 행 우선 3x3, out = M·[R,G,B]^T
  - ct 보간은 원소별 선형, 범위 밖은 끝값으로 고정
  - saturation은 Y2RGB·S·RGB2Y로 곱해진다. 튜닝에 saturation 항목이 없으면 S = I

사용:
  python -m cctvlog.calib.tuning_import \
      --tuning data/pi_probe/20261120/tuning/pisp__imx708_wide.json \
      --camera-model imx708_wide --out-dir configs
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

# controller.cpp hardware config: awbRegions (width, height)
ALSC_GRID_BY_TARGET: dict[str, tuple[int, int]] = {"pisp": (32, 32), "bcm2835": (16, 12)}
REFERENCE = {
    "repo": "https://github.com/raspberrypi/libcamera",
    "branch": "main",
    "read_date": "2026-11-20",
    "files": [
        "src/ipa/rpi/controller/controller.cpp",
        "src/ipa/rpi/controller/rpi/alsc.cpp",
        "src/ipa/rpi/controller/rpi/ccm.cpp",
    ],
}


def _algorithms(tuning: Mapping[str, Any]) -> dict[str, Any]:
    algos = tuning.get("algorithms")
    if not isinstance(algos, list):
        raise ValueError("tuning JSON v2 expected: top-level 'algorithms' list")
    out: dict[str, Any] = {}
    for entry in algos:
        ((name, body),) = entry.items()
        out[name] = body
    return out


def _source(path: Path, tuning: Mapping[str, Any], camera_model: str | None) -> dict[str, Any]:
    return {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "camera_model": camera_model,
        "target": tuning.get("target", "bcm2835"),
        "version": tuning.get("version"),
    }


def _check_ct_increasing(cts: Sequence[float], what: str) -> None:
    if not cts or any(b <= a for a, b in itertools.pairwise(cts)) or cts[0] <= 0:
        raise ValueError(f"{what}: ct must be positive and strictly increasing: {list(cts)}")


def extract_ccm(tuning: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    body = _algorithms(tuning)["rpi.ccm"]
    if "saturation" in body:
        raise NotImplementedError("tuning has rpi.ccm saturation Pwl; not handled (S = I assumed)")
    entries = []
    for c in body["ccms"]:
        m = np.asarray(c["ccm"], dtype=np.float64)
        if m.size != 9:
            raise ValueError(f"ccm at ct {c['ct']} has {m.size} values")
        m = m.reshape(3, 3)
        entries.append(
            {"ct": c["ct"], "matrix": m.tolist(), "row_sums": np.round(m.sum(1), 6).tolist()}
        )
    _check_ct_increasing([e["ct"] for e in entries], "rpi.ccm")
    return {
        "kind": "tuning_ccm",
        "source": dict(source),
        "layout": "row-major 3x3; out = M @ [R, G, B]^T (WB 적용 후 카메라 RGB 입력)",
        "ct_interpolation": "linear in kelvin, element-wise; clamp to first/last outside range",
        "saturation": "tuning has no saturation Pwl -> S = I (Y2RGB*S*RGB2Y omitted)",
        "reference": REFERENCE,
        "ccms": entries,
    }


def extract_alsc(tuning: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    body = _algorithms(tuning)["rpi.alsc"]
    target = str(source["target"])
    if target not in ALSC_GRID_BY_TARGET:
        raise ValueError(f"unknown target {target!r}")
    w, h = ALSC_GRID_BY_TARGET[target]
    if "corner_strength" in body:
        raise NotImplementedError("corner_strength (generated luminance LUT) not handled")

    def grid(values: Sequence[float], what: str) -> list[list[float]]:
        a = np.asarray(values, dtype=np.float64)
        if a.size != w * h:
            raise ValueError(f"{what}: {a.size} values, expected {w}x{h}={w * h}")
        return a.reshape(h, w).tolist()

    def cals(key: str) -> list[dict[str, Any]]:
        out = [{"ct": c["ct"], "table": grid(c["table"], f"{key}@{c['ct']}")} for c in body[key]]
        _check_ct_increasing([c["ct"] for c in out], key)
        return out

    lum = body.get("luminance_lut")
    return {
        "kind": "tuning_alsc",
        "source": dict(source),
        "grid": {"width": w, "height": h},
        "order": "row-major: table[y][x], y = 0 is the top row in sensor-native orientation",
        "orientation": "sensor_native",
        "flip_rule": (
            "frame sensor_flip.horizontal -> x' = W-1-x, sensor_flip.vertical -> y' = H-1-y "
            "(alsc.cpp resampleCalTable)"
        ),
        "gain_formula": (
            "cal = ct-interp(calibrations); R = cal_cr/min(cal_cr) * L'; G = L'; "
            "B = cal_cb/min(cal_cb) * L'; L' = (luminance_lut - 1) * luminance_strength + 1 "
            "(adaptive lambda iteration not applied)"
        ),
        "ct_interpolation": "linear in kelvin; clamp to first/last outside range",
        "luminance_strength": float(body.get("luminance_strength", 1.0)),
        "luminance_lut": grid(lum, "luminance_lut") if lum is not None else None,
        "calibrations_cr": cals("calibrations_Cr"),
        "calibrations_cb": cals("calibrations_Cb"),
        "adaptive_params_unused": {
            k: body[k] for k in ("omega", "n_iter", "sigma", "sigma_Cb") if k in body
        },
        "reference": REFERENCE,
    }


# ---- 파라미터 계산 헬퍼 (Phase 4 C++ 결과 검증용 기준값) ---------------------------------------
def _interp_ct(entries: Sequence[Mapping[str, Any]], ct: float, key: str) -> np.ndarray:
    cts = [e["ct"] for e in entries]
    if ct <= cts[0]:
        return np.asarray(entries[0][key], dtype=np.float64)
    if ct >= cts[-1]:
        return np.asarray(entries[-1][key], dtype=np.float64)
    i = int(np.searchsorted(cts, ct, side="right"))
    ct0, ct1 = cts[i - 1], cts[i]
    a0 = np.asarray(entries[i - 1][key], dtype=np.float64)
    a1 = np.asarray(entries[i][key], dtype=np.float64)
    return (a0 * (ct1 - ct) + a1 * (ct - ct0)) / (ct1 - ct0)


def ccm_at(ccm_json: Mapping[str, Any], ct: float) -> np.ndarray:
    """색온도 ct(K)의 3x3 CCM."""
    return _interp_ct(ccm_json["ccms"], ct, "matrix")


def alsc_gains(
    alsc_json: Mapping[str, Any],
    ct: float,
    luminance_strength: float | None = None,
    flip_h: bool = False,
    flip_v: bool = False,
) -> np.ndarray:
    """보정 이득 격자 (3, H, W) = R, G, B. flip은 RAW의 센서 flip 상태."""
    s = alsc_json["luminance_strength"] if luminance_strength is None else luminance_strength
    h, w = alsc_json["grid"]["height"], alsc_json["grid"]["width"]
    lut = alsc_json["luminance_lut"]
    lum = np.ones((h, w)) if lut is None else (np.asarray(lut, dtype=np.float64) - 1.0) * s + 1.0
    cr = _interp_ct(alsc_json["calibrations_cr"], ct, "table")
    cb = _interp_ct(alsc_json["calibrations_cb"], ct, "table")
    gains = np.stack([cr / cr.min() * lum, lum, cb / cb.min() * lum])
    if flip_h:
        gains = gains[:, :, ::-1]
    if flip_v:
        gains = gains[:, ::-1, :]
    return np.ascontiguousarray(gains)


def import_tuning(
    tuning_path: Path, camera_model: str | None
) -> tuple[dict[str, Any], dict[str, Any]]:
    tuning = json.loads(Path(tuning_path).read_text())
    src = _source(Path(tuning_path), tuning, camera_model)
    return extract_ccm(tuning, src), extract_alsc(tuning, src)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--tuning", type=Path, required=True, help="Pi 튜닝 JSON (pisp/<model>.json)")
    ap.add_argument("--camera-model", default=None, help="예: imx708_wide (기록용)")
    ap.add_argument("--out-dir", type=Path, default=Path("configs"))
    args = ap.parse_args(argv)

    ccm, alsc = import_tuning(args.tuning, args.camera_model)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (("tuning_ccm.json", ccm), ("tuning_alsc.json", alsc)):
        path = args.out_dir / name
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    print(
        f"ccm: {len(ccm['ccms'])} entries, ct {[c['ct'] for c in ccm['ccms']]}; "
        f"alsc: grid {alsc['grid']['width']}x{alsc['grid']['height']}, "
        f"cal ct {[c['ct'] for c in alsc['calibrations_cr']]}, "
        f"luminance_strength {alsc['luminance_strength']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
