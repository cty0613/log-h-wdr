#pragma once

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace cctvlog {

// row-major, interleaved, 명시적 stride(원소 단위) 이미지 (CLAUDE.md 5.3).
template <typename T>
class Image {
 public:
  using value_type = T;

  Image() = default;

  Image(int width, int height, int channels = 1)
      : Image(width, height, channels, static_cast<std::size_t>(width) * channels) {}

  Image(int width, int height, int channels, std::size_t stride)
      : width_(width), height_(height), channels_(channels), stride_(stride) {
    if (width < 0 || height < 0 || channels <= 0) {
      throw std::invalid_argument("Image: invalid dimensions");
    }
    if (stride < static_cast<std::size_t>(width) * channels) {
      throw std::invalid_argument("Image: stride smaller than width * channels");
    }
    data_.assign(stride_ * static_cast<std::size_t>(height_), T{});
  }

  int width() const { return width_; }
  int height() const { return height_; }
  int channels() const { return channels_; }
  // 한 행의 원소 수 (>= width * channels)
  std::size_t stride() const { return stride_; }
  bool empty() const { return data_.empty(); }
  // 패딩이 없어 전체가 하나의 연속 버퍼인지
  bool contiguous() const { return stride_ == static_cast<std::size_t>(width_) * channels_; }

  T* row(int y) {
    assert(y >= 0 && y < height_);
    return data_.data() + static_cast<std::size_t>(y) * stride_;
  }
  const T* row(int y) const {
    assert(y >= 0 && y < height_);
    return data_.data() + static_cast<std::size_t>(y) * stride_;
  }

  T& at(int x, int y, int c = 0) {
    assert(x >= 0 && x < width_ && c >= 0 && c < channels_);
    return row(y)[static_cast<std::size_t>(x) * channels_ + c];
  }
  const T& at(int x, int y, int c = 0) const {
    assert(x >= 0 && x < width_ && c >= 0 && c < channels_);
    return row(y)[static_cast<std::size_t>(x) * channels_ + c];
  }

  T* data() { return data_.data(); }
  const T* data() const { return data_.data(); }

 private:
  int width_ = 0;
  int height_ = 0;
  int channels_ = 1;
  std::size_t stride_ = 0;
  std::vector<T> data_;
};

// Bayer 패턴: 좌상단 2x2 블록의 색 순서. 하드코딩 금지 — 캡처 JSON에서 읽는다 (CLAUDE.md 2절).
enum class BayerOrder { RGGB, GRBG, GBRG, BGGR };

inline BayerOrder parse_bayer_order(std::string_view s) {
  if (s == "RGGB") return BayerOrder::RGGB;
  if (s == "GRBG") return BayerOrder::GRBG;
  if (s == "GBRG") return BayerOrder::GBRG;
  if (s == "BGGR") return BayerOrder::BGGR;
  throw std::invalid_argument("unknown bayer_order: " + std::string(s));
}

inline std::string_view to_string(BayerOrder o) {
  switch (o) {
    case BayerOrder::RGGB: return "RGGB";
    case BayerOrder::GRBG: return "GRBG";
    case BayerOrder::GBRG: return "GBRG";
    case BayerOrder::BGGR: return "BGGR";
  }
  return "?";
}

// 1채널 Bayer 모자이크 + 패턴 순서.
template <typename T>
struct BayerImage {
  Image<T> image;
  BayerOrder order = BayerOrder::RGGB;
};

}  // namespace cctvlog
