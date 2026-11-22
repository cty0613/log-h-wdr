#pragma once

// 전달 곡선 (CLAUDE.md 6.4). 기준 모델: python/cctvlog/ref/curves.py
// - encode: 선형 x (긴 노출 스케일, [0, x_max]로 자름) → 정규화 신호 V ∈ [0, 1]
// - decode: V ([0, 1]로 자름) → x ([0, x_max]로 자름)
// 비교 곡선의 입력 스케일: x_max → 각 곡선 정의역 상한 (docs/decisions.md 2026-11-22 결정 1)

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace cctvlog::curves {

class Curve {
 public:
  virtual ~Curve() = default;
  virtual double encode(double x) const = 0;
  virtual double decode(double v) const = 0;
  double x_max() const { return x_max_; }
  const std::string& type() const { return type_; }
  const std::string& name() const { return name_; }

 protected:
  Curve(std::string type, std::string name, double x_max);
  double clamp_x(double x) const;
  double x_max_;
  std::string type_, name_;
};

// 구간별 노이즈 매칭 H-Log. 파라미터(k, 구간별 x_start·a·b·c_start)는 curve_params.py가 계산한다.
class PiecewiseNoiseMatched final : public Curve {
 public:
  struct Segment {
    double x_start, a, b, c_start, s_start;  // s_start = √(a·x_start + b)
  };
  explicit PiecewiseNoiseMatched(const nlohmann::json& j);
  double encode(double x) const override;
  double decode(double v) const override;
  double k() const { return k_; }
  const std::vector<Segment>& segments() const { return segs_; }

 private:
  double k_;
  std::vector<Segment> segs_;
};

class Rec709 final : public Curve {
 public:
  explicit Rec709(const nlohmann::json& j);
  double encode(double x) const override;
  double decode(double v) const override;
  static double oetf(double L);
  static double inverse(double V);
};

class Hlg final : public Curve {
 public:
  explicit Hlg(const nlohmann::json& j);
  double encode(double x) const override;
  double decode(double v) const override;
  static double oetf(double E);
  static double inverse(double V);
};

class SLog3 final : public Curve {
 public:
  explicit SLog3(const nlohmann::json& j);
  double encode(double x) const override;
  double decode(double v) const override;
  static double encode_in(double in);
  static double decode_in(double V);
  static double in_max();  // V = 1 (코드 1023)이 되는 입력
};

// 곡선 JSON의 "type"(hlog | rec709 | hlg | slog3)으로 만든다.
std::unique_ptr<Curve> make_curve(const nlohmann::json& j);

// H-Log 고정소수점 LUT (docs/fixed_point.md, decisions.md 2026-11-22 결정 5)
// 정방향: 인덱스 = round_half_up(clamp(x)/x_max·65535), 엔트리 = 10bit 코드 << 6 (uint16)
// 역방향: 1024 엔트리, float32(C⁻¹(code / 1023))
class FixedLut {
 public:
  static constexpr int kForwardSize = 65536;
  static constexpr int kBits = 10;
  static constexpr int kInverseSize = 1 << kBits;
  static constexpr int kShift = 16 - kBits;

  explicit FixedLut(const Curve& c);
  std::size_t index(double x) const;
  std::uint16_t encode16(double x) const { return forward_[index(x)]; }
  int encode_code(double x) const { return forward_[index(x)] >> kShift; }
  float decode_code(int code) const;
  const std::vector<std::uint16_t>& forward() const { return forward_; }
  const std::vector<float>& inverse() const { return inverse_; }
  double x_max() const { return x_max_; }

 private:
  double x_max_;
  std::vector<std::uint16_t> forward_;
  std::vector<float> inverse_;
};

}  // namespace cctvlog::curves
