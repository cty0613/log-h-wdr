// hdr_merge: 브래킷 세트 폴더 → 합성 HDR Bayer (CLAUDE.md 6.1, Phase 2).
//
// 사용법: hdr_merge --config <json> --in <bracket_dir> --out <dir> [--frames 0,2]
//   --config  BracketMergeParams JSON (configs/hdr_merge.json: tau, band_ratio)
//   --in      bracket_KKKK/ (bracket.json + NNNNNN.npy/.json, capture_raw.py 형식)
//   --frames  bracket.json files 목록에서 쓸 프레임 번호 (기본: 전부). 예: R=4는 0,1
// 출력: merged.npy (float32, 긴 노출 스케일), source.npy (uint8 출처 맵), merged.json, run.json
#include <algorithm>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "cctvlog/cli.hpp"
#include "cctvlog/hdr.hpp"
#include "cctvlog/io.hpp"
#include "cctvlog/params.hpp"
#include "cctvlog/run_info.hpp"

namespace fs = std::filesystem;
namespace io = cctvlog::io;
using nlohmann::json;

namespace {

constexpr const char* kUsage =
    "usage: hdr_merge --config <json> --in <bracket_dir> --out <dir> [--frames 0,1,2]\n";

std::vector<std::size_t> parse_frames(const std::string& s) {
  std::vector<std::size_t> out;
  std::stringstream ss(s);
  std::string tok;
  while (std::getline(ss, tok, ',')) {
    if (!tok.empty()) out.push_back(static_cast<std::size_t>(std::stoul(tok)));
  }
  return out;
}

double black_of(const json& meta) {
  const auto& bl = meta.at("black_level");
  if (bl.is_number()) return bl.get<double>();
  if (bl.is_array() && !bl.empty()) {
    for (const auto& v : bl) {
      if (v.get<double>() != bl[0].get<double>()) {
        throw std::runtime_error("per-channel black levels differ (unsupported)");
      }
    }
    return bl[0].get<double>();
  }
  throw std::runtime_error("frame JSON black_level is missing or null");
}

}  // namespace

int main(int argc, char** argv) {
  cctvlog::RunInfo info("hdr_merge", argc, argv);
  try {
    const cctvlog::cli::Args args(argc, argv);
    if (args.has("help")) {
      std::cout << kUsage;
      return 0;
    }
    const fs::path config_path = args.require("config");
    const fs::path in_dir = args.require("in");
    const fs::path out_dir = args.require("out");

    const json config = io::read_json(config_path);
    const auto params = config.get<cctvlog::BracketMergeParams>();
    const json bj = io::read_json(in_dir / "bracket.json");
    const auto files = bj.at("files").get<std::vector<std::string>>();
    std::vector<std::size_t> sel;
    if (auto f = args.get("frames"); f && !f->empty()) {
      sel = parse_frames(*f);
    } else {
      for (std::size_t i = 0; i < files.size(); ++i) sel.push_back(i);
    }

    std::vector<cctvlog::Image<std::uint16_t>> raws;
    std::vector<json> metas;
    raws.reserve(sel.size());
    {
      cctvlog::ScopedTimer t(info, "load");
      for (std::size_t i : sel) {
        if (i >= files.size()) throw std::invalid_argument("--frames index out of range");
        const fs::path npy = in_dir / files[i];
        raws.push_back(io::read_npy<std::uint16_t>(npy));
        metas.push_back(io::read_json(fs::path(npy).replace_extension(".json")));
      }
    }
    std::vector<cctvlog::hdr::BracketFrame> frames;
    json frames_json = json::array();
    std::string bayer;
    for (std::size_t k = 0; k < sel.size(); ++k) {
      const json& m = metas[k];
      if (m.at("exposure_time_us").is_null()) throw std::runtime_error("exposure_time_us is null");
      const auto order = m.at("bayer_order").get<std::string>();
      if (k == 0) bayer = order;
      if (order != bayer) throw std::runtime_error("bayer_order differs between frames");
      cctvlog::parse_bayer_order(order);  // 형식 확인
      cctvlog::hdr::BracketFrame f{&raws[k], m.at("exposure_time_us").get<double>(), black_of(m),
                                   m.at("white_level").get<double>()};
      frames.push_back(f);
      frames_json.push_back({{"index", sel[k]},
                             {"file", files[sel[k]]},
                             {"exposure_time_us", f.exposure_us},
                             {"black_level", f.black},
                             {"white_level", f.white},
                             {"analogue_gain", m.value("analogue_gain", json())}});
    }

    cctvlog::hdr::MergeResult res;
    {
      cctvlog::ScopedTimer t(info, "bracket_merge");
      res = cctvlog::hdr::bracket_merge(frames, params);
    }
    double t_long = 0.0, t_short = 0.0;
    for (const auto& f : frames) {
      t_long = std::max(t_long, f.exposure_us);
      t_short = (t_short == 0.0) ? f.exposure_us : std::min(t_short, f.exposure_us);
    }
    for (auto& fj : frames_json) fj["ratio"] = t_long / fj["exposure_time_us"].get<double>();

    // 출처 통계
    std::vector<std::uint64_t> hist(256, 0);
    for (int y = 0; y < res.source.height(); ++y) {
      const auto* s = res.source.row(y);
      for (int x = 0; x < res.source.width(); ++x) ++hist[s[x]];
    }
    json source_hist = json::object();
    const double npx = static_cast<double>(res.source.width()) * res.source.height();
    for (int v = 0; v < 256; ++v) {
      if (hist[static_cast<std::size_t>(v)]) {
        source_hist[std::to_string(v)] = static_cast<double>(hist[static_cast<std::size_t>(v)]) / npx;
      }
    }

    {
      cctvlog::ScopedTimer t(info, "write");
      fs::create_directories(out_dir);
      io::write_npy(out_dir / "merged.npy", res.merged);
      io::write_npy(out_dir / "source.npy", res.source);
      io::write_json(out_dir / "merged.json",
                     {{"input", fs::absolute(in_dir).lexically_normal().string()},
                      {"bayer_order", bayer},
                      {"scale", "normalized_long_exposure"},
                      {"x_max", t_long / t_short},
                      {"frames", frames_json},
                      {"params", params},
                      {"source_bits",
                       {{"index_mask", cctvlog::hdr::kSourceIndexMask},
                        {"blend", cctvlog::hdr::kSourceBlend},
                        {"saturated", cctvlog::hdr::kSourceSaturated},
                        {"index", "position in the 'frames' list below (after --frames selection)"}}},
                      {"source_fraction", source_hist}});
    }
    info.set_params(params);
    info.set("config_path", fs::absolute(config_path).string());
    info.set("input", fs::absolute(in_dir).string());
    info.set("frames", frames_json);
    info.write(out_dir);
    return 0;
  } catch (const std::invalid_argument& e) {
    std::cerr << "hdr_merge: " << e.what() << '\n' << kUsage;
    return 2;
  } catch (const std::exception& e) {
    std::cerr << "hdr_merge: " << e.what() << '\n';
    return 1;
  }
}
