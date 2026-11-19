#include <bit>
#include <cstring>
#include <fstream>
#include <regex>
#include <sstream>
#include <stdexcept>

#include "cctvlog/io.hpp"

namespace cctvlog::io {

static_assert(std::endian::native == std::endian::little, "npy I/O assumes little-endian host");

namespace {

constexpr char kMagic[] = "\x93NUMPY";
constexpr std::size_t kMagicLen = 6;
constexpr std::size_t kAlign = 64;

template <typename T>
constexpr const char* dtype_descr();
template <>
constexpr const char* dtype_descr<std::uint8_t>() { return "|u1"; }
template <>
constexpr const char* dtype_descr<std::uint16_t>() { return "<u2"; }
template <>
constexpr const char* dtype_descr<float>() { return "<f4"; }

[[noreturn]] void fail(const std::filesystem::path& p, const std::string& msg) {
  throw std::runtime_error("npy " + p.string() + ": " + msg);
}

NpyHeader parse_header_dict(const std::filesystem::path& path, const std::string& h) {
  NpyHeader out;
  std::smatch m;
  static const std::regex re_descr(R"('descr'\s*:\s*'([^']+)')");
  static const std::regex re_fortran(R"('fortran_order'\s*:\s*(True|False))");
  static const std::regex re_shape(R"('shape'\s*:\s*\(([^)]*)\))");
  if (!std::regex_search(h, m, re_descr)) fail(path, "missing descr");
  out.descr = m[1];
  if (!std::regex_search(h, m, re_fortran)) fail(path, "missing fortran_order");
  out.fortran_order = (m[1] == "True");
  if (!std::regex_search(h, m, re_shape)) fail(path, "missing shape");
  std::stringstream ss(m[1]);
  std::string tok;
  while (std::getline(ss, tok, ',')) {
    const auto b = tok.find_first_not_of(" \t");
    if (b == std::string::npos) continue;
    out.shape.push_back(static_cast<std::size_t>(std::stoull(tok.substr(b))));
  }
  return out;
}

// 파일을 열고 헤더를 읽은 뒤 데이터 시작 위치에 둔다.
NpyHeader open_and_read_header(const std::filesystem::path& path, std::ifstream& f) {
  f.open(path, std::ios::binary);
  if (!f) fail(path, "cannot open");
  char magic[kMagicLen];
  f.read(magic, kMagicLen);
  if (!f || std::memcmp(magic, kMagic, kMagicLen) != 0) fail(path, "bad magic");
  unsigned char ver[2];
  f.read(reinterpret_cast<char*>(ver), 2);
  std::uint32_t hlen = 0;
  if (ver[0] == 1) {
    std::uint16_t l16 = 0;
    f.read(reinterpret_cast<char*>(&l16), 2);
    hlen = l16;
  } else if (ver[0] == 2 || ver[0] == 3) {
    f.read(reinterpret_cast<char*>(&hlen), 4);
  } else {
    fail(path, "unsupported version " + std::to_string(ver[0]));
  }
  std::string h(hlen, '\0');
  f.read(h.data(), hlen);
  if (!f) fail(path, "truncated header");
  return parse_header_dict(path, h);
}

}  // namespace

NpyHeader read_npy_header(const std::filesystem::path& path) {
  std::ifstream f;
  return open_and_read_header(path, f);
}

template <typename T>
Image<T> read_npy(const std::filesystem::path& path) {
  std::ifstream f;
  const NpyHeader h = open_and_read_header(path, f);
  if (h.descr != dtype_descr<T>()) {
    fail(path, "dtype mismatch: file " + h.descr + ", expected " + dtype_descr<T>());
  }
  if (h.fortran_order) fail(path, "fortran_order not supported");
  if (h.shape.size() != 2 && h.shape.size() != 3) fail(path, "expected 2D or 3D array");
  const int height = static_cast<int>(h.shape[0]);
  const int width = static_cast<int>(h.shape[1]);
  const int channels = h.shape.size() == 3 ? static_cast<int>(h.shape[2]) : 1;
  Image<T> img(width, height, channels);
  const auto bytes = static_cast<std::streamsize>(img.stride() * height * sizeof(T));
  f.read(reinterpret_cast<char*>(img.data()), bytes);
  if (f.gcount() != bytes) fail(path, "truncated data");
  return img;
}

template <typename T>
void write_npy(const std::filesystem::path& path, const Image<T>& img) {
  std::string dict = std::string("{'descr': '") + dtype_descr<T>() +
                     "', 'fortran_order': False, 'shape': (" + std::to_string(img.height()) +
                     ", " + std::to_string(img.width());
  if (img.channels() > 1) dict += ", " + std::to_string(img.channels());
  dict += "), }";

  // 전체 헤더(magic + ver + len + dict + '\n')를 64바이트 정렬
  std::size_t prefix = kMagicLen + 2 + 2;
  unsigned char major = 1;
  if (dict.size() + 1 + prefix > 65535) {
    prefix = kMagicLen + 2 + 4;
    major = 2;
  }
  const std::size_t total = prefix + dict.size() + 1;
  dict.append((kAlign - total % kAlign) % kAlign, ' ');
  dict.push_back('\n');

  std::ofstream f(path, std::ios::binary);
  if (!f) fail(path, "cannot open for writing");
  f.write(kMagic, kMagicLen);
  const unsigned char ver[2] = {major, 0};
  f.write(reinterpret_cast<const char*>(ver), 2);
  if (major == 1) {
    const auto l16 = static_cast<std::uint16_t>(dict.size());
    f.write(reinterpret_cast<const char*>(&l16), 2);
  } else {
    const auto l32 = static_cast<std::uint32_t>(dict.size());
    f.write(reinterpret_cast<const char*>(&l32), 4);
  }
  f.write(dict.data(), static_cast<std::streamsize>(dict.size()));
  const auto row_bytes =
      static_cast<std::streamsize>(static_cast<std::size_t>(img.width()) * img.channels() * sizeof(T));
  for (int y = 0; y < img.height(); ++y) {
    f.write(reinterpret_cast<const char*>(img.row(y)), row_bytes);
  }
  if (!f) fail(path, "write failed");
}

template Image<std::uint8_t> read_npy<std::uint8_t>(const std::filesystem::path&);
template Image<std::uint16_t> read_npy<std::uint16_t>(const std::filesystem::path&);
template Image<float> read_npy<float>(const std::filesystem::path&);
template void write_npy<std::uint8_t>(const std::filesystem::path&, const Image<std::uint8_t>&);
template void write_npy<std::uint16_t>(const std::filesystem::path&, const Image<std::uint16_t>&);
template void write_npy<float>(const std::filesystem::path&, const Image<float>&);

}  // namespace cctvlog::io
