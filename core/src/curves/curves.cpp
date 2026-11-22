// 전달 곡선 (CLAUDE.md 6.4). 외부 수식 출처는 각 함수 주석, 기준값은 tests/cpp/test_curves.cpp.
#include "cctvlog/curves.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "cctvlog/fixed.hpp"

namespace cctvlog::curves {

namespace {

double require_x_max(const nlohmann::json& j) {
  const double xm = j.at("x_max").get<double>();
  if (!(xm > 0.0)) throw std::invalid_argument("curve: x_max must be > 0");
  return xm;
}

// HLG 상수 (ITU-R BT.2100-2 표 5)
constexpr double kHlgA = 0.17883277;
const double kHlgB = 1.0 - 4.0 * kHlgA;
const double kHlgC = 0.5 - kHlgA * std::log(4.0 * kHlgA);
// S-Log3 상수 (Sony S-Log3 Technical Summary)
constexpr double kSlog3BreakIn = 0.01125;
constexpr double kSlog3BreakCode = 171.2102946929;

}  // namespace

Curve::Curve(std::string type, std::string name, double x_max)
    : x_max_(x_max), type_(std::move(type)), name_(std::move(name)) {}

double Curve::clamp_x(double x) const {
  if (std::isnan(x)) return 0.0;
  return std::clamp(x, 0.0, x_max_);
}

// ---- H-Log -----------------------------------------------------------------------------
PiecewiseNoiseMatched::PiecewiseNoiseMatched(const nlohmann::json& j)
    : Curve("hlog", j.value("name", std::string("hlog")), require_x_max(j)),
      k_(j.at("k").get<double>()) {
  if (!(k_ > 0.0)) throw std::invalid_argument("hlog: k must be > 0");
  for (const auto& s : j.at("segments")) {
    Segment g{s.at("x_start").get<double>(), s.at("a").get<double>(), s.at("b").get<double>(),
              s.at("c_start").get<double>(), 0.0};
    if (g.a < 0.0 || g.b <= 0.0) throw std::invalid_argument("hlog: need a >= 0, b > 0");
    g.s_start = std::sqrt(g.a * g.x_start + g.b);
    if (!segs_.empty() && !(g.x_start > segs_.back().x_start && g.c_start > segs_.back().c_start)) {
      throw std::invalid_argument("hlog: segments must increase in x_start and c_start");
    }
    segs_.push_back(g);
  }
  if (segs_.empty() || segs_.front().x_start != 0.0 || segs_.front().c_start != 0.0) {
    throw std::invalid_argument("hlog: first segment must start at x = 0, C = 0");
  }
  // 파라미터 일관성: C(x_max) = 1
  if (std::fabs(encode(x_max_) - 1.0) > 1e-9) {
    throw std::invalid_argument("hlog: C(x_max) != 1 (inconsistent k / c_start)");
  }
}

double PiecewiseNoiseMatched::encode(double x) const {
  x = clamp_x(x);
  std::size_t i = segs_.size() - 1;
  while (i > 0 && x < segs_[i].x_start) --i;
  const Segment& g = segs_[i];
  // ∫ dt/√(a t + b) = 2(x − x_i) / (√(a x + b) + √(a x_i + b))  (a → 0에서도 안정)
  return g.c_start + k_ * 2.0 * (x - g.x_start) / (std::sqrt(g.a * x + g.b) + g.s_start);
}

double PiecewiseNoiseMatched::decode(double v) const {
  if (std::isnan(v)) return 0.0;
  v = std::clamp(v, 0.0, 1.0);
  std::size_t i = segs_.size() - 1;
  while (i > 0 && v < segs_[i].c_start) --i;
  const Segment& g = segs_[i];
  const double u = (v - g.c_start) / k_;
  // √(a x + b) = s_i + a u / 2  →  x = x_i + u·(s_i + a u / 4)
  return std::clamp(g.x_start + u * (g.s_start + g.a * u / 4.0), 0.0, x_max_);
}

// ---- Rec.709 (ITU-R BT.709-6 1.2절) ------------------------------------------------------
Rec709::Rec709(const nlohmann::json& j)
    : Curve("rec709", j.value("name", std::string("rec709")), require_x_max(j)) {}
double Rec709::oetf(double L) { return L < 0.018 ? 4.5 * L : 1.099 * std::pow(L, 0.45) - 0.099; }
double Rec709::inverse(double V) {
  return V < 0.081 ? V / 4.5 : std::pow((V + 0.099) / 1.099, 1.0 / 0.45);
}
double Rec709::encode(double x) const { return oetf(clamp_x(x) / x_max_); }
double Rec709::decode(double v) const {
  if (std::isnan(v)) return 0.0;
  return std::clamp(inverse(std::clamp(v, 0.0, 1.0)) * x_max_, 0.0, x_max_);
}

// ---- HLG (ITU-R BT.2100-2 표 5) -----------------------------------------------------------
Hlg::Hlg(const nlohmann::json& j)
    : Curve("hlg", j.value("name", std::string("hlg")), require_x_max(j)) {}
double Hlg::oetf(double E) {
  return E <= 1.0 / 12.0 ? std::sqrt(3.0 * E) : kHlgA * std::log(12.0 * E - kHlgB) + kHlgC;
}
double Hlg::inverse(double V) {
  return V <= 0.5 ? V * V / 3.0 : (std::exp((V - kHlgC) / kHlgA) + kHlgB) / 12.0;
}
double Hlg::encode(double x) const { return oetf(clamp_x(x) / x_max_); }
double Hlg::decode(double v) const {
  if (std::isnan(v)) return 0.0;
  return std::clamp(inverse(std::clamp(v, 0.0, 1.0)) * x_max_, 0.0, x_max_);
}

// ---- S-Log3 (Sony S-Log3 Technical Summary) ----------------------------------------------
SLog3::SLog3(const nlohmann::json& j)
    : Curve("slog3", j.value("name", std::string("slog3")), require_x_max(j)) {}
double SLog3::encode_in(double in) {
  return in >= kSlog3BreakIn ? (420.0 + 261.5 * std::log10((in + 0.01) / 0.19)) / 1023.0
                             : (in * (kSlog3BreakCode - 95.0) / kSlog3BreakIn + 95.0) / 1023.0;
}
double SLog3::decode_in(double V) {
  const double c = V * 1023.0;
  return c >= kSlog3BreakCode ? std::pow(10.0, (c - 420.0) / 261.5) * 0.19 - 0.01
                              : (c - 95.0) * kSlog3BreakIn / (kSlog3BreakCode - 95.0);
}
double SLog3::in_max() { return 0.19 * std::pow(10.0, (1023.0 - 420.0) / 261.5) - 0.01; }
double SLog3::encode(double x) const { return encode_in(clamp_x(x) * in_max() / x_max_); }
double SLog3::decode(double v) const {
  if (std::isnan(v)) return 0.0;
  return std::clamp(decode_in(std::clamp(v, 0.0, 1.0)) * x_max_ / in_max(), 0.0, x_max_);
}

std::unique_ptr<Curve> make_curve(const nlohmann::json& j) {
  const auto t = j.at("type").get<std::string>();
  if (t == "hlog") return std::make_unique<PiecewiseNoiseMatched>(j);
  if (t == "rec709") return std::make_unique<Rec709>(j);
  if (t == "hlg") return std::make_unique<Hlg>(j);
  if (t == "slog3") return std::make_unique<SLog3>(j);
  throw std::invalid_argument("unknown curve type: " + t);
}

// ---- 고정소수점 LUT ------------------------------------------------------------------------
FixedLut::FixedLut(const Curve& c)
    : x_max_(c.x_max()), forward_(kForwardSize), inverse_(kInverseSize) {
  for (int i = 0; i < kForwardSize; ++i) {
    const double x = static_cast<double>(i) / (kForwardSize - 1) * x_max_;
    const auto code = fixed::to_code(c.encode(x), kBits);
    forward_[static_cast<std::size_t>(i)] = static_cast<std::uint16_t>(code << kShift);
  }
  for (int code = 0; code < kInverseSize; ++code) {
    inverse_[static_cast<std::size_t>(code)] =
        static_cast<float>(c.decode(static_cast<double>(code) / (kInverseSize - 1)));
  }
}

std::size_t FixedLut::index(double x) const {
  if (std::isnan(x)) return 0;
  const double t = std::clamp(x, 0.0, x_max_) / x_max_ * (kForwardSize - 1);
  return static_cast<std::size_t>(fixed::quantize(t, 0, kForwardSize - 1));
}

float FixedLut::decode_code(int code) const {
  return inverse_[static_cast<std::size_t>(std::clamp(code, 0, kInverseSize - 1))];
}

}  // namespace cctvlog::curves
