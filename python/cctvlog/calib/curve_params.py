"""곡선 파라미터 계산 (CLAUDE.md 6.4, Phase 3). 파라미터 계산은 Python, 적용은 C++.

H-Log (구간별 노이즈 매칭):
  C(x) = k·∫₀ˣ dt/σ(t),  k = 1/N,  N = ∫₀^{x_max} dt/σ(t)   (C(x_max) = 1)
  구간 i (σ² = a_i·t + b_i):
    ∫_{x_i}^{x} dt/√(a_i t + b_i) = 2(x − x_i) / (√(a_i x + b_i) + √(a_i x_i + b_i))
  (2/a)(√· − √·)와 같은 값이지만 a → 0에서도 안정하다. a = 0이면 (x − x_i)/√b.
  각 구간 시작의 C 값 c_start를 미리 계산해 C++에 넘긴다.

비교 곡선(Rec.709, HLG, S-Log3)은 종류와 x_max(입력 스케일)만 갖는다.
(decisions.md 2026-11-22 결정 1)
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from pathlib import Path
from typing import Any

from cctvlog.sensor import merged_noise as mn

COMPARISON_TYPES = ("rec709", "hlg", "slog3")


def segment_integral(a: float, b: float, x0: float, x1: float) -> float:
    """∫_{x0}^{x1} dt / √(a t + b), 수치적으로 안정한 형태."""
    return 2.0 * (x1 - x0) / (math.sqrt(a * x1 + b) + math.sqrt(a * x0 + b))


def hlog_params(noise: dict[str, Any], name: str, source: dict[str, Any] | None = None) -> dict:
    """5.4 노이즈 JSON → H-Log 곡선 JSON."""
    segs = noise["segments"]
    x_max = float(noise["x_max"])
    if segs[0]["x_start"] != 0.0:
        raise ValueError("first segment must start at 0")
    starts = [float(s["x_start"]) for s in segs] + [x_max]
    if any(b <= a for a, b in itertools.pairwise(starts)):
        raise ValueError("segment starts must increase and stay below x_max")
    partial = [0.0]
    for i, s in enumerate(segs):
        partial.append(partial[-1] + segment_integral(s["a"], s["b"], starts[i], starts[i + 1]))
    n = partial[-1]
    k = 1.0 / n
    out_segs = [
        {"x_start": starts[i], "a": float(s["a"]), "b": float(s["b"]), "c_start": partial[i] * k}
        for i, s in enumerate(segs)
    ]
    return {
        "type": "hlog",
        "name": name,
        "x_max": x_max,
        "N": n,
        "k": k,
        "required_bits": math.ceil(math.log2(n)),
        "R_const": {str(bits): n / (2**bits - 1) for bits in (8, 10, 12)},
        "segments": out_segs,
        "noise_kind": noise.get("kind"),
        "source": source or {},
    }


def comparison_params(curve_type: str, x_max: float, name: str | None = None) -> dict[str, Any]:
    if curve_type not in COMPARISON_TYPES:
        raise ValueError(f"unknown comparison curve {curve_type}")
    return {
        "type": curve_type,
        "name": name or curve_type,
        "x_max": float(x_max),
        "input_scale_rule": "x_max -> domain upper bound (decisions.md 2026-11-22 결정 1)",
    }


def single_extended(single: dict[str, Any], x_max: float) -> dict[str, Any]:
    """단일 노출 모델을 x_max까지 늘린 노이즈 모델 (H-Log 단일, 결정 2)."""
    s = single["segments"][0]
    return {
        "kind": "single_extended",
        "scale": "normalized_long_exposure",
        "x_max": float(x_max),
        "segments": [{"x_start": 0.0, "a": s["a"], "b": s["b"]}],
        "assumptions": {"note": "single-exposure noise model extended to x_max (ignores merge)"},
    }


def params_hash(curve: dict[str, Any]) -> str:
    """곡선을 정하는 값만으로 만든 해시 (사이드카 curve_params_hash, CLAUDE.md 5.5)."""
    keys = ("type", "x_max", "k", "segments")
    body = json.dumps({k: curve.get(k) for k in keys}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def write(curve: dict[str, Any], path: Path) -> None:
    curve = dict(curve)
    curve["params_hash"] = params_hash(curve)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(curve, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def noise_N(noise: dict[str, Any]) -> float:
    return mn.noise_integral(noise)
