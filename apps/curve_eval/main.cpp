// curve_eval: 램프 입력 → encode → quantize(bits) → decode → 결과 npy (CLAUDE.md Phase 3).
//
// 사용법: curve_eval --config <curve.json> --out <dir> [--bits 8,10,12] [--in x.npy]
//                    [--ramp-n 200001] [--ramp-min 1e-5] [--lut]
//   --config   곡선 JSON (curve_params.py 출력: type = hlog | rec709 | hlg | slog3)
//   --in       float32 1채널 입력 (없으면 [ramp-min, x_max] 로그 램프 + 0, x_max)
//   --lut      H-Log 고정소수점 LUT 경로도 평가 (10bit)
// 출력:
//   x.npy (float32 입력), v.npy (float32 float 경로 V)
//   codes_<b>.npy (uint16), decoded_<b>.npy (float32), levels_<b>.npy (float32, 코드 0..2^b−1의 복호값)
//   --lut: lut_forward.npy (uint16 65536), lut_inverse.npy (float32 1024), lut_codes.npy,
//          lut_decoded.npy, run.json의 lut 통계(조밀 검사 포함)
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "cctvlog/cli.hpp"
#include "cctvlog/curves.hpp"
#include "cctvlog/fixed.hpp"
#include "cctvlog/io.hpp"
#include "cctvlog/run_info.hpp"

namespace fs = std::filesystem;
namespace io = cctvlog::io;
namespace cv = cctvlog::curves;
using nlohmann::json;

namespace {

constexpr const char* kUsage =
    "usage: curve_eval --config <curve.json> --out <dir> [--bits 8,10,12] [--in x.npy]\n"
    "                  [--ramp-n 200001] [--ramp-min 1e-5] [--lut]\n";

std::vector<int> parse_bits(const std::string& s) {
  std::vector<int> out;
  std::stringstream ss(s);
  std::string tok;
  while (std::getline(ss, tok, ',')) {
    if (tok.empty()) continue;
    const int b = std::stoi(tok);
    if (b < 2 || b > 16) throw std::invalid_argument("--bits must be in [2, 16]");
    out.push_back(b);
  }
  return out;
}

template <typename T>
cctvlog::Image<T> row_image(const std::vector<T>& v) {
  cctvlog::Image<T> img(static_cast<int>(v.size()), 1, 1);
  std::copy(v.begin(), v.end(), img.row(0));
  return img;
}

// 조밀 검사: 모든 LUT 인덱스 점과 그 사이(인덱스 반올림 경계 근처 포함)
json lut_dense_check(const cv::Curve& c, const cv::FixedLut& lut) {
  const int n = cv::FixedLut::kForwardSize;
  const double step = c.x_max() / (n - 1);
  const double offs[] = {0.0, 0.25, 0.4999, 0.5001, 0.75};
  int max_err = 0;
  double worst_x = 0.0;
  std::int64_t count = 0, over1 = 0;
  std::vector<std::int64_t> hist(4, 0);
  for (int i = 0; i < n; ++i) {
    for (double o : offs) {
      const double x = std::min((i + o) * step, c.x_max());
      const auto ref = cctvlog::fixed::to_code(c.encode(x), cv::FixedLut::kBits);
      const int err = static_cast<int>(std::llabs(lut.encode_code(x) - ref));
      ++hist[static_cast<std::size_t>(std::min(err, 3))];
      ++count;
      over1 += err > 1;
      if (err > max_err) {
        max_err = err;
        worst_x = x;
      }
    }
  }
  // 단조성
  bool mono = true;
  for (std::size_t i = 1; i < lut.forward().size(); ++i) mono = mono && lut.forward()[i] >= lut.forward()[i - 1];
  for (std::size_t i = 1; i < lut.inverse().size(); ++i) mono = mono && lut.inverse()[i] >= lut.inverse()[i - 1];
  // 역방향: float 복호와의 차이 (선형 단위, 상대)
  double inv_rel = 0.0;
  for (int code = 1; code < cv::FixedLut::kInverseSize; ++code) {
    const double ref = c.decode(static_cast<double>(code) / (cv::FixedLut::kInverseSize - 1));
    inv_rel = std::max(inv_rel, std::fabs(lut.decode_code(code) - ref) / ref);
  }
  return {{"samples", count},
          {"max_abs_err_lsb", max_err},
          {"worst_x", worst_x},
          {"count_err_gt_1lsb", over1},
          {"hist_err_0_1_2_3plus", hist},
          {"monotonic", mono},
          {"inverse_max_rel_err_vs_float", inv_rel}};
}

}  // namespace

int main(int argc, char** argv) {
  cctvlog::RunInfo info("curve_eval", argc, argv);
  try {
    const cctvlog::cli::Args args(argc, argv);
    if (args.has("help")) {
      std::cout << kUsage;
      return 0;
    }
    const fs::path config_path = args.require("config");
    const fs::path out_dir = args.require("out");
    const json cfg = io::read_json(config_path);
    const auto curve = cv::make_curve(cfg);
    const std::vector<int> bits = parse_bits(args.get("bits").value_or("8,10,12"));

    std::vector<float> xs;
    if (auto in = args.get("in"); in && !in->empty()) {
      const auto img = io::read_npy<float>(*in);
      for (int y = 0; y < img.height(); ++y)
        for (int x = 0; x < img.width() * img.channels(); ++x) xs.push_back(img.row(y)[x]);
    } else {
      const int n = std::stoi(args.get("ramp-n").value_or("200001"));
      const double lo = std::stod(args.get("ramp-min").value_or("1e-5"));
      if (n < 2 || !(lo > 0.0) || lo >= curve->x_max()) throw std::invalid_argument("bad ramp");
      xs.push_back(0.0f);
      for (int i = 0; i < n; ++i) {
        xs.push_back(static_cast<float>(lo * std::pow(curve->x_max() / lo, double(i) / (n - 1))));
      }
    }

    fs::create_directories(out_dir);
    std::vector<float> v(xs.size());
    {
      cctvlog::ScopedTimer t(info, "encode_float");
      for (std::size_t i = 0; i < xs.size(); ++i) v[i] = static_cast<float>(curve->encode(xs[i]));
    }
    io::write_npy(out_dir / "x.npy", row_image(xs));
    io::write_npy(out_dir / "v.npy", row_image(v));
    json per_bits = json::object();
    for (int b : bits) {
      cctvlog::ScopedTimer t(info, "quantize_" + std::to_string(b));
      const int cmax = (1 << b) - 1;
      std::vector<std::uint16_t> codes(xs.size());
      std::vector<float> dec(xs.size());
      std::vector<float> levels(static_cast<std::size_t>(cmax) + 1);
      for (int c = 0; c <= cmax; ++c) {
        levels[static_cast<std::size_t>(c)] = static_cast<float>(curve->decode(double(c) / cmax));
      }
      double max_rel_roundtrip = 0.0;
      for (std::size_t i = 0; i < xs.size(); ++i) {
        const double vv = curve->encode(xs[i]);
        codes[i] = static_cast<std::uint16_t>(cctvlog::fixed::to_code(vv, b));
        dec[i] = static_cast<float>(curve->decode(double(codes[i]) / cmax));
        if (xs[i] > 0.0f) {
          const double back = curve->decode(vv);  // 양자화 없는 왕복
          max_rel_roundtrip = std::max(max_rel_roundtrip, std::fabs(back - xs[i]) / xs[i]);
        }
      }
      io::write_npy(out_dir / ("codes_" + std::to_string(b) + ".npy"), row_image(codes));
      io::write_npy(out_dir / ("decoded_" + std::to_string(b) + ".npy"), row_image(dec));
      io::write_npy(out_dir / ("levels_" + std::to_string(b) + ".npy"), row_image(levels));
      per_bits[std::to_string(b)] = {{"float_roundtrip_max_rel_err", max_rel_roundtrip}};
    }
    info.set("bits", per_bits);

    if (args.has("lut")) {
      if (curve->type() != "hlog") throw std::invalid_argument("--lut is for hlog curves only");
      cctvlog::ScopedTimer t(info, "lut");
      const cv::FixedLut lut(*curve);
      std::vector<std::uint16_t> lc(xs.size());
      std::vector<float> ld(xs.size());
      for (std::size_t i = 0; i < xs.size(); ++i) {
        lc[i] = static_cast<std::uint16_t>(lut.encode_code(xs[i]));
        ld[i] = lut.decode_code(lc[i]);
      }
      io::write_npy(out_dir / "lut_forward.npy", row_image(lut.forward()));
      io::write_npy(out_dir / "lut_inverse.npy", row_image(lut.inverse()));
      io::write_npy(out_dir / "lut_codes.npy", row_image(lc));
      io::write_npy(out_dir / "lut_decoded.npy", row_image(ld));
      info.set("lut", lut_dense_check(*curve, lut));
    }
    info.set_params(cfg);
    info.set("config_path", fs::absolute(config_path).string());
    info.set("n_inputs", xs.size());
    info.write(out_dir);
    return 0;
  } catch (const std::invalid_argument& e) {
    std::cerr << "curve_eval: " << e.what() << '\n' << kUsage;
    return 2;
  } catch (const std::exception& e) {
    std::cerr << "curve_eval: " << e.what() << '\n';
    return 1;
  }
}
