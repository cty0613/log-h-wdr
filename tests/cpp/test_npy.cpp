#include <gtest/gtest.h>

#include <cstring>
#include <filesystem>
#include <fstream>
#include <limits>

#include "cctvlog/io.hpp"

namespace fs = std::filesystem;
using cctvlog::Image;
namespace io = cctvlog::io;

namespace {

fs::path tmp_file(const std::string& name) {
  const fs::path dir = fs::temp_directory_path() / "cctvlog_tests";
  fs::create_directories(dir);
  return dir / name;
}

}  // namespace

TEST(Npy, Uint16RoundTripBitExact) {
  Image<std::uint16_t> img(2304, 4);
  for (int y = 0; y < img.height(); ++y)
    for (int x = 0; x < img.width(); ++x) img.at(x, y) = static_cast<std::uint16_t>((x * 31 + y * 7) & 0xFFFF);
  img.at(0, 0) = 0;
  img.at(1, 0) = std::numeric_limits<std::uint16_t>::max();

  const auto p = tmp_file("u16.npy");
  io::write_npy(p, img);
  const auto back = io::read_npy<std::uint16_t>(p);
  ASSERT_EQ(back.width(), img.width());
  ASSERT_EQ(back.height(), img.height());
  ASSERT_EQ(back.channels(), 1);
  EXPECT_EQ(std::memcmp(back.data(), img.data(), img.stride() * img.height() * 2), 0);

  const auto h = io::read_npy_header(p);
  EXPECT_EQ(h.descr, "<u2");
  EXPECT_FALSE(h.fortran_order);
  EXPECT_EQ(h.shape, (std::vector<std::size_t>{4, 2304}));
  // 헤더는 64바이트 정렬 (NumPy format spec)
  EXPECT_EQ((fs::file_size(p) - img.stride() * img.height() * 2) % 64, 0u);
}

TEST(Npy, Float32ThreeChannelRoundTrip) {
  Image<float> img(3, 2, 3);
  for (int y = 0; y < 2; ++y)
    for (int x = 0; x < 3; ++x)
      for (int c = 0; c < 3; ++c) img.at(x, y, c) = 0.1f * static_cast<float>(x + 10 * y + 100 * c);
  img.at(0, 0, 0) = std::numeric_limits<float>::denorm_min();
  img.at(1, 0, 0) = 16.0f;

  const auto p = tmp_file("f32c3.npy");
  io::write_npy(p, img);
  const auto back = io::read_npy<float>(p);
  ASSERT_EQ(back.channels(), 3);
  EXPECT_EQ(std::memcmp(back.data(), img.data(), sizeof(float) * 18), 0);
  EXPECT_EQ(io::read_npy_header(p).shape, (std::vector<std::size_t>{2, 3, 3}));
}

TEST(Npy, WritesPaddedStrideWithoutPadding) {
  Image<std::uint16_t> img(3, 2, 1, 8);
  for (int y = 0; y < 2; ++y)
    for (int x = 0; x < 3; ++x) img.at(x, y) = static_cast<std::uint16_t>(x + 3 * y);
  const auto p = tmp_file("stride.npy");
  io::write_npy(p, img);
  const auto back = io::read_npy<std::uint16_t>(p);
  EXPECT_TRUE(back.contiguous());
  for (int y = 0; y < 2; ++y)
    for (int x = 0; x < 3; ++x) EXPECT_EQ(back.at(x, y), x + 3 * y);
}

TEST(Npy, DtypeMismatchThrows) {
  Image<float> img(2, 2);
  const auto p = tmp_file("f32.npy");
  io::write_npy(p, img);
  EXPECT_THROW(io::read_npy<std::uint16_t>(p), std::runtime_error);
}

TEST(Npy, ParsesNumpyStyleHeader) {
  // np.save가 쓰는 형태(1차원 축 뒤 공백 패딩 포함)를 직접 구성
  std::string dict = "{'descr': '<u2', 'fortran_order': False, 'shape': (2, 3), }";
  dict.append(64 - (10 + dict.size() + 1) % 64, ' ');
  dict.push_back('\n');
  const auto p = tmp_file("np_style.npy");
  {
    std::ofstream f(p, std::ios::binary);
    f.write("\x93NUMPY\x01\x00", 8);
    const auto len = static_cast<std::uint16_t>(dict.size());
    f.write(reinterpret_cast<const char*>(&len), 2);
    f.write(dict.data(), static_cast<std::streamsize>(dict.size()));
    const std::uint16_t vals[6] = {0, 1, 2, 1021, 1022, 1023};
    f.write(reinterpret_cast<const char*>(vals), sizeof(vals));
  }
  const auto img = io::read_npy<std::uint16_t>(p);
  EXPECT_EQ(img.width(), 3);
  EXPECT_EQ(img.height(), 2);
  EXPECT_EQ(img.at(2, 1), 1023);
}

TEST(Jsonl, AppendAndRead) {
  const auto p = tmp_file("side.jsonl");
  fs::remove(p);
  io::append_jsonl(p, {{"frame_index", 0}, {"exposure_time_us", nullptr}});
  io::append_jsonl(p, {{"frame_index", 1}});
  const auto rows = io::read_jsonl(p);
  ASSERT_EQ(rows.size(), 2u);
  EXPECT_TRUE(rows[0]["exposure_time_us"].is_null());
  EXPECT_EQ(rows[1]["frame_index"], 1);
}
