#pragma once

// 고정소수점 반올림·포화 규칙 (CLAUDE.md 5.3, docs/fixed_point.md와 항상 일치).
// - 반올림: round-half-up = floor(v + 0.5). 음수도 같은 식(−0.5 → 0, −1.5 → −1).
// - 포화: [lo, hi]로 자른다. NaN은 lo.

#include <cmath>
#include <cstdint>
#include <limits>

namespace cctvlog::fixed {

inline std::int64_t round_half_up(double v) { return static_cast<std::int64_t>(std::floor(v + 0.5)); }

inline std::int64_t saturate(std::int64_t v, std::int64_t lo, std::int64_t hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// 실수 → [lo, hi] 정수 (반올림 후 포화)
inline std::int64_t quantize(double v, std::int64_t lo, std::int64_t hi) {
  if (std::isnan(v)) return lo;
  if (v <= static_cast<double>(lo)) return lo;
  if (v >= static_cast<double>(hi)) return hi;
  return saturate(round_half_up(v), lo, hi);
}

// 정규화 신호 V ∈ [0, 1] → bits 코드
inline std::int64_t to_code(double v, int bits) {
  const std::int64_t cmax = (std::int64_t{1} << bits) - 1;
  return quantize(v * static_cast<double>(cmax), 0, cmax);
}

}  // namespace cctvlog::fixed
