"""곡선 기준 모델 (검증 전용, float64). C++ cctvlog::curves와 같은 식 (CLAUDE.md 6.4).

실험 경로에서 쓰지 않는다 (CLAUDE.md 1절).
- encode: 선형 x (긴 노출 스케일) → 정규화 신호 V ∈ [0, 1]. x는 [0, x_max]로 자른다.
- decode: V → x. V는 [0, 1]로 자르고 결과는 [0, x_max]로 자른다.

외부 수식 출처
- Rec.709 OETF: ITU-R BT.709-6, 1.2절. V = 4.5L (L < 0.018), 1.099·L^0.45 − 0.099
- HLG OETF: ITU-R BT.2100-2, 표 5. a = 0.17883277, b = 1 − 4a, c = 0.5 − a·ln(4a)
- S-Log3: Sony "Technical Summary for S-Gamut3.Cine/S-Log3 and S-Gamut3/S-Log3".
  in ≥ 0.01125: V = (420 + 261.5·log10((in + 0.01)/0.19)) / 1023
  그 외:        V = (in·(171.2102946929 − 95)/0.01125 + 95) / 1023
"""

from __future__ import annotations

from typing import Any

import numpy as np

HLG_A = 0.17883277
HLG_B = 1.0 - 4.0 * HLG_A
HLG_C = 0.5 - HLG_A * np.log(4.0 * HLG_A)
SLOG3_BREAK_IN = 0.01125
SLOG3_BREAK_CODE = 171.2102946929
# V = 1 (코드 1023)이 되는 S-Log3 입력
SLOG3_IN_MAX = 0.19 * 10.0 ** ((1023.0 - 420.0) / 261.5) - 0.01


def rec709_oetf(L: np.ndarray) -> np.ndarray:
    return np.where(L < 0.018, 4.5 * L, 1.099 * np.power(np.maximum(L, 0.018), 0.45) - 0.099)


def rec709_inv(V: np.ndarray) -> np.ndarray:
    return np.where(V < 0.081, V / 4.5, np.power((np.maximum(V, 0.081) + 0.099) / 1.099, 1 / 0.45))


def hlg_oetf(E: np.ndarray) -> np.ndarray:
    hi = HLG_A * np.log(np.maximum(12.0 * E - HLG_B, 1e-300)) + HLG_C
    return np.where(E <= 1.0 / 12.0, np.sqrt(np.maximum(E, 0.0) * 3.0), hi)


def hlg_inv(V: np.ndarray) -> np.ndarray:
    return np.where(V <= 0.5, V * V / 3.0, (np.exp((V - HLG_C) / HLG_A) + HLG_B) / 12.0)


def slog3_encode(i: np.ndarray) -> np.ndarray:
    hi = (420.0 + 261.5 * np.log10((np.maximum(i, SLOG3_BREAK_IN) + 0.01) / 0.19)) / 1023.0
    lo = (i * (SLOG3_BREAK_CODE - 95.0) / SLOG3_BREAK_IN + 95.0) / 1023.0
    return np.where(i >= SLOG3_BREAK_IN, hi, lo)


def slog3_decode(V: np.ndarray) -> np.ndarray:
    c = V * 1023.0
    hi = 10.0 ** ((c - 420.0) / 261.5) * 0.19 - 0.01
    lo = (c - 95.0) * SLOG3_BREAK_IN / (SLOG3_BREAK_CODE - 95.0)
    return np.where(c >= SLOG3_BREAK_CODE, hi, lo)


def _hlog_seg(curve: dict[str, Any], x: np.ndarray) -> np.ndarray:
    starts = np.array([s["x_start"] for s in curve["segments"]])
    return np.clip(np.searchsorted(starts, x, side="right") - 1, 0, len(starts) - 1)


def hlog_encode(curve: dict[str, Any], x: np.ndarray) -> np.ndarray:
    segs = curve["segments"]
    i = _hlog_seg(curve, x)
    x0 = np.array([s["x_start"] for s in segs])[i]
    a = np.array([s["a"] for s in segs])[i]
    b = np.array([s["b"] for s in segs])[i]
    c0 = np.array([s["c_start"] for s in segs])[i]
    integ = 2.0 * (x - x0) / (np.sqrt(a * x + b) + np.sqrt(a * x0 + b))
    return c0 + curve["k"] * integ


def hlog_decode(curve: dict[str, Any], v: np.ndarray) -> np.ndarray:
    segs = curve["segments"]
    c_starts = np.array([s["c_start"] for s in segs])
    i = np.clip(np.searchsorted(c_starts, v, side="right") - 1, 0, len(segs) - 1)
    x0 = np.array([s["x_start"] for s in segs])[i]
    a = np.array([s["a"] for s in segs])[i]
    b = np.array([s["b"] for s in segs])[i]
    u = (v - c_starts[i]) / curve["k"]
    s0 = np.sqrt(a * x0 + b)
    return x0 + u * (s0 + a * u / 4.0)


def encode(curve: dict[str, Any], x: np.ndarray) -> np.ndarray:
    xm = float(curve["x_max"])
    x = np.clip(np.asarray(x, dtype=np.float64), 0.0, xm)
    t = curve["type"]
    if t == "hlog":
        return hlog_encode(curve, x)
    if t == "rec709":
        return rec709_oetf(x / xm)
    if t == "hlg":
        return hlg_oetf(x / xm)
    if t == "slog3":
        return slog3_encode(x * SLOG3_IN_MAX / xm)
    raise ValueError(f"unknown curve type {t}")


def decode(curve: dict[str, Any], v: np.ndarray) -> np.ndarray:
    xm = float(curve["x_max"])
    v = np.clip(np.asarray(v, dtype=np.float64), 0.0, 1.0)
    t = curve["type"]
    if t == "hlog":
        x = hlog_decode(curve, v)
    elif t == "rec709":
        x = rec709_inv(v) * xm
    elif t == "hlg":
        x = hlg_inv(v) * xm
    elif t == "slog3":
        x = slog3_decode(v) * xm / SLOG3_IN_MAX
    else:
        raise ValueError(f"unknown curve type {t}")
    return np.clip(x, 0.0, xm)


def to_code(v: np.ndarray, bits: int) -> np.ndarray:
    """round-half-up + 포화 (fixed.hpp와 같은 규칙)."""
    cmax = 2**bits - 1
    return np.clip(np.floor(np.asarray(v) * cmax + 0.5), 0, cmax).astype(np.int64)
