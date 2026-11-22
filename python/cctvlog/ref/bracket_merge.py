"""브래킷 HDR 합성 기준 모델 (검증 전용, float64).

C++ hdr::bracket_merge와 같은 로직 (CLAUDE.md 6.1).

실험 경로에서 쓰지 않는다 (CLAUDE.md 1절 경계 규칙).

화소마다 노출이 긴 프레임부터 본다. 프레임 k의 정규화 값 n_k = (v − black) / (white − black),
스케일 s_k = t_long / t_k.
- n_k < band_ratio·τ           → n_k·s_k                                  (출처 = k)
- band_ratio·τ ≤ n_k < τ       → w·n_k·s_k + (1−w)·n_{k+1}·s_{k+1},
                                 w = (τ − n_k) / (τ − band_ratio·τ)       (출처 = k | BLEND)
- n_k ≥ τ                      → 다음 프레임
- 마지막 프레임은 값과 무관하게 n·s를 쓰고, n ≥ τ면 SATURATED 비트.
출처 값의 하위 4bit는 **입력 순서**(넘겨받은 프레임 목록 안의 위치) 번호,
0x40 = 다음(더 짧은) 프레임과 섞임, 0x80 = 포화.
음수(블랙 아래 노이즈)는 자르지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

SOURCE_INDEX_MASK = 0x0F
SOURCE_BLEND = 0x40
SOURCE_SATURATED = 0x80


def bracket_merge(
    raws: Sequence[np.ndarray],
    exposures_us: Sequence[float],
    blacks: Sequence[float],
    whites: Sequence[float],
    tau: float = 0.9,
    band_ratio: float = 0.8,
) -> tuple[np.ndarray, np.ndarray]:
    n_frames = len(raws)
    if not 1 <= n_frames <= 4:
        raise ValueError("1..4 frames")
    if not (len(exposures_us) == len(blacks) == len(whites) == n_frames):
        raise ValueError("length mismatch")
    if not (0.0 < band_ratio < 1.0 and 0.0 < tau):
        raise ValueError("invalid tau/band_ratio")
    # 노출 내림차순 (같으면 입력 순서) — C++의 std::stable_sort와 같다
    order = sorted(range(n_frames), key=lambda i: -float(exposures_us[i]))
    t_long = float(exposures_us[order[0]])
    lo = band_ratio * tau
    norm = []
    scale = []
    for i in order:
        r = raws[i].astype(np.float64)
        norm.append((r - float(blacks[i])) / (float(whites[i]) - float(blacks[i])))
        scale.append(t_long / float(exposures_us[i]))

    last = n_frames - 1
    out = norm[last] * scale[last]
    src = np.full(out.shape, order[last], dtype=np.uint8)
    src[norm[last] >= tau] |= SOURCE_SATURATED
    for k in range(n_frames - 2, -1, -1):
        n = norm[k]
        own = n * scale[k]
        nxt = norm[k + 1] * scale[k + 1]
        w = (tau - n) / (tau - lo)
        blend = w * own + (1.0 - w) * nxt
        m_own = n < lo
        m_band = (n >= lo) & (n < tau)
        out = np.where(m_own, own, np.where(m_band, blend, out))
        src = np.where(m_own, order[k], np.where(m_band, order[k] | SOURCE_BLEND, src)).astype(
            np.uint8
        )
    return out, src
