"""단일 노출 노이즈 모델 (CLAUDE.md 5.4 스키마, kind=single).

PTC 결과(DN 단위 σ² = K·μ + b)를 정규화 선형 스케일 x = (v − black) / (white − black)로 옮긴다.
  σ_x² = σ²_DN / S² = (K / S)·x + b / S²,  S = white − black (= 959)
  → a = K / S, b_x = b_DN / S²
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

METHODS = ("fixed_b", "free")


def normalize(k_dn: float, b_dn2: float, full_scale_dn: float) -> tuple[float, float]:
    return k_dn / full_scale_dn, b_dn2 / full_scale_dn**2


def variance(x: float, a: float, b: float) -> float:
    return a * x + b


def single_noise_model(
    gain_result: dict[str, Any],
    full_scale_dn: float,
    method: str = "fixed_b",
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """ptc.analyze()의 게인 결과 하나 → 5.4 JSON (구간 1개, x_max = 1.0)."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}: {method}")
    fit = gain_result[method]
    a, b = normalize(fit["K"], fit["b"], full_scale_dn)
    alt = METHODS[1 - METHODS.index(method)]
    a_alt, b_alt = normalize(gain_result[alt]["K"], gain_result[alt]["b"], full_scale_dn)
    return {
        "kind": "single",
        "scale": "normalized_long_exposure",
        "x_max": 1.0,
        "segments": [{"x_start": 0.0, "a": a, "b": b}],
        "assumptions": {
            "analogue_gain": gain_result["analogue_gain"],
            "method": method,
            "full_scale_dn": full_scale_dn,
            "K_dn_per_e": fit["K"],
            "b_dn2": fit["b"],
            "r2": fit["r2"],
            "read_noise_e": gain_result.get("read_noise_e"),
            "saturation_e": gain_result.get("saturation_e"),
            "dr_stops": gain_result.get("dr_stops"),
            "curvature_K_high_over_low": gain_result.get("curvature", {}).get("high_over_low"),
            "alternative": {"method": alt, "a": a_alt, "b": b_alt},
            "channels": "B,G1,G2,R joint fit",
            "per_channel_K_dn_per_e": {
                ch: v[method]["K"] for ch, v in gain_result.get("per_channel", {}).items()
            },
            "source": source or {},
        },
    }


def write(model: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    m = json.loads(Path(path).read_text())
    for key in ("kind", "scale", "x_max", "segments"):
        if key not in m:
            raise ValueError(f"noise model missing {key}: {path}")
    return m
