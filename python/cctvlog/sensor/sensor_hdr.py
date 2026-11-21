"""센서 HDR 모드 특성 (Phase 1, 비교군용 블랙박스 측정).

`capture/capture_sensor_hdr.py` 스윕(게인 여러 개 가능) → 유효 노출 E = (게인 / 최소 게인)·t 위의
응답 곡선. 측정 항목 (CLAUDE.md Phase 1: 노출 대비 평균의 선형성과 경계 위치):
- 선형 구간: 일반 모드 감도 직선 대비 편차, 응답 압축이 시작되는 첫 E (첫 경계)
- 구간 분할: 정렬된 (E, 평균) 점의 구간별 직선 최소제곱(DP), 구간 수는 BIC로 선택
- 포화: 평균이 sat_level 이상이 되는 첫 E, 일반 모드 포화 노출 대비 확장 배수(stop)
- 게인 겹침: 서로 다른 게인의 같은 E에서 응답 차이 (E = 게인·노출 가정 검증)
- 노이즈: 쌍 차분 분산과 SNR (경계 전후 SNR 변화)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from cctvlog.sensor import ptc


@dataclass(frozen=True)
class HdrParams:
    roi: int = 256
    channel: str = "G1"  # 응답·노이즈 요약 채널
    linear_tol: float = 0.05  # 일반 모드 감도 직선 아래로 이만큼 벗어나면 압축(경계)
    sat_level: float = 0.99
    max_segments: int = 7


def load_steps(run_dir: Path, black: float, p: HdrParams) -> list[dict[str, Any]]:
    """스윕 한 번 → 단계별 (채널별 평균·분산). 쌍마다 하나씩, 단계 평균도 함께."""
    frames = [
        f for f in json.loads((run_dir / "ptc.json").read_text())["frames"] if f["part"] == "flat"
    ]
    out = []
    for step in sorted({f["step"] for f in frames}):
        pairs = []
        for pair in sorted({f["pair"] for f in frames if f["step"] == step}):
            fr = sorted(
                (f for f in frames if f["step"] == step and f["pair"] == pair),
                key=lambda f: f["frame"],
            )
            (a, ma), (b, _) = (ptc._load(run_dir, f["file"], p.roi) for f in fr)
            labels = ptc.channel_labels(ma["bayer_order"])
            pts = ptc.pair_point(a, b, [black] * 4, float(ma["white_level"]), labels)
            pairs.append({r["channel"]: r for r in pts})
        ch = p.channel
        out.append(
            {
                "step": step,
                "exposure_us": float(ma["exposure_time_us"]),
                "exposure_us_requested": ma.get("exposure_time_us_requested"),
                "analogue_gain": float(ma["analogue_gain"]),
                "mean_dn": float(np.mean([q[ch]["mean_dn"] for q in pairs])),
                "var_dn2": float(np.mean([q[ch]["var_dn2"] for q in pairs])),
                "sat_fraction": float(np.max([q[ch]["sat_fraction"] for q in pairs])),
                "channel_means_dn": {
                    c: float(np.mean([q[c]["mean_dn"] for q in pairs])) for c in pairs[0]
                },
                "max_raw": int(max(a.max(), b.max())),
                "run": run_dir.name,
            }
        )
    return out


def linear_region(e: np.ndarray, m: np.ndarray, t_sat_normal_us: float, p: HdrParams) -> dict:
    """일반 모드 감도(출력 = E / t_sat_normal)를 기준 직선으로, 편차 dev = m / (E / t_sat) − 1.
    첫 경계 = 그 점부터 끝까지 dev < −linear_tol 인 첫 점(응답 압축 시작). 그 앞이 선형 구간.
    HDR 모드는 메타데이터 노출이 거칠게 보고돼(약 71.5 µs 단위) 짧은 노출에서 dev가 수 % 흔들리므로
    "한 번 벗어나면 끝"이 아니라 "아래로 벗어난 뒤 돌아오지 않음"으로 판정한다."""
    dev = m / (e / t_sat_normal_us) - 1.0
    below = dev < -p.linear_tol
    knee = next((i for i in range(len(e)) if below[i:].all()), None)
    last = (knee - 1) if knee else len(e) - 1
    sel = slice(0, last + 1)
    s, c = np.polyfit(e[sel], m[sel], 1) if last >= 1 else (float("nan"), float("nan"))
    return {
        "reference": "normal mode sensitivity, level = E / t_sat_normal",
        "tol": p.linear_tol,
        "slope_per_us": float(s),
        "intercept": float(c),
        "slope_vs_normal": float(s * t_sat_normal_us),
        "max_abs_dev_in_linear": float(np.abs(dev[sel]).max()),
        "last_linear_E_us": float(e[last]),
        "last_linear_level": float(m[last]),
        "first_nonlinear_E_us": None if knee is None else float(e[knee]),
        "first_nonlinear_level": None if knee is None else float(m[knee]),
        "first_nonlinear_dev": None if knee is None else float(dev[knee]),
        "deviation": dev.tolist(),
    }


def snr_vs_normal(
    steps: list[dict[str, Any]], model: dict[str, Any], e_max: float
) -> dict[str, Any]:
    """선형 구간 점의 분산을, 같은 출력 레벨에서 일반 모드 노이즈 모델 분산과 비교.
    (같은 노출끼리 비교하면 HDR 모드의 거친 노출 메타데이터 오차가 섞이므로 레벨 기준.)"""
    seg = model["segments"][0]
    fs = model["assumptions"]["full_scale_dn"]
    ratios, d = [], []
    for s in steps:
        if s["E_us"] <= e_max and s["var_dn2"] > 0:
            x = s["level"]
            var_ref = (seg["a"] * x + seg["b"]) * fs**2
            ratios.append(s["var_dn2"] / var_ref)
            d.append(-10 * np.log10(s["var_dn2"] / var_ref))
    return {
        "basis": "same output level",
        "n": len(d),
        "var_ratio_median": float(np.median(ratios)) if d else None,
        "median_delta_db": float(np.median(d)) if d else None,
        "min_delta_db": float(np.min(d)) if d else None,
        "max_delta_db": float(np.max(d)) if d else None,
    }


def _seg_cost(e: np.ndarray, m: np.ndarray, w: np.ndarray) -> float:
    if len(e) < 2:
        return 0.0
    A = np.vstack([e, np.ones_like(e)]).T * np.sqrt(w)[:, None]
    coef, *_ = np.linalg.lstsq(A, m * np.sqrt(w), rcond=None)
    r = (m - (coef[0] * e + coef[1])) * np.sqrt(w)
    return float((r * r).sum())


def segment_fit(e: np.ndarray, m: np.ndarray, max_segments: int) -> dict[str, Any]:
    """정렬된 점을 연속 구간 k개로 나눠 구간별 직선(가중 1/m²) SSE 최소 (DP). k는 BIC로 선택.
    구간은 2점 이상. 반환: 선택된 k, 구간 목록(E 범위, 출력 범위, 기울기), k별 BIC."""
    n = len(e)
    w = 1.0 / np.maximum(m, 1e-6) ** 2
    cost = np.full((n, n), np.inf)
    for i in range(n):
        for j in range(i + 1, n):
            cost[i, j] = _seg_cost(e[i : j + 1], m[i : j + 1], w[i : j + 1])
    kmax = max(1, min(max_segments, n // 2))
    best = np.full((kmax + 1, n), np.inf)
    arg = np.zeros((kmax + 1, n), dtype=int)
    for j in range(1, n):
        best[1, j] = cost[0, j]
    for k in range(2, kmax + 1):
        for j in range(2 * k - 1, n):
            cand = [best[k - 1, i - 1] + cost[i, j] for i in range(2 * (k - 1), j)]
            if cand:
                i = int(np.argmin(cand))
                best[k, j] = cand[i]
                arg[k, j] = i + 2 * (k - 1)
    bic = {}
    for k in range(1, kmax + 1):
        sse = best[k, n - 1]
        if np.isfinite(sse):
            bic[k] = float(n * np.log(max(sse, 1e-300) / n) + (3 * k - 1) * np.log(n))
    k_best = min(bic, key=bic.get)
    bounds = []
    j, k = n - 1, k_best
    while k > 1:
        i = int(arg[k, j])
        bounds.append((i, j))
        j, k = i - 1, k - 1
    bounds.append((0, j))
    bounds.reverse()
    segs = []
    for i, j in bounds:
        s, c = np.polyfit(e[i : j + 1], m[i : j + 1], 1, w=np.sqrt(w[i : j + 1]))
        segs.append(
            {
                "E_us": [float(e[i]), float(e[j])],
                "level": [float(m[i]), float(m[j])],
                "slope_per_us": float(s),
                "intercept": float(c),
                "n": j - i + 1,
            }
        )
    return {"k": k_best, "segments": segs, "bic": bic}


def gain_overlap(steps: list[dict[str, Any]], e_key: str = "E_us") -> dict[str, Any]:
    """게인이 다른 스윕끼리, 겹치는 E 범위에서 로그 보간 응답의 최대 상대 차이."""
    gains = sorted({round(s["analogue_gain"], 3) for s in steps})
    if len(gains) < 2:
        return {}
    lo = [s for s in steps if round(s["analogue_gain"], 3) == gains[0]]
    out = {}
    for g in gains[1:]:
        hi = [s for s in steps if round(s["analogue_gain"], 3) == g]
        le = np.log([s[e_key] for s in lo])
        lm = np.array([s["level"] for s in lo])
        sel = [s for s in hi if le.min() <= np.log(s[e_key]) <= le.max()]
        if not sel:
            continue
        ref = np.interp([np.log(s[e_key]) for s in sel], le, lm)
        diff = np.array([s["level"] for s in sel]) / ref - 1.0
        out[f"{gains[0]}_vs_{g}"] = {
            "n": len(sel),
            "E_us": [float(sel[0][e_key]), float(sel[-1][e_key])],
            "max_abs_rel_diff": float(np.abs(diff).max()),
            "median_abs_rel_diff": float(np.median(np.abs(diff))),
        }
    return out


def add_input_referred_snr(
    steps: list[dict[str, Any]], segs: list[dict[str, Any]], t_sat_normal_us: float, fs: float
) -> None:
    """입력(장면) 기준 SNR. 출력 노이즈를 그 지점 응답 기울기(구간 직선)로 나눠 입력 단위로
    되돌린다: 신호 x = E / t_sat, 노이즈 σ_x = σ_level / (slope·t_sat) → SNR = E·slope / σ_level.
    압축 구간에서는 출력 SNR(평균/σ)이 장면 SNR보다 훨씬 크게 보이므로 비교에는 이 값을 쓴다."""
    for s in steps:
        seg = next((g for g in segs if g["E_us"][0] <= s["E_us"] <= g["E_us"][1]), None)
        if seg is None:
            seg = min(
                segs,
                key=lambda g: min(
                    abs(np.log(s["E_us"] / g["E_us"][0])), abs(np.log(s["E_us"] / g["E_us"][1]))
                ),
            )
        sig = np.sqrt(s["var_dn2"]) / fs
        s["slope_rel"] = seg["slope_per_us"] * t_sat_normal_us
        s["snr_in_db"] = (
            float(20 * np.log10(s["E_us"] * seg["slope_per_us"] / sig))
            if sig > 0 and seg["slope_per_us"] > 0
            else None
        )


def analyze(
    runs: list[Path],
    black: float,
    full_scale: float,
    t_sat_normal_us: float,
    p: HdrParams,
    normal_noise_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for r in runs:
        steps += load_steps(r, black, p)
    g0 = min(s["analogue_gain"] for s in steps)
    for s in steps:
        s["E_us"] = s["exposure_us"] * s["analogue_gain"] / g0
        s["level"] = s["mean_dn"] / full_scale
        s["snr_db"] = (
            float(20 * np.log10(s["mean_dn"] / np.sqrt(s["var_dn2"])))
            if s["var_dn2"] > 0 and s["mean_dn"] > 0
            else None
        )
    steps.sort(key=lambda s: (s["E_us"], s["analogue_gain"]))
    unsat = [s for s in steps if s["level"] < p.sat_level and s["sat_fraction"] == 0]
    e = np.array([s["E_us"] for s in unsat])
    m = np.array([s["level"] for s in unsat])
    lin = linear_region(e, m, t_sat_normal_us, p)
    sat = next((s for s in steps if s["level"] >= p.sat_level), None)
    res: dict[str, Any] = {
        "params": p.__dict__,
        "runs": [str(r) for r in runs],
        "black_dn": black,
        "full_scale_dn": full_scale,
        "reference_gain": g0,
        "linear": lin,
        "normal_mode_t_sat_us": t_sat_normal_us,
        "segments": (segs := segment_fit(e, m, p.max_segments)),
        "gain_overlap": gain_overlap(steps),
        "max_raw_code": max(s["max_raw"] for s in steps),
        "steps": steps,
    }
    if sat:
        res["saturation"] = {
            "E_us": sat["E_us"],
            "level": sat["level"],
            "extension_ratio": sat["E_us"] / t_sat_normal_us,
            "extension_stops": float(np.log2(sat["E_us"] / t_sat_normal_us)),
        }
        prev = [s for s in steps if s["E_us"] < sat["E_us"]]
        if prev:
            res["saturation"]["last_below_E_us"] = prev[-1]["E_us"]
            res["saturation"]["last_below_level"] = prev[-1]["level"]
    add_input_referred_snr(unsat, segs["segments"], t_sat_normal_us, full_scale)
    if normal_noise_model is not None:
        res["snr_vs_normal_linear"] = snr_vs_normal(
            unsat, normal_noise_model, lin["last_linear_E_us"]
        )
    # 첫 경계 전후 SNR
    nl = lin["first_nonlinear_E_us"]
    if nl is not None:
        before = next(s for s in unsat if s["E_us"] == lin["last_linear_E_us"])
        after = next(s for s in unsat if s["E_us"] == nl)
        res["boundary_snr"] = {
            "before": {
                "E_us": before["E_us"],
                "level": before["level"],
                "snr_db": before["snr_db"],
            },
            "after": {"E_us": after["E_us"], "level": after["level"], "snr_db": after["snr_db"]},
            "delta_db": after["snr_db"] - before["snr_db"],
        }
    return res
