// curve_eval: 램프 입력 → encode → quantize(bits) → decode → 결과 npy (Phase 3).
// Phase 0: 인자 파싱·config 로드·run.json 기록만 하는 뼈대.
//
// 사용법: curve_eval --config <json> --out <dir>
#include <exception>
#include <filesystem>
#include <iostream>

#include "cctvlog/cli.hpp"
#include "cctvlog/io.hpp"
#include "cctvlog/run_info.hpp"

namespace fs = std::filesystem;

int main(int argc, char** argv) {
  cctvlog::RunInfo info("curve_eval", argc, argv);
  try {
    const cctvlog::cli::Args args(argc, argv);
    if (args.has("help")) {
      std::cout << "usage: curve_eval --config <json> --out <dir>\n";
      return 0;
    }
    const fs::path config_path = args.require("config");
    const fs::path out_dir = args.require("out");

    nlohmann::json config;
    {
      cctvlog::ScopedTimer t(info, "load_config");
      config = cctvlog::io::read_json(config_path);
    }
    info.set_params(config);
    info.set("config_path", fs::absolute(config_path).string());
    info.set("status", "stub");  // Phase 3에서 실제 처리로 교체

    info.write(out_dir);
    return 0;
  } catch (const std::invalid_argument& e) {
    std::cerr << "curve_eval: " << e.what() << "\nusage: curve_eval --config <json> --out <dir>\n";
    return 2;
  } catch (const std::exception& e) {
    std::cerr << "curve_eval: " << e.what() << '\n';
    return 1;
  }
}
