// 브래킷 HDR 합성 (CLAUDE.md 6.1). 기준 모델: python/cctvlog/ref/bracket_merge.py
#include <algorithm>
#include <array>
#include <numeric>
#include <stdexcept>

#include "cctvlog/hdr.hpp"

namespace cctvlog::hdr {

MergeResult bracket_merge(std::span<const BracketFrame> frames, const BracketMergeParams& p) {
  p.validate();
  const std::size_t n = frames.size();
  if (n < 1 || n > 4) throw std::invalid_argument("bracket_merge: 1..4 frames");
  const int w = frames[0].raw->width();
  const int h = frames[0].raw->height();
  for (const auto& f : frames) {
    if (f.raw == nullptr || f.raw->width() != w || f.raw->height() != h || f.raw->channels() != 1) {
      throw std::invalid_argument("bracket_merge: frames must be 1-channel and the same size");
    }
    if (!(f.exposure_us > 0.0)) throw std::invalid_argument("bracket_merge: exposure must be > 0");
    if (!(f.white > f.black)) throw std::invalid_argument("bracket_merge: white must be > black");
  }

  // 노출 내림차순, 같으면 입력 순서
  std::array<std::size_t, 4> order{};
  std::iota(order.begin(), order.begin() + static_cast<std::ptrdiff_t>(n), std::size_t{0});
  std::stable_sort(order.begin(), order.begin() + static_cast<std::ptrdiff_t>(n),
                   [&](std::size_t a, std::size_t b) {
                     return frames[a].exposure_us > frames[b].exposure_us;
                   });
  const double t_long = frames[order[0]].exposure_us;
  std::array<double, 4> black{}, range{}, scale{};
  std::array<std::uint8_t, 4> idx{};
  for (std::size_t k = 0; k < n; ++k) {
    const auto& f = frames[order[k]];
    black[k] = f.black;
    range[k] = f.white - f.black;
    scale[k] = t_long / f.exposure_us;
    idx[k] = static_cast<std::uint8_t>(order[k]);
  }
  const double tau = p.tau;
  const double lo = p.band_ratio * p.tau;
  const double band = tau - lo;
  const std::size_t last = n - 1;

  MergeResult res{Image<float>(w, h, 1), Image<std::uint8_t>(w, h, 1)};
#pragma omp parallel for schedule(static)
  for (int y = 0; y < h; ++y) {
    std::array<const std::uint16_t*, 4> rows{};
    for (std::size_t k = 0; k < n; ++k) rows[k] = frames[order[k]].raw->row(y);
    float* out = res.merged.row(y);
    std::uint8_t* src = res.source.row(y);
    for (int x = 0; x < w; ++x) {
      double v = 0.0;
      std::uint8_t s = 0;
      for (std::size_t k = 0; k < n; ++k) {
        const double nk = (static_cast<double>(rows[k][x]) - black[k]) / range[k];
        if (k == last) {
          v = nk * scale[k];
          s = static_cast<std::uint8_t>(idx[k] | (nk >= tau ? kSourceSaturated : 0));
          break;
        }
        if (nk < lo) {
          v = nk * scale[k];
          s = idx[k];
          break;
        }
        if (nk < tau) {
          const double nn = (static_cast<double>(rows[k + 1][x]) - black[k + 1]) / range[k + 1];
          const double wt = (tau - nk) / band;
          v = wt * (nk * scale[k]) + (1.0 - wt) * (nn * scale[k + 1]);
          s = static_cast<std::uint8_t>(idx[k] | kSourceBlend);
          break;
        }
      }
      out[x] = static_cast<float>(v);
      src[x] = s;
    }
  }
  return res;
}

}  // namespace cctvlog::hdr
