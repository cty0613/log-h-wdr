#pragma once

// 재현 가능한 난수 (docs/decisions.md 2026-11-22 결정 3).
// 표준 라이브러리 분포는 구현마다 결과가 달라질 수 있어 직접 구현한다.
// - SplitMix64 (Steele, Lea, Flood 2014): seed 확장
// - xoshiro256** (Blackman & Vigna 2018, https://prng.di.unimi.it/xoshiro256starstar.c)
// - 정규분포: Box–Muller (쌍으로 생성, 두 값 모두 사용)
// - Poisson: λ < 10은 Knuth 곱셈법, λ ≥ 10은 PTRS (Hörmann 1993, "The transformed rejection
//   method for generating Poisson random variables"). NumPy random_poisson_ptrs와 같은 상수.

#include <cmath>
#include <cstdint>
#include <numbers>

namespace cctvlog::rng {

inline std::uint64_t splitmix64(std::uint64_t& state) {
  std::uint64_t z = (state += 0x9E3779B97F4A7C15ULL);
  z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
  z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
  return z ^ (z >> 31);
}

class Xoshiro256ss {
 public:
  explicit Xoshiro256ss(std::uint64_t seed) {
    std::uint64_t sm = seed;
    for (auto& w : s_) w = splitmix64(sm);
  }

  // (seed, stream) → 독립 스트림. 행 단위 병렬에서 행 번호를 stream으로 쓴다.
  static Xoshiro256ss stream(std::uint64_t seed, std::uint64_t stream_id) {
    std::uint64_t sm = seed ^ (0xD1B54A32D192ED03ULL * (stream_id + 1));
    return Xoshiro256ss(splitmix64(sm));
  }

  std::uint64_t next() {
    const std::uint64_t result = rotl(s_[1] * 5, 7) * 9;
    const std::uint64_t t = s_[1] << 17;
    s_[2] ^= s_[0];
    s_[3] ^= s_[1];
    s_[1] ^= s_[2];
    s_[0] ^= s_[3];
    s_[2] ^= t;
    s_[3] = rotl(s_[3], 45);
    return result;
  }

  // [0, 1), 53bit
  double uniform() { return static_cast<double>(next() >> 11) * 0x1.0p-53; }

  // (0, 1]
  double uniform_pos() { return (static_cast<double>(next() >> 11) + 1.0) * 0x1.0p-53; }

 private:
  static std::uint64_t rotl(std::uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }
  std::uint64_t s_[4]{};
};

// 표준정규 두 개 (Box–Muller)
inline void normal_pair(Xoshiro256ss& g, double& z0, double& z1) {
  const double u1 = g.uniform_pos();
  const double u2 = g.uniform();
  const double r = std::sqrt(-2.0 * std::log(u1));
  const double th = 2.0 * std::numbers::pi * u2;
  z0 = r * std::cos(th);
  z1 = r * std::sin(th);
}

inline std::int64_t poisson(Xoshiro256ss& g, double lam) {
  if (!(lam > 0.0)) return 0;
  if (lam < 10.0) {
    const double enlam = std::exp(-lam);
    std::int64_t k = 0;
    double prod = 1.0;
    while (true) {
      prod *= g.uniform();
      if (prod > enlam) {
        ++k;
      } else {
        return k;
      }
    }
  }
  const double slam = std::sqrt(lam);
  const double loglam = std::log(lam);
  const double b = 0.931 + 2.53 * slam;
  const double a = -0.059 + 0.02483 * b;
  const double invalpha = 1.1239 + 1.1328 / (b - 3.4);
  const double vr = 0.9277 - 3.6224 / (b - 2.0);
  while (true) {
    const double U = g.uniform() - 0.5;
    const double V = g.uniform();
    const double us = 0.5 - std::fabs(U);
    const double kf = std::floor((2.0 * a / us + b) * U + lam + 0.43);
    if (us >= 0.07 && V <= vr) return static_cast<std::int64_t>(kf);
    if (kf < 0.0 || (us < 0.013 && V > us)) continue;
    if (std::log(V) + std::log(invalpha) - std::log(a / (us * us) + b) <=
        -lam + kf * loglam - std::lgamma(kf + 1.0)) {
      return static_cast<std::int64_t>(kf);
    }
  }
}

}  // namespace cctvlog::rng
