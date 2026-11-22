// hdr::bracket_merge: 해석적 입력(상수, 램프) 테스트 (CLAUDE.md 6.1).
// Python 기준 모델과의 상대오차 비교는 tests/py/test_bracket_merge_crosscheck.py.
#include <cmath>
#include <cstdint>
#include <vector>

#include <gtest/gtest.h>
#ifdef _OPENMP
#include <omp.h>
#endif

#include "cctvlog/hdr.hpp"
#include "cctvlog/params.hpp"

using cctvlog::BracketMergeParams;
using cctvlog::Image;
using cctvlog::hdr::BracketFrame;
using cctvlog::hdr::kSourceBlend;
using cctvlog::hdr::kSourceSaturated;

namespace {

constexpr double kBlack = 64.0, kWhite = 1023.0, kRange = kWhite - kBlack;

Image<std::uint16_t> constant(int w, int h, int v) {
  Image<std::uint16_t> img(w, h, 1);
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x) img.at(x, y) = static_cast<std::uint16_t>(v);
  return img;
}

std::uint16_t code_of(double n) { return static_cast<std::uint16_t>(std::lround(n * kRange + kBlack)); }
double norm_of(std::uint16_t c) { return (c - kBlack) / kRange; }

}  // namespace

TEST(BracketMerge, UsesLongBelowBand) {
  auto l = constant(4, 2, code_of(0.5)), s = constant(4, 2, code_of(0.5 / 16));
  std::vector<BracketFrame> f{{&l, 16000, kBlack, kWhite}, {&s, 1000, kBlack, kWhite}};
  const auto r = cctvlog::hdr::bracket_merge(f, {});
  EXPECT_FLOAT_EQ(r.merged.at(1, 1), static_cast<float>(norm_of(code_of(0.5))));
  EXPECT_EQ(r.source.at(1, 1), 0);
}

TEST(BracketMerge, ScalesShortWhenLongSaturated) {
  auto l = constant(4, 2, 1023), s = constant(4, 2, 600);
  std::vector<BracketFrame> f{{&l, 16000, kBlack, kWhite}, {&s, 1000, kBlack, kWhite}};
  const auto r = cctvlog::hdr::bracket_merge(f, {});
  EXPECT_FLOAT_EQ(r.merged.at(0, 0), static_cast<float>(norm_of(600) * 16.0));
  EXPECT_EQ(r.source.at(0, 0), 1);
}

TEST(BracketMerge, BandWeights) {
  // 띠 중간 n = 0.81: w = (0.9 − 0.81) / 0.18 = 0.5
  BracketMergeParams p;
  const double n_long = 0.81;
  const double lo = p.band_ratio * p.tau;
  Image<std::uint16_t> l(1, 1, 1), s(1, 1, 1);
  l.at(0, 0) = 841;  // (841 − 64)/959 = 0.81022
  s.at(0, 0) = 113;
  std::vector<BracketFrame> f{{&l, 4000, kBlack, kWhite}, {&s, 1000, kBlack, kWhite}};
  const auto r = cctvlog::hdr::bracket_merge(f, p);
  const double nl = norm_of(841), ns = norm_of(113) * 4.0;
  const double w = (p.tau - nl) / (p.tau - lo);
  EXPECT_NEAR(w, 0.5, 0.01);
  EXPECT_NEAR(n_long, nl, 1e-3);
  EXPECT_FLOAT_EQ(r.merged.at(0, 0), static_cast<float>(w * nl + (1 - w) * ns));
  EXPECT_EQ(r.source.at(0, 0), kSourceBlend);
}

TEST(BracketMerge, AllSaturatedFlagsShortest) {
  auto a = constant(2, 2, 1023), b = constant(2, 2, 1000), c = constant(2, 2, 1023);
  // 입력 순서와 노출 순서를 다르게: 가장 짧은 노출은 입력 1번
  std::vector<BracketFrame> f{
      {&a, 16000, kBlack, kWhite}, {&b, 1000, kBlack, kWhite}, {&c, 4000, kBlack, kWhite}};
  const auto r = cctvlog::hdr::bracket_merge(f, {});
  EXPECT_EQ(r.source.at(0, 0), 1 | kSourceSaturated);
  EXPECT_FLOAT_EQ(r.merged.at(0, 0), static_cast<float>(norm_of(1000) * 16.0));
}

TEST(BracketMerge, ThreeFrameChainPicksMiddle) {
  auto a = constant(2, 2, 1023), b = constant(2, 2, 500), c = constant(2, 2, 200);
  std::vector<BracketFrame> f{
      {&a, 16000, kBlack, kWhite}, {&b, 4000, kBlack, kWhite}, {&c, 1000, kBlack, kWhite}};
  const auto r = cctvlog::hdr::bracket_merge(f, {});
  EXPECT_EQ(r.source.at(1, 0), 1);
  EXPECT_FLOAT_EQ(r.merged.at(1, 0), static_cast<float>(norm_of(500) * 4.0));
}

TEST(BracketMerge, NoiselessRampIsContinuousAndLinear) {
  // 노이즈 없는 이상적 입력(실수 값 그대로를 반올림한 코드)으로 x = 0..16 램프:
  // 합성 결과는 x를 따라가야 한다 (양자화 오차 범위). 3장 16:4:1.
  const int w = 4000;
  Image<std::uint16_t> a(w, 1, 1), b(w, 1, 1), c(w, 1, 1);
  std::vector<double> xs(w);
  for (int i = 0; i < w; ++i) {
    const double x = 16.0 * (i + 0.5) / w;
    xs[static_cast<std::size_t>(i)] = x;
    auto clip = [](double n) { return std::min(n, 1.0); };
    a.at(i, 0) = code_of(clip(x));
    b.at(i, 0) = code_of(clip(x / 4));
    c.at(i, 0) = code_of(clip(x / 16));
  }
  std::vector<BracketFrame> f{
      {&a, 16000, kBlack, kWhite}, {&b, 4000, kBlack, kWhite}, {&c, 1000, kBlack, kWhite}};
  const auto r = cctvlog::hdr::bracket_merge(f, {});
  for (int i = 0; i < w; ++i) {
    const double x = xs[static_cast<std::size_t>(i)];
    // 쓰인 프레임 중 가장 거친 LSB (띠 [0.8τ, τ]에서는 다음 프레임이 섞인다). 반올림 오차 ≤ 0.5 LSB
    const double q = (x < 0.72 ? 1.0 : x < 2.88 ? 4.0 : 16.0) / kRange;
    EXPECT_NEAR(r.merged.at(i, 0), x, q) << "x " << x;
  }
}

TEST(BracketMerge, ThreadCountDoesNotChangeOutput) {
#ifdef _OPENMP
  const int w = 257, h = 131;
  Image<std::uint16_t> a(w, h, 1), b(w, h, 1);
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x) {
      a.at(x, y) = static_cast<std::uint16_t>((x * 7 + y * 13) % 1024);
      b.at(x, y) = static_cast<std::uint16_t>((x * 3 + y * 5) % 1024);
    }
  std::vector<BracketFrame> f{{&a, 16000, kBlack, kWhite}, {&b, 1000, kBlack, kWhite}};
  const int saved = omp_get_max_threads();
  omp_set_num_threads(1);
  const auto r1 = cctvlog::hdr::bracket_merge(f, {});
  omp_set_num_threads(4);
  const auto r4 = cctvlog::hdr::bracket_merge(f, {});
  omp_set_num_threads(saved);
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x) {
      ASSERT_EQ(r1.merged.at(x, y), r4.merged.at(x, y));
      ASSERT_EQ(r1.source.at(x, y), r4.source.at(x, y));
    }
#else
  GTEST_SKIP() << "OpenMP off";
#endif
}

TEST(BracketMergeParams, JsonRoundTripAndValidation) {
  BracketMergeParams p;
  p.tau = 0.85;
  const nlohmann::json j = p;
  EXPECT_DOUBLE_EQ(j.get<BracketMergeParams>().tau, 0.85);
  EXPECT_DOUBLE_EQ(nlohmann::json::object().get<BracketMergeParams>().band_ratio, 0.8);
  EXPECT_THROW((nlohmann::json{{"tua", 0.9}}.get<BracketMergeParams>()), std::invalid_argument);
  EXPECT_THROW((nlohmann::json{{"band_ratio", 1.2}}.get<BracketMergeParams>()),
               std::invalid_argument);
  EXPECT_NO_THROW((nlohmann::json{{"_comment", "x"}}.get<BracketMergeParams>()));
}
