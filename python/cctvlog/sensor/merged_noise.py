"""합성 후 노이즈 분포 (CLAUDE.md 6.3, 5.4 스키마). Phase 2.

해석 모델
- 브래킷: 단일 노출 (a, b)와 노출비 R_i = t_long / t_i (오름차순, R_0 = 1).
  프레임 i는 앞 프레임이 포화 임계 τ에 닿는 x = τ·R_{i−1}부터 쓰인다.
  그 프레임의 값에 R_i를 곱하므로
  σ²(x) = a·R_i·x + b·R_i² (5.4 구간 i). x_max = R_last (가장 짧은 노출이 1.0에서 클립).
- DCG: 같은 전하를 HCG·LCG로 두 번 읽는다. 샷 노이즈를 공유하므로 기울기 a = 1/S는 두 구간이 같고
  판독·양자화 노이즈 b만 바뀐다. S = 장면 스케일(e⁻ / 선형 1.0).

경계 띠 [band_ratio·τ, τ] (docs/decisions.md 2026-11-22 결정 2)
- 5.4 JSON은 τ에서 점프하는 모델이다 (띠 무시).
- 띠 안의 정확한 분산은 `band_variance` / `dcg_band_variance`로 따로 계산해 실측 검증에 쓴다.
  브래킷은 독립 프레임이라 w²σ_L² + (1−w)²σ_S², DCG는 샷 노이즈 공유라 shot + w²r_H² + (1−w)²r_L².
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

TAU = 0.9
BAND_RATIO = 0.8


# ---------------------------------------------------------------- 5.4 JSON 공통


def segment_index(model: dict[str, Any], x: np.ndarray | float) -> np.ndarray:
    starts = np.array([s["x_start"] for s in model["segments"]])
    i = np.searchsorted(starts, np.asarray(x, dtype=np.float64), side="right") - 1
    return np.clip(i, 0, None)


def model_variance(model: dict[str, Any], x: np.ndarray | float) -> np.ndarray:
    """5.4 JSON의 σ²(x) = a_i·x + b_i (점프 모델)."""
    x = np.asarray(x, dtype=np.float64)
    i = segment_index(model, x)
    a = np.array([s["a"] for s in model["segments"]])[i]
    b = np.array([s["b"] for s in model["segments"]])[i]
    return a * x + b


def snr_db(x: np.ndarray | float, var: np.ndarray | float) -> np.ndarray:
    return 20.0 * np.log10(np.asarray(x) / np.sqrt(np.asarray(var)))


def noise_integral(model: dict[str, Any], x_end: float | None = None) -> float:
    """N = ∫₀^{x_end} dt/σ(t) 닫힌 형태 (CLAUDE.md 6.4). x_end 기본값은 x_max."""
    x_end = float(model["x_max"]) if x_end is None else float(x_end)
    segs = model["segments"]
    total = 0.0
    for i, s in enumerate(segs):
        x0 = float(s["x_start"])
        x1 = float(segs[i + 1]["x_start"]) if i + 1 < len(segs) else x_end
        x1 = min(x1, x_end)
        if x1 <= x0:
            continue
        a, b = float(s["a"]), float(s["b"])
        if a > 0:
            total += (2.0 / a) * (math.sqrt(a * x1 + b) - math.sqrt(a * x0 + b))
        else:
            total += (x1 - x0) / math.sqrt(b)
    return total


def required_bits(model: dict[str, Any]) -> int:
    return math.ceil(math.log2(noise_integral(model)))


def dr_stops(model: dict[str, Any]) -> float:
    """DR = x_max / σ(0) = x_max / √b_0 (판독 노이즈 바닥 대비 포화)."""
    return math.log2(float(model["x_max"]) / math.sqrt(float(model["segments"][0]["b"])))


def boundary_dips(model: dict[str, Any]) -> list[dict[str, float]]:
    """각 경계 x_i에서 앞 구간과 뒤 구간의 SNR 차이 (dB, 양수 = 뒤에서 SNR이 낮아짐)."""
    segs = model["segments"]
    out = []
    for prev, cur in itertools.pairwise(segs):
        x = float(cur["x_start"])
        v0 = prev["a"] * x + prev["b"]
        v1 = cur["a"] * x + cur["b"]
        out.append(
            {
                "x": x,
                "snr_before_db": float(snr_db(x, v0)),
                "snr_after_db": float(snr_db(x, v1)),
                "dip_db": float(10.0 * math.log10(v1 / v0)),
            }
        )
    return out


def summary(model: dict[str, Any]) -> dict[str, Any]:
    n = noise_integral(model)
    return {
        "x_max": model["x_max"],
        "n_segments": len(model["segments"]),
        "dr_stops": dr_stops(model),
        "N": n,
        "required_bits": math.ceil(math.log2(n)),
        "R_at_bits": {str(bits): n / (2**bits - 1) for bits in (8, 10, 12)},
        "dips": boundary_dips(model),
    }


# ---------------------------------------------------------------- 브래킷


def _single_ab(single: dict[str, Any]) -> tuple[float, float]:
    if single.get("kind") != "single" or len(single["segments"]) != 1:
        raise ValueError("single noise model must have kind=single and one segment")
    s = single["segments"][0]
    return float(s["a"]), float(s["b"])


def _check_ratios(ratios: Sequence[float]) -> list[float]:
    r = [float(v) for v in ratios]
    if not r or r[0] != 1.0 or any(b <= a for a, b in itertools.pairwise(r)):
        raise ValueError(f"ratios must start at 1 and strictly increase: {ratios}")
    return r


def bracket_model(
    single: dict[str, Any],
    ratios: Sequence[float],
    tau: float = TAU,
    band_ratio: float = BAND_RATIO,
    assumptions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """단일 노출 모델 + 노출비 → 5.4 JSON (kind=bracket, 점프 모델)."""
    a, b = _single_ab(single)
    r = _check_ratios(ratios)
    x_sat = float(single.get("x_max", 1.0))
    segs = []
    for i, ri in enumerate(r):
        segs.append({"x_start": 0.0 if i == 0 else tau * r[i - 1], "a": a * ri, "b": b * ri * ri})
    return {
        "kind": "bracket",
        "scale": "normalized_long_exposure",
        "x_max": x_sat * r[-1],
        "segments": segs,
        "assumptions": {
            "exposure_ratios": r,
            "tau": tau,
            "band_ratio": band_ratio,
            "band_model": "jump_at_tau (band [band_ratio*tau, tau] ignored; "
            "exact: merged_noise.band_variance)",
            "single": {"a": a, "b": b, "analogue_gain": single["assumptions"].get("analogue_gain")},
            **(assumptions or {}),
        },
    }


def band_variance(
    x: np.ndarray | float,
    a: float,
    b: float,
    ratios: Sequence[float],
    tau: float = TAU,
    band_ratio: float = BAND_RATIO,
) -> np.ndarray:
    """브래킷 합성 결과의 정확한 분산 (경계 띠 포함).

    합성 로직은 CLAUDE.md 6.1 / ref.bracket_merge와 같다.

    가중치 w는 긴 프레임의 (참)값으로 계산한다. w가 노이즈로 흔들리는 2차 항은 1% 미만이라 무시한다.
    """
    r = _check_ratios(ratios)
    x = np.asarray(x, dtype=np.float64)
    var = a * r[-1] * x + b * r[-1] ** 2
    lo = band_ratio * tau
    for k in range(len(r) - 2, -1, -1):
        n = x / r[k]  # 프레임 k의 정규화 값
        vk = a * r[k] * x + b * r[k] ** 2
        vn = a * r[k + 1] * x + b * r[k + 1] ** 2
        w = np.clip((tau - n) / (tau - lo), 0.0, 1.0)
        var = np.where(n < lo, vk, np.where(n < tau, w * w * vk + (1 - w) ** 2 * vn, var))
    return var


# ---------------------------------------------------------------- DCG


def dcg_derived(p: dict[str, Any]) -> dict[str, Any]:
    """configs/dcg_assumptions.json → HCG 값을 채운 완전한 가정값 (C++ DcgParams와 같은 규칙).

    hcg_full_well_e, hcg_read_noise_e가 없거나 null이면 LCG ÷ conversion_gain_ratio.
    """
    g = float(p["conversion_gain_ratio"])
    out = dict(p)
    if out.get("hcg_full_well_e") is None:
        out["hcg_full_well_e"] = float(p["lcg_full_well_e"]) / g
    if out.get("hcg_read_noise_e") is None:
        out["hcg_read_noise_e"] = float(p["lcg_read_noise_e"]) / g
    fs = 2 ** int(p["adc_bits"]) - 1 - float(p["black_level_dn"])
    out["adc_full_scale_dn"] = fs
    out["hcg_e_per_dn"] = out["hcg_full_well_e"] / fs
    out["lcg_e_per_dn"] = float(p["lcg_full_well_e"]) / fs
    return out


def _dcg_terms(p: dict[str, Any]) -> dict[str, float]:
    d = dcg_derived(p)
    s = float(d["scene_scale_e_per_unit"])
    return {
        "S": s,
        "x_hcg_sat": d["hcg_full_well_e"] / s,
        "x_lcg_sat": float(d["lcg_full_well_e"]) / s,
        # 판독 + 양자화(q²/12), 선형 단위²
        "b_h": (d["hcg_read_noise_e"] ** 2 + d["hcg_e_per_dn"] ** 2 / 12.0) / s**2,
        "b_l": (float(d["lcg_read_noise_e"]) ** 2 + d["lcg_e_per_dn"] ** 2 / 12.0) / s**2,
        "tau": float(d.get("tau", TAU)),
        "band_ratio": float(d.get("band_ratio", BAND_RATIO)),
    }


def dcg_model(p: dict[str, Any]) -> dict[str, Any]:
    """DCG 가정값 → 5.4 JSON (kind=dcg, 점프 모델). C++ hdr::dcg_noise_model과 같아야 한다."""
    t = _dcg_terms(p)
    a = 1.0 / t["S"]
    return {
        "kind": "dcg",
        "scale": "normalized_long_exposure",
        "x_max": t["x_lcg_sat"],
        "segments": [
            {"x_start": 0.0, "a": a, "b": t["b_h"]},
            {"x_start": t["tau"] * t["x_hcg_sat"], "a": a, "b": t["b_l"]},
        ],
        "assumptions": {
            **dcg_derived(p),
            "band_model": "jump_at_tau (exact: merged_noise.dcg_band_variance)",
            "shot_noise": "shared between HCG and LCG readouts",
        },
    }


def dcg_band_variance(x: np.ndarray | float, p: dict[str, Any]) -> np.ndarray:
    """DCG 합성 결과의 정확한 분산 (띠 포함). 샷 노이즈는 두 판독이 공유하므로 가중치와 무관."""
    t = _dcg_terms(p)
    x = np.asarray(x, dtype=np.float64)
    shot = x / t["S"]
    n = x / t["x_hcg_sat"]
    lo = t["band_ratio"] * t["tau"]
    w = np.clip((t["tau"] - n) / (t["tau"] - lo), 0.0, 1.0)
    return shot + w * w * t["b_h"] + (1 - w) ** 2 * t["b_l"]
