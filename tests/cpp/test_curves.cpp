// curves: 공개 기준값, 왕복, 단조성, H-Log 해석 성질, 고정소수점 LUT (CLAUDE.md 1.1, Phase 3).
// Python 기준 모델과의 상대오차 비교는 tests/py/test_curves_crosscheck.py.
#include <array>
#include <cmath>
#include <vector>

#include <gtest/gtest.h>

#include "cctvlog/curves.hpp"
#include "cctvlog/fixed.hpp"

namespace cv = cctvlog::curves;
using nlohmann::json;

namespace {

// curve_params.py와 같은 계산 (테스트용 H-Log JSON)
json hlog_json(const std::vector<std::array<double, 3>>& segs, double x_max) {
  std::vector<double> starts;
  for (const auto& s : segs) starts.push_back(s[0]);
  starts.push_back(x_max);
  std::vector<double> part{0.0};
  for (std::size_t i = 0; i < segs.size(); ++i) {
    const double a = segs[i][1], b = segs[i][2], x0 = starts[i], x1 = starts[i + 1];
    part.push_back(part.back() + 2.0 * (x1 - x0) / (std::sqrt(a * x1 + b) + std::sqrt(a * x0 + b)));
  }
  const double k = 1.0 / part.back();
  json j = {{"type", "hlog"}, {"name", "test"}, {"x_max", x_max}, {"k", k}, {"segments", json::array()}};
  for (std::size_t i = 0; i < segs.size(); ++i) {
    j["segments"].push_back(
        {{"x_start", segs[i][0]}, {"a", segs[i][1]}, {"b", segs[i][2]}, {"c_start", part[i] * k}});
  }
  return j;
}

constexpr double kA = 5.660737407263459e-05, kB = 4.0833976754083865e-07;

json r16() {  // 브래킷 16:4:1 (configs/noise_bracket_R16.json과 같은 구간)
  return hlog_json({{{0.0, kA, kB}}, {{0.9, 4 * kA, 16 * kB}}, {{3.6, 16 * kA, 256 * kB}}}, 16.0);
}

}  // namespace

TEST(Fixed, RoundHalfUpAndSaturate) {
  EXPECT_EQ(cctvlog::fixed::round_half_up(0.5), 1);
  EXPECT_EQ(cctvlog::fixed::round_half_up(1.49999), 1);
  EXPECT_EQ(cctvlog::fixed::round_half_up(-0.5), 0);
  EXPECT_EQ(cctvlog::fixed::round_half_up(-1.5), -1);
  EXPECT_EQ(cctvlog::fixed::to_code(1.2, 10), 1023);
  EXPECT_EQ(cctvlog::fixed::to_code(-0.1, 10), 0);
  EXPECT_EQ(cctvlog::fixed::to_code(std::nan(""), 10), 0);
  EXPECT_EQ(cctvlog::fixed::to_code(0.5 / 1023.0, 10), 1);  // 정확히 반 → 올림
}

// 공개 기준값 (출처: 각 곡선 주석)
TEST(Curves, Rec709Reference) {
  // BT.709의 반올림된 상수 때문에 L = 0.018에서 두 식이 0.00025 어긋난다 (알려진 성질)
  EXPECT_NEAR(4.5 * 0.018, 0.081, 1e-15);
  EXPECT_NEAR(cv::Rec709::oetf(0.018), 0.0812479, 1e-6);
  EXPECT_NEAR(cv::Rec709::oetf(1.0), 1.0, 1e-12);
  EXPECT_NEAR(cv::Rec709::oetf(0.01), 0.045, 1e-12);
  EXPECT_NEAR(cv::Rec709::inverse(cv::Rec709::oetf(0.4)), 0.4, 1e-12);
}

TEST(Curves, HlgReference) {
  EXPECT_NEAR(cv::Hlg::oetf(1.0 / 12.0), 0.5, 1e-12);
  EXPECT_NEAR(cv::Hlg::oetf(1.0), 1.0, 1e-7);  // BT.2100: E = 1 → 1.0 (상수 반올림 수준)
  EXPECT_NEAR(cv::Hlg::oetf(0.0), 0.0, 1e-15);
  EXPECT_NEAR(cv::Hlg::inverse(cv::Hlg::oetf(0.3)), 0.3, 1e-12);
}

TEST(Curves, SLog3Reference) {
  EXPECT_NEAR(cv::SLog3::encode_in(0.18) * 1023.0, 420.0, 1e-9);   // 18% 회색 → 420
  EXPECT_EQ(std::lround(cv::SLog3::encode_in(0.90) * 1023.0), 598);  // 90% 백색 → 598 (반올림 코드)
  EXPECT_NEAR(cv::SLog3::encode_in(0.0) * 1023.0, 95.0, 1e-9);     // 검정 → 95
  EXPECT_NEAR(cv::SLog3::encode_in(0.01125) * 1023.0, 171.2102946929, 1e-6);  // 연결점 연속
  EXPECT_NEAR(cv::SLog3::encode_in(cv::SLog3::in_max()), 1.0, 1e-12);
  EXPECT_NEAR(cv::SLog3::decode_in(cv::SLog3::encode_in(5.0)), 5.0, 1e-9);
}

class AllCurves : public ::testing::TestWithParam<std::string> {};

TEST_P(AllCurves, MonotonicRoundTripAndRange) {
  json j = GetParam() == "hlog" ? r16() : json{{"type", GetParam()}, {"x_max", 16.0}};
  const auto c = cv::make_curve(j);
  EXPECT_NEAR(c->encode(16.0), 1.0, 1e-7);
  EXPECT_NEAR(c->encode(100.0), c->encode(16.0), 0.0);  // 포화
  EXPECT_EQ(c->encode(-1.0), c->encode(0.0));          // 0으로 자름
  double prev = -1.0;
  for (int i = 0; i <= 20000; ++i) {
    const double x = 16.0 * std::pow(1e-6, 1.0 - i / 20000.0);
    const double v = c->encode(x);
    ASSERT_GT(v, prev) << GetParam() << " x " << x;
    prev = v;
    ASSERT_NEAR(c->decode(v), x, 1e-9 * x + 1e-15) << GetParam() << " x " << x;
  }
  // 양자화 왕복: 복호값이 같은 코드로 다시 부호화된다 (x = 0의 코드부터. S-Log3는 95 아래가 음수 입력)
  for (int code = static_cast<int>(cctvlog::fixed::to_code(c->encode(0.0), 10)); code < 1024; ++code) {
    const double x = c->decode(code / 1023.0);
    ASSERT_EQ(cctvlog::fixed::to_code(c->encode(x), 10), code) << GetParam() << " code " << code;
  }
}

INSTANTIATE_TEST_SUITE_P(Types, AllCurves, ::testing::Values("hlog", "rec709", "hlg", "slog3"));

TEST(HLog, NoiseMatchingProperties) {
  const auto j = r16();
  const cv::PiecewiseNoiseMatched c(j);
  // C'(x) = k/σ(x): 수치 미분과 비교, 경계에서 값 연속
  for (double x : {0.001, 0.1, 0.5, 0.89, 1.5, 3.0, 5.0, 12.0}) {
    const double h = 1e-7 * std::max(x, 1e-3);
    const double d = (c.encode(x + h) - c.encode(x - h)) / (2 * h);
    const double r = x < 0.9 ? 1 : x < 3.6 ? 4 : 16;
    const double sigma = std::sqrt(kA * r * x + kB * r * r);
    EXPECT_NEAR(d * sigma / c.k(), 1.0, 1e-5) << "x " << x;
  }
  for (double xb : {0.9, 3.6}) EXPECT_NEAR(c.encode(xb - 1e-12), c.encode(xb + 1e-12), 1e-10);
  EXPECT_THROW(cv::PiecewiseNoiseMatched(json{{"type", "hlog"},
                                              {"x_max", 16.0},
                                              {"k", 0.5},
                                              {"segments", {{{"x_start", 0.0}, {"a", kA}, {"b", kB}, {"c_start", 0.0}}}}}),
               std::invalid_argument);  // C(x_max) != 1
}

TEST(FixedLut, WithinOneLsbOfFloat) {
  for (const auto& j : {r16(), hlog_json({{{0.0, kA, kB}}, {{0.9, 16 * kA, 256 * kB}}}, 16.0),
                        hlog_json({{{0.0, kA, kB}}}, 1.0)}) {
    const cv::PiecewiseNoiseMatched c(j);
    const cv::FixedLut lut(c);
    int max_err = 0;
    const double step = c.x_max() / (cv::FixedLut::kForwardSize - 1);
    for (int i = 0; i < cv::FixedLut::kForwardSize; ++i) {
      for (double o : {0.0, 0.4999, 0.5001}) {
        const double x = std::min((i + o) * step, c.x_max());
        const auto ref = cctvlog::fixed::to_code(c.encode(x), 10);
        max_err = std::max(max_err, static_cast<int>(std::llabs(lut.encode_code(x) - ref)));
      }
    }
    EXPECT_LE(max_err, 1);
    EXPECT_EQ(lut.encode_code(0.0), 0);
    EXPECT_EQ(lut.encode_code(c.x_max()), 1023);
    EXPECT_EQ(lut.encode16(c.x_max()) & ((1 << cv::FixedLut::kShift) - 1), 0);  // 하위 6bit 0
    for (int code = 0; code < 1024; ++code) {
      EXPECT_FLOAT_EQ(lut.decode_code(code), static_cast<float>(c.decode(code / 1023.0)));
    }
  }
}
