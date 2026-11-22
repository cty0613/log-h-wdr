// DCG 시뮬레이터 (CLAUDE.md 6.2, docs/decisions.md 2026-11-22 결정 3).
#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "cctvlog/hdr.hpp"
#include "cctvlog/random.hpp"

namespace cctvlog::hdr {

namespace {

struct Readout {
  double fw;       // full well (e⁻)
  double rn;       // 판독 노이즈 (e⁻)
  double e_per_dn;
};

// 전자 수(샷 노이즈 포함) + 정규 난수 z → 정규화 값 (1.0 = full well)
double read_once(double electrons, double z, const Readout& r, double black, double code_max,
                 double fs) {
  double v = electrons + r.rn * z;
  v = std::min(v, r.fw);  // full well 클립
  // ADC: round-half-up, 블랙 오프셋, [0, 2^bits − 1] 포화 (docs/fixed_point.md 반올림 규칙)
  double code = std::floor(v / r.e_per_dn + black + 0.5);
  code = std::clamp(code, 0.0, code_max);
  return (code - black) / fs;
}

}  // namespace

double dcg_merge_pixel(const DcgReadout& r, const DcgParams& p, std::uint8_t& source) {
  const double lo = p.band_ratio * p.tau;
  if (r.hcg_norm < lo) {
    source = 0;
    return r.hcg_x;
  }
  if (r.hcg_norm < p.tau) {
    const double w = (p.tau - r.hcg_norm) / (p.tau - lo);
    source = kSourceBlend;
    return w * r.hcg_x + (1.0 - w) * r.lcg_x;
  }
  source = static_cast<std::uint8_t>(1 | (r.lcg_norm >= p.tau ? kSourceSaturated : 0));
  return r.lcg_x;
}

DcgResult dcg_simulate(const Image<float>& scene, const DcgParams& p) {
  p.validate();
  if (scene.channels() != 1) throw std::invalid_argument("dcg_simulate: 1-channel scene expected");
  const int w = scene.width();
  const int h = scene.height();
  const double fs = p.adc_full_scale_dn();
  const double code_max = static_cast<double>((std::uint64_t{1} << p.adc_bits) - 1);
  const Readout hcg{p.hcg_fw(), p.hcg_rn(), p.hcg_fw() / fs};
  const Readout lcg{p.lcg_full_well_e, p.lcg_read_noise_e, p.lcg_full_well_e / fs};
  const double S = p.scene_scale_e_per_unit;

  DcgResult res{Image<float>(w, h, 1), Image<std::uint8_t>(w, h, 1)};
#pragma omp parallel for schedule(static)
  for (int y = 0; y < h; ++y) {
    auto g = rng::Xoshiro256ss::stream(p.seed, static_cast<std::uint64_t>(y));
    const float* in = scene.row(y);
    float* out = res.merged.row(y);
    std::uint8_t* src = res.source.row(y);
    for (int x = 0; x < w; ++x) {
      const double lam = std::max(static_cast<double>(in[x]), 0.0) * S;
      // 샷 노이즈 1회 — 두 판독이 공유
      const double e = static_cast<double>(rng::poisson(g, lam));
      double zh = 0.0, zl = 0.0;
      rng::normal_pair(g, zh, zl);
      DcgReadout r{};
      r.hcg_norm = read_once(e, zh, hcg, p.black_level_dn, code_max, fs);
      r.lcg_norm = read_once(e, zl, lcg, p.black_level_dn, code_max, fs);
      r.hcg_x = r.hcg_norm * hcg.fw / S;
      r.lcg_x = r.lcg_norm * lcg.fw / S;
      std::uint8_t s = 0;
      out[x] = static_cast<float>(dcg_merge_pixel(r, p, s));
      src[x] = s;
    }
  }
  return res;
}

nlohmann::json dcg_noise_model(const DcgParams& p) {
  p.validate();
  const double S = p.scene_scale_e_per_unit;
  const double fs = p.adc_full_scale_dn();
  const double qh = p.hcg_fw() / fs;
  const double ql = p.lcg_full_well_e / fs;
  const double a = 1.0 / S;
  const double bh = (p.hcg_rn() * p.hcg_rn() + qh * qh / 12.0) / (S * S);
  const double bl = (p.lcg_read_noise_e * p.lcg_read_noise_e + ql * ql / 12.0) / (S * S);
  nlohmann::json assumptions = p;
  assumptions["adc_full_scale_dn"] = fs;
  assumptions["hcg_e_per_dn"] = qh;
  assumptions["lcg_e_per_dn"] = ql;
  assumptions["band_model"] = "jump_at_tau (exact: merged_noise.dcg_band_variance)";
  assumptions["shot_noise"] = "shared between HCG and LCG readouts";
  return {{"kind", "dcg"},
          {"scale", "normalized_long_exposure"},
          {"x_max", p.lcg_full_well_e / S},
          {"segments",
           nlohmann::json::array({{{"x_start", 0.0}, {"a", a}, {"b", bh}},
                                  {{"x_start", p.tau * p.hcg_fw() / S}, {"a", a}, {"b", bl}}})},
          {"assumptions", assumptions}};
}

}  // namespace cctvlog::hdr
