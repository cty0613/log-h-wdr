// dcg_sim: 선형 장면 → DCG(HCG/LCG 이중 판독) 합성 시뮬레이션 (CLAUDE.md 6.2, Phase 2).
//
// 사용법: dcg_sim --config <json> --out <dir> [--in <scene.npy>] [--seed N]
//   --config  DcgParams JSON (configs/dcg_assumptions.json)
//   --in      float32 1채널 선형 장면 (예: hdr_merge의 merged.npy). 없으면 노이즈 JSON만 쓴다.
//             같은 폴더에 merged.json이 있으면 bayer_order를 옮겨 적는다.
//   --seed    config의 seed 대신 사용
// 출력: dcg.npy (float32), dcg_source.npy (uint8), noise_dcg.json (5.4 해석 모델), dcg.json, run.json
#include <exception>
#include <filesystem>
#include <iostream>
#include <string>

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
    "usage: dcg_sim --config <json> --out <dir> [--in <scene.npy>] [--seed N]\n";
}

int main(int argc, char** argv) {
  cctvlog::RunInfo info("dcg_sim", argc, argv);
  try {
    const cctvlog::cli::Args args(argc, argv);
    if (args.has("help")) {
      std::cout << kUsage;
      return 0;
    }
    const fs::path config_path = args.require("config");
    const fs::path out_dir = args.require("out");
    auto params = io::read_json(config_path).get<cctvlog::DcgParams>();
    if (auto s = args.get("seed"); s && !s->empty()) params.seed = std::stoull(*s);

    fs::create_directories(out_dir);
    const json noise = cctvlog::hdr::dcg_noise_model(params);
    io::write_json(out_dir / "noise_dcg.json", noise);

    if (auto in = args.get("in"); in && !in->empty()) {
      const fs::path in_path = *in;
      cctvlog::Image<float> scene;
      {
        cctvlog::ScopedTimer t(info, "load");
        scene = io::read_npy<float>(in_path);
      }
      cctvlog::hdr::DcgResult res;
      {
        cctvlog::ScopedTimer t(info, "dcg_simulate");
        res = cctvlog::hdr::dcg_simulate(scene, params);
      }
      json meta = {{"input", fs::absolute(in_path).lexically_normal().string()},
                   {"scale", "normalized_long_exposure"},
                   {"x_max", noise["x_max"]},
                   {"params", params},
                   {"source_bits",
                    {{"index", "0 = HCG, 1 = LCG"},
                     {"blend", cctvlog::hdr::kSourceBlend},
                     {"saturated", cctvlog::hdr::kSourceSaturated}}}};
      const fs::path side = in_path.parent_path() / "merged.json";
      if (fs::exists(side)) meta["bayer_order"] = io::read_json(side).value("bayer_order", json());
      {
        cctvlog::ScopedTimer t(info, "write");
        io::write_npy(out_dir / "dcg.npy", res.merged);
        io::write_npy(out_dir / "dcg_source.npy", res.source);
        io::write_json(out_dir / "dcg.json", meta);
      }
      info.set("input", fs::absolute(in_path).string());
    }
    info.set_params(params);
    info.set("config_path", fs::absolute(config_path).string());
    info.write(out_dir);
    return 0;
  } catch (const std::invalid_argument& e) {
    std::cerr << "dcg_sim: " << e.what() << '\n' << kUsage;
    return 2;
  } catch (const std::exception& e) {
    std::cerr << "dcg_sim: " << e.what() << '\n';
    return 1;
  }
}
