"""Photon Transfer Curve 분석 (Phase 1).

`capture/capture_ptc.py`의 flat·dark 실행 결과에서, 게인마다 시간적 노이즈 분산 σ²와 평균 μ의
관계 σ² = K·μ + b (DN 단위)를 구한다. K = DN/e⁻ (변환 이득의 역수), b = 판독 노이즈 분산.

방법 (docs/whitepaper/03_noise_and_ptc.md):
- 쌍 차분: σ² = Var(A − B) / 2. 고정 패턴(PRNU·DSNU)이 상쇄된다.
- 채널(2x2 Bayer 위치)마다 따로 계산. K는 전자 회로 값이라 채널과 무관해야 하므로 피팅은 네
  채널을 함께 쓰고, 채널별 K는 일관성 확인용으로 따로 보고한다.
- 블랙 레벨은 다크 프레임 실측값(게인·채널별)을 쓴다.
- 제외: ROI에 포화 화소가 있는 쌍, 평균이 max_level·(white − black)를 넘는 쌍(포화 직전),
  깜빡임 오염 쌍(행 평균을 뺀 분산과의 차이가 다크 프레임의 행 노이즈 몫보다 flicker_flag 이상 큼).
- 피팅 두 가지: free (K, b 자유) / fixed_b (b = 다크 프레임 쌍 차분 분산, K만). 상대오차가 같다고
  보고 가중치 1/σ⁴(= 1/y²)를 쓴다. 신호 구간별 국소 기울기 (σ² − b_dark)/μ로 휨을 본다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

CHANNEL_BAYER_POS = ((0, 0), (0, 1), (1, 0), (1, 1))


@dataclass(frozen=True)
class PtcParams:
    roi: int = 256  # 센서 화소, 중앙
    max_level: float = 0.9  # 포화 직전 제외 (정규화 평균)
    flicker_flag: float = 0.01  # 행 평균 제거 전후 분산 차이 − 다크의 같은 차이
    low_level: float = 0.15  # 국소 기울기 "low" 구간 상한 (정규화)
    high_level: float = 0.5  # "high" 구간 하한


# ---------------------------------------------------------------- 화소 통계


def channel_labels(bayer_order: str) -> list[str]:
    """2x2 위치 [(0,0), (0,1), (1,0), (1,1)]의 색 이름. 초록 둘은 G1(행 0쪽), G2."""
    labels, g = [], 0
    for c in bayer_order.upper():
        if c == "G":
            g += 1
            labels.append(f"G{g}")
        else:
            labels.append(c)
    return labels


def center_crop(img: np.ndarray, roi: int) -> np.ndarray:
    """짝수 정렬 중앙 roi x roi (Bayer 위상 유지). 마지막 두 축 기준."""
    h, w = img.shape[-2:]
    y0, x0 = (h // 2 - roi // 2) & ~1, (w // 2 - roi // 2) & ~1
    return img[..., y0 : y0 + roi, x0 : x0 + roi]


def planes(img: np.ndarray) -> list[np.ndarray]:
    return [img[..., dy::2, dx::2] for dy, dx in CHANNEL_BAYER_POS]


def pair_variance(d: np.ndarray) -> tuple[float, float]:
    """차분 영상 d = A − B (rows, cols) → (Var/2, 행 평균을 뺀 Var/2).
    행 평균을 빼면 행마다 자유도 1을 잃으므로 분모는 rows·(cols − 1)."""
    v = float(d.var(ddof=1)) / 2.0
    r = d - d.mean(axis=-1, keepdims=True)
    vr = float((r * r).sum() / (r.size - d.shape[-2])) / 2.0
    return v, vr


def lag1_corr(d: np.ndarray, axis: int) -> float:
    a = np.moveaxis(d, axis, 0)
    x, y = a[:-1] - a[:-1].mean(), a[1:] - a[1:].mean()
    den = np.sqrt((x * x).mean() * (y * y).mean())
    return float((x * y).mean() / den) if den > 0 else float("nan")


def pair_point(
    a: np.ndarray, b: np.ndarray, black: list[float], white: float, labels: list[str]
) -> list[dict[str, Any]]:
    """ROI로 자른 원시 프레임 쌍(uint16) → 채널별 평균·분산."""
    out = []
    for c, (pa, pb) in enumerate(zip(planes(a), planes(b), strict=True)):
        pa64, pb64 = pa.astype(np.float64), pb.astype(np.float64)
        d = pa64 - pb64
        v, vr = pair_variance(d)
        out.append(
            {
                "channel": labels[c],
                "mean_dn": float((pa64.mean() + pb64.mean()) / 2.0 - black[c]),
                "mean_diff_rel": float(
                    (pa64.mean() - pb64.mean()) / max(pa64.mean() - black[c], 1e-9)
                ),
                "var_dn2": v,
                "var_rowdet_dn2": vr,
                "sat_fraction": float(((pa >= white) | (pb >= white)).mean()),
                "corr_h": lag1_corr(d, 1),
                "corr_v": lag1_corr(d, 0),
                "n_pixels": int(d.size),
            }
        )
    return out


def dark_stats(stack: np.ndarray, labels: list[str]) -> list[dict[str, Any]]:
    """ROI로 자른 다크 스택 (N, H, W) → 채널별 블랙 레벨, 시간적 분산, 행 노이즈, DSNU."""
    out = []
    for c, p in enumerate(planes(stack.astype(np.float64))):
        n = p.shape[0]
        pv = [pair_variance(p[i] - p[i + 1]) for i in range(0, n - 1, 2)]
        v = float(np.mean([x[0] for x in pv]))
        vr = float(np.mean([x[1] for x in pv]))
        temporal = float(p.var(axis=0, ddof=1).mean())
        mean_img = p.mean(axis=0)
        dsnu_var = max(float(mean_img.var(ddof=1)) - temporal / n, 0.0)
        row_t = p.mean(axis=2)  # (N, rows)
        row_var = float(row_t.var(axis=0, ddof=1).mean())
        out.append(
            {
                "channel": labels[c],
                "black_dn": float(p.mean()),
                "var_pair_dn2": v,
                "var_pair_rowdet_dn2": vr,
                "rowdet_excess": v / vr - 1.0,
                "var_temporal_dn2": temporal,
                "row_noise_var_dn2": max(row_var - temporal / p.shape[2], 0.0),
                "dsnu_dn": float(np.sqrt(dsnu_var)),
                "n_frames": n,
            }
        )
    return out


# ---------------------------------------------------------------- 피팅


def r_squared(x: np.ndarray, y: np.ndarray, k: float, b: float) -> float:
    ss_res = float(((y - (k * x + b)) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def fit_free(x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """가중 최소제곱 y = K·x + b, 가중치 1/y² (상대오차 균일)."""
    w = 1.0 / np.maximum(y, 1e-12) ** 2
    A = np.vstack([x, np.ones_like(x)]).T * np.sqrt(w)[:, None]
    (k, b), *_ = np.linalg.lstsq(A, y * np.sqrt(w), rcond=None)
    return {"K": float(k), "b": float(b), "r2": r_squared(x, y, float(k), float(b))}


def fit_fixed_b(x: np.ndarray, y: np.ndarray, b: float) -> dict[str, float]:
    """b 고정, K만: min Σ w (y − b − K x)², w = 1/y²."""
    w = 1.0 / np.maximum(y, 1e-12) ** 2
    k = float((w * x * (y - b)).sum() / (w * x * x).sum())
    return {"K": k, "b": float(b), "r2": r_squared(x, y, k, b)}


def local_slopes(
    x: np.ndarray, y: np.ndarray, b_dark: float, full_scale: float, p: PtcParams
) -> dict[str, Any]:
    ks = (y - b_dark) / x
    lo, hi = x <= p.low_level * full_scale, x >= p.high_level * full_scale
    out: dict[str, Any] = {
        "K_low": float(np.median(ks[lo])) if lo.any() else None,
        "K_high": float(np.median(ks[hi])) if hi.any() else None,
    }
    if out["K_low"] and out["K_high"]:
        out["high_over_low"] = out["K_high"] / out["K_low"]
    return out


# ---------------------------------------------------------------- 실행 단위


def _load(run_dir: Path, rel: str, roi: int) -> tuple[np.ndarray, dict[str, Any]]:
    p = run_dir / rel
    return center_crop(np.load(p), roi), json.loads(p.with_suffix(".json").read_text())


def analyze_dark(dark_dir: Path, p: PtcParams) -> dict[str, Any]:
    frames = json.loads((dark_dir / "ptc.json").read_text())["frames"]
    out: dict[str, Any] = {}
    for label in dict.fromkeys(f["gain_label"] for f in frames):
        sets = []
        for t in sorted({f["exposure_us_requested"] for f in frames if f["gain_label"] == label}):
            fs = [f for f in frames if f["gain_label"] == label and f["exposure_us_requested"] == t]
            loaded = [_load(dark_dir, f["file"], p.roi) for f in fs]
            meta = loaded[0][1]
            labels = channel_labels(meta["bayer_order"])
            sets.append(
                {
                    "exposure_us": meta["exposure_time_us"],
                    "analogue_gain": meta["analogue_gain"],
                    "sensor_temperature": meta.get("sensor_temperature"),
                    "channels": dark_stats(np.stack([x[0] for x in loaded]), labels),
                }
            )
        short = sets[0]  # 가장 짧은 노출: 판독 노이즈 기준
        out[label] = {
            "sets": sets,
            "black_dn": [c["black_dn"] for c in short["channels"]],
            "b_dn2": float(np.mean([c["var_pair_dn2"] for c in short["channels"]])),
            "rowdet_excess": float(np.mean([c["rowdet_excess"] for c in short["channels"]])),
            "labels": [c["channel"] for c in short["channels"]],
        }
        if len(sets) > 1:
            lng = sets[-1]
            out[label]["dark_current"] = {
                "exposure_us": lng["exposure_us"],
                "black_shift_dn": float(
                    np.mean([c["black_dn"] for c in lng["channels"]])
                    - np.mean(out[label]["black_dn"])
                ),
                "var_increase_dn2": float(
                    np.mean([c["var_pair_dn2"] for c in lng["channels"]]) - out[label]["b_dn2"]
                ),
            }
    return out


def flat_points(flat_dir: Path, dark: dict[str, Any], p: PtcParams) -> list[dict[str, Any]]:
    frames = [
        f for f in json.loads((flat_dir / "ptc.json").read_text())["frames"] if f["part"] == "flat"
    ]
    pts: list[dict[str, Any]] = []
    keys = list(dict.fromkeys((f["gain_label"], f["step"], f["pair"]) for f in frames))
    for label, step, pair in keys:
        fr = sorted(
            (
                f
                for f in frames
                if f["gain_label"] == label and f["step"] == step and f["pair"] == pair
            ),
            key=lambda f: f["frame"],
        )
        if len(fr) != 2:
            continue
        (a, ma), (b, _) = (_load(flat_dir, f["file"], p.roi) for f in fr)
        labels = channel_labels(ma["bayer_order"])
        black = dark[label]["black_dn"] if label in dark else [float(ma["black_level"])] * 4
        for row in pair_point(a, b, black, float(ma["white_level"]), labels):
            row.update(
                {
                    "gain_label": label,
                    "analogue_gain": ma["analogue_gain"],
                    "step": step,
                    "pair": pair,
                    "level": fr[0].get("level"),
                    "exposure_us": ma["exposure_time_us"],
                    "white_level": ma["white_level"],
                    "black_metadata": ma["black_level"],
                }
            )
            pts.append(row)
    return pts


def select_points(
    pts: list[dict[str, Any]], dark_excess: float, full_scale: float, p: PtcParams
) -> list[dict[str, Any]]:
    """각 점에 use / exclude_reason 표시 (원본 수정).

    포화 판정은 같은 쌍의 모든 채널을 함께 본다. 초록이 포화된 단계에서는 이웃 R·B 화소의
    분산이 약 20% 떨어지고 이웃 상관이 0.2로 커진다(넘친 전하의 번짐으로 추정,
    results/p1 2026-11-21). 그래서 한 채널이라도 포화·포화 직전·깜빡임이면 그 쌍 전체를 뺀다.
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for r in pts:
        groups.setdefault((r["gain_label"], r["step"], r["pair"]), []).append(r)
    for rows in groups.values():
        any_sat = any(r["sat_fraction"] > 0 for r in rows)
        any_near = any(r["mean_dn"] > p.max_level * full_scale for r in rows)
        # 깜빡임은 프레임 쌍의 성질. 어두운 채널은 같은 깜빡임도 노이즈에 묻혀 덜 보인다.
        any_flicker = any(
            r["var_dn2"] / r["var_rowdet_dn2"] - 1.0 - dark_excess > p.flicker_flag
            for r in rows
            if r["sat_fraction"] == 0 and r["var_rowdet_dn2"] > 0
        )
        for r in rows:
            reason = None
            if r["sat_fraction"] > 0:
                reason = "saturated pixels"
            elif any_sat:
                reason = "other channel saturated"
            elif r["mean_dn"] > p.max_level * full_scale:
                reason = "near saturation"
            elif any_near:
                reason = "other channel near saturation"
            elif any_flicker:
                reason = "flicker (row-correlated excess)"
            elif r["mean_dn"] <= 0:
                reason = "no signal"
            r["use"] = reason is None
            r["exclude_reason"] = reason
    return pts


def analyze_gain(
    pts: list[dict[str, Any]], dark_g: dict[str, Any], full_scale: float, p: PtcParams
) -> dict[str, Any]:
    use = [r for r in pts if r["use"]]
    x = np.array([r["mean_dn"] for r in use])
    y = np.array([r["var_dn2"] for r in use])
    b_dark = dark_g["b_dn2"]
    res: dict[str, Any] = {
        "analogue_gain": pts[0]["analogue_gain"],
        "n_points": len(use),
        "n_excluded": {
            k: sum(1 for r in pts if r["exclude_reason"] == k)
            for k in sorted({r["exclude_reason"] for r in pts if r["exclude_reason"]})
        },
        "mean_range_dn": [float(x.min()), float(x.max())] if x.size else None,
        "b_dark_dn2": b_dark,
        "free": fit_free(x, y),
        "fixed_b": fit_fixed_b(x, y, b_dark),
        "curvature": local_slopes(x, y, b_dark, full_scale, p),
        "per_channel": {},
    }
    for ch in dark_g["labels"]:
        cx = np.array([r["mean_dn"] for r in use if r["channel"] == ch])
        cy = np.array([r["var_dn2"] for r in use if r["channel"] == ch])
        if cx.size >= 3:
            res["per_channel"][ch] = {
                "n_points": int(cx.size),
                "fixed_b": fit_fixed_b(cx, cy, b_dark),
                "free": fit_free(cx, cy),
                "max_mean_dn": float(cx.max()),
            }
    ks = [v["fixed_b"]["K"] for v in res["per_channel"].values()]
    if ks:
        res["channel_K_spread"] = float((max(ks) - min(ks)) / np.mean(ks))
    return res


def linearity(pts: list[dict[str, Any]], full_scale: float) -> dict[str, Any]:
    """평균 대 실제 노출: 원점 통과 비례 피팅의 상대 편차 (채널별, 포화 제외)."""
    out: dict[str, Any] = {}
    for ch in dict.fromkeys(r["channel"] for r in pts):
        rows = [r for r in pts if r["channel"] == ch and r["sat_fraction"] == 0 and r["pair"] == 0]
        rows = [r for r in rows if r["mean_dn"] <= 0.9 * full_scale]
        if len(rows) < 3:
            continue
        t = np.array([float(r["exposure_us"]) for r in rows])
        m = np.array([r["mean_dn"] for r in rows])
        slope = float((t * m).sum() / (t * t).sum())
        dev = m / (slope * t) - 1.0
        big = m >= 0.05 * full_scale
        out[ch] = {
            "dn_per_us": slope,
            "max_abs_dev": float(np.abs(dev).max()),
            "max_abs_dev_above_5pct": float(np.abs(dev[big]).max()) if big.any() else None,
            "exposure_us": t.tolist(),
            "deviation": dev.tolist(),
        }
    return out


def analyze(flat_dir: Path, dark_dir: Path, p: PtcParams | None = None) -> dict[str, Any]:
    p = p or PtcParams()
    dark = analyze_dark(dark_dir, p)
    pts = flat_points(flat_dir, dark, p)
    if not pts:
        raise ValueError(f"no flat pairs in {flat_dir}")
    full_scale = float(pts[0]["white_level"]) - float(np.mean(pts[0]["black_metadata"]))
    gains: dict[str, Any] = {}
    for label in dict.fromkeys(r["gain_label"] for r in pts):
        gp = [r for r in pts if r["gain_label"] == label]
        select_points(gp, dark[label]["rowdet_excess"], full_scale, p)
        g = analyze_gain(gp, dark[label], full_scale, p)
        g["linearity"] = linearity(gp, full_scale)
        g.update(derived(g, dark[label], full_scale))
        gains[label] = g
    monitor = [
        f
        for f in json.loads((flat_dir / "ptc.json").read_text())["frames"]
        if f["part"] == "monitor"
    ]
    mm = np.array([f["roi_max_channel_mean"] for f in monitor]) if monitor else np.array([])
    return {
        "params": p.__dict__,
        "flat_dir": str(flat_dir),
        "dark_dir": str(dark_dir),
        "full_scale_dn": full_scale,
        "dark": dark,
        "gains": gains,
        "monitor": {
            "n": int(mm.size),
            "range_rel": float((mm.max() - mm.min()) / mm.mean()) if mm.size else None,
            "values": mm.tolist(),
        },
        "points": pts,
    }


def derived(g: dict[str, Any], dark_g: dict[str, Any], full_scale: float) -> dict[str, Any]:
    """K(fixed_b) 기준 파생량: 판독 노이즈(e⁻), 포화 전자 수(ADC 클립), 단일 노출 DR."""
    k = g["fixed_b"]["K"]
    sigma_dn = float(np.sqrt(dark_g["b_dn2"]))
    dr = full_scale / sigma_dn
    return {
        "read_noise_dn": sigma_dn,
        "read_noise_e": sigma_dn / k,
        "conversion_gain_e_per_dn": 1.0 / k,
        "saturation_e": full_scale / k,
        "dr_ratio": dr,
        "dr_stops": float(np.log2(dr)),
        "dr_db": float(20 * np.log10(dr)),
    }
