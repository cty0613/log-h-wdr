// hdr::dcg_simulate: 몬테카를로 평균·분산 vs 해석 모델 (CLAUDE.md 1.1: 1e5 샘플, 분산 ±3%).
#include <cmath>
#include <cstdint>
#include <vector>

#include <gtest/gtest.h>
#ifdef _OPENMP
#include <omp.h>
#endif

#include "cctvlog/hdr.hpp"
#include "cctvlog/params.hpp"

using cctvlog::DcgParams;
using cctvlog::Image;

namespace {

constexpr int kW = 400, kH = 250;  // 1e5 샘플

// 해석 분산: 샷(공유) + 가중 판독·양자화 (띠 밖에서는 5.4 점프 모델과 같다)
double analytic_var(double x, const DcgParams& p) {
  const double S = p.scene_scale_e_per_unit;
  const double fs = p.adc_full_scale_dn();
  const double qh = p.hcg_fw() / fs, ql = p.lcg_full_well_e / fs;
  const double bh = (p.hcg_rn() * p.hcg_rn() + qh * qh / 12.0) / (S * S);
  const double bl = (p.lcg_read_noise_e * p.lcg_read_noise_e + ql * ql / 12.0) / (S * S);
  const double n = x * S / p.hcg_fw();
  const double lo = p.band_ratio * p.tau;
  const double w = std::clamp((p.tau - n) / (p.tau - lo), 0.0, 1.0);
  return x / S + w * w * bh + (1 - w) * (1 - w) * bl;
}

struct Stats {
  double mean, var;
};

Stats simulate(double x, const DcgParams& p) {
  Image<float> scene(kW, kH, 1);
  for (int y = 0; y < kH; ++y)
    for (int i = 0; i < kW; ++i) scene.at(i, y) = static_cast<float>(x);
  const auto r = cctvlog::hdr::dcg_simulate(scene, p);
  double s = 0, s2 = 0;
  const double n = static_cast<double>(kW) * kH;
  for (int y = 0; y < kH; ++y)
    for (int i = 0; i < kW; ++i) s += r.merged.at(i, y);
  const double m = s / n;
  for (int y = 0; y < kH; ++y)
    for (int i = 0; i < kW; ++i) s2 += (r.merged.at(i, y) - m) * (r.merged.at(i, y) - m);
  return {m, s2 / (n - 1)};
}

}  // namespace

class DcgMonteCarlo : public ::testing::TestWithParam<std::tuple<double, double>> {};

TEST_P(DcgMonteCarlo, VarianceMatchesAnalyticWithin3Percent) {
  const auto [ratio, x] = GetParam();
  DcgParams p;
  p.conversion_gain_ratio = ratio;  // HCG 값은 이상적 규칙으로 파생
  p.seed = 1000 + static_cast<std::uint64_t>(x * 1000) + static_cast<std::uint64_t>(ratio);
  const auto st = simulate(x, p);
  const double v = analytic_var(x, p);
  EXPECT_NEAR(st.var / v, 1.0, 0.03) << "g " << ratio << " x " << x;
  EXPECT_NEAR(st.mean, x, 5.0 * std::sqrt(v / (kW * kH)) + 1e-4 * x) << "g " << ratio << " x " << x;
}

// g=4: HCG 포화 x=4, 경계 3.6, 띠 [2.88, 3.6). 3.24 = 띠 중간(w = 0.5) — 샷 노이즈 공유를 검증한다
// (독립 샷이면 분산이 약 50% 작게 나온다).
INSTANTIATE_TEST_SUITE_P(
    Levels, DcgMonteCarlo,
    ::testing::Values(std::tuple{4.0, 0.005}, std::tuple{4.0, 0.05}, std::tuple{4.0, 0.5},
                      std::tuple{4.0, 2.5}, std::tuple{4.0, 3.0}, std::tuple{4.0, 3.24},
                      std::tuple{4.0, 3.5}, std::tuple{4.0, 5.0}, std::tuple{4.0, 10.0},
                      std::tuple{4.0, 14.0}, std::tuple{2.0, 0.05}, std::tuple{2.0, 7.2},
                      std::tuple{2.0, 12.0}, std::tuple{8.0, 0.05}, std::tuple{8.0, 1.62},
                      std::tuple{8.0, 6.0}));

TEST(Dcg, NoiseModelJson) {
  const DcgParams p;  // 기본값 = configs/dcg_assumptions.json
  const auto j = cctvlog::hdr::dcg_noise_model(p);
  EXPECT_EQ(j["kind"], "dcg");
  EXPECT_NEAR(j["x_max"].get<double>(), 16.0, 1e-12);
  ASSERT_EQ(j["segments"].size(), 2u);
  EXPECT_NEAR(j["segments"][1]["x_start"].get<double>(), 0.9 * 4.0, 1e-12);
  const double S = 17666.0 / 16.0;
  EXPECT_NEAR(j["segments"][0]["a"].get<double>(), 1.0 / S, 1e-15);
  const double qh = 17666.0 / 4.0 / 959.0;
  EXPECT_NEAR(j["segments"][0]["b"].get<double>(),
              ((9.96 / 4) * (9.96 / 4) + qh * qh / 12.0) / (S * S), 1e-15);
  EXPECT_DOUBLE_EQ(j["assumptions"]["hcg_read_noise_e"].get<double>(), 9.96 / 4.0);
}

TEST(Dcg, MergeRule) {
  const DcgParams p;
  std::uint8_t s = 0;
  cctvlog::hdr::DcgReadout r{0.5, 0.125, 2.0, 2.01};
  EXPECT_DOUBLE_EQ(cctvlog::hdr::dcg_merge_pixel(r, p, s), 2.0);
  EXPECT_EQ(s, 0);
  r = {0.81, 0.2025, 3.24, 3.30};
  EXPECT_NEAR(cctvlog::hdr::dcg_merge_pixel(r, p, s), 0.5 * 3.24 + 0.5 * 3.30, 1e-12);
  EXPECT_EQ(s, cctvlog::hdr::kSourceBlend);
  r = {1.0, 0.95, 4.0, 15.2};
  EXPECT_DOUBLE_EQ(cctvlog::hdr::dcg_merge_pixel(r, p, s), 15.2);
  EXPECT_EQ(s, 1 | cctvlog::hdr::kSourceSaturated);
}

TEST(Dcg, ThreadCountAndSeed) {
#ifdef _OPENMP
  DcgParams p;
  Image<float> scene(301, 97, 1);
  for (int y = 0; y < 97; ++y)
    for (int x = 0; x < 301; ++x) scene.at(x, y) = static_cast<float>(16.0 * x / 301.0);
  const int saved = omp_get_max_threads();
  omp_set_num_threads(1);
  const auto r1 = cctvlog::hdr::dcg_simulate(scene, p);
  omp_set_num_threads(4);
  const auto r4 = cctvlog::hdr::dcg_simulate(scene, p);
  omp_set_num_threads(saved);
  for (int y = 0; y < 97; ++y)
    for (int x = 0; x < 301; ++x) {
      ASSERT_EQ(r1.merged.at(x, y), r4.merged.at(x, y));
      ASSERT_EQ(r1.source.at(x, y), r4.source.at(x, y));
    }
  p.seed += 1;
  const auto r2 = cctvlog::hdr::dcg_simulate(scene, p);
  int diff = 0;
  for (int x = 0; x < 301; ++x) diff += r1.merged.at(x, 50) != r2.merged.at(x, 50);
  EXPECT_GT(diff, 250);
#else
  GTEST_SKIP() << "OpenMP off";
#endif
}

TEST(DcgParams, JsonDefaultsAndExplicitHcg) {
  const auto d = nlohmann::json::object().get<DcgParams>();
  EXPECT_DOUBLE_EQ(d.hcg_fw(), 17666.0 / 4.0);
  const auto e = nlohmann::json{{"hcg_read_noise_e", 4.1}, {"hcg_full_well_e", nullptr}}
                     .get<DcgParams>();
  EXPECT_DOUBLE_EQ(e.hcg_rn(), 4.1);
  EXPECT_DOUBLE_EQ(e.hcg_fw(), 17666.0 / 4.0);
  EXPECT_THROW((nlohmann::json{{"hcg_full_well", 1.0}}.get<DcgParams>()), std::invalid_argument);
  EXPECT_THROW((nlohmann::json{{"conversion_gain_ratio", 0.5}}.get<DcgParams>()),
               std::invalid_argument);
}
