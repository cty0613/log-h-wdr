// 테스트 전용 도구: C++ npy 로더/라이터를 NumPy와 비트 단위로 비교하기 위한 것 (Phase 0 수락 기준).
//
// npy_roundtrip --in X.npy --raw-out X.bin --npy-out Y.npy
//   - X.npy를 cctvlog::io::read_npy로 읽고
//   - 읽은 화소 버퍼를 헤더 없이 X.bin에 그대로 쓰고 (NumPy arr.tobytes()와 비교)
//   - 같은 이미지를 cctvlog::io::write_npy로 Y.npy에 쓴다 (np.load 결과와 비교)
//   - stdout에 {"descr", "shape", "width", "height", "channels"} JSON 한 줄
#include <cstdint>
#include <exception>
#include <fstream>
#include <iostream>

#include "cctvlog/cli.hpp"
#include "cctvlog/io.hpp"

namespace fs = std::filesystem;
namespace io = cctvlog::io;

namespace {

template <typename T>
nlohmann::json roundtrip(const fs::path& in, const fs::path& raw_out, const fs::path& npy_out) {
  const auto img = io::read_npy<T>(in);
  {
    std::ofstream f(raw_out, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + raw_out.string());
    const auto row_bytes = static_cast<std::streamsize>(static_cast<std::size_t>(img.width()) *
                                                        img.channels() * sizeof(T));
    for (int y = 0; y < img.height(); ++y) {
      f.write(reinterpret_cast<const char*>(img.row(y)), row_bytes);
    }
    if (!f) throw std::runtime_error("write failed " + raw_out.string());
  }
  io::write_npy(npy_out, img);
  return {{"width", img.width()}, {"height", img.height()}, {"channels", img.channels()}};
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const cctvlog::cli::Args args(argc, argv);
    const fs::path in = args.require("in");
    const fs::path raw_out = args.require("raw-out");
    const fs::path npy_out = args.require("npy-out");

    const auto header = io::read_npy_header(in);
    nlohmann::json meta;
    if (header.descr == "|u1") {
      meta = roundtrip<std::uint8_t>(in, raw_out, npy_out);
    } else if (header.descr == "<u2") {
      meta = roundtrip<std::uint16_t>(in, raw_out, npy_out);
    } else if (header.descr == "<f4") {
      meta = roundtrip<float>(in, raw_out, npy_out);
    } else {
      throw std::runtime_error("unsupported dtype " + header.descr);
    }
    meta["descr"] = header.descr;
    meta["shape"] = header.shape;
    std::cout << meta.dump() << '\n';
    return 0;
  } catch (const std::invalid_argument& e) {
    std::cerr << "npy_roundtrip: " << e.what() << '\n';
    return 2;
  } catch (const std::exception& e) {
    std::cerr << "npy_roundtrip: " << e.what() << '\n';
    return 1;
  }
}
