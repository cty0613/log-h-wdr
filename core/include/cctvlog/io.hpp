#pragma once

#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "cctvlog/image.hpp"

namespace cctvlog::io {

using json = nlohmann::json;

// ---- .npy (NumPy format v1/v2/v3, little-endian, C-order) ----------------------
// 지원 dtype: uint8('|u1'), uint16('<u2'), float32('<f4').
// shape (H, W) → channels=1, (H, W, C) → channels=C.

struct NpyHeader {
  std::string descr;
  bool fortran_order = false;
  std::vector<std::size_t> shape;
};

NpyHeader read_npy_header(const std::filesystem::path& path);

template <typename T>
Image<T> read_npy(const std::filesystem::path& path);

template <typename T>
void write_npy(const std::filesystem::path& path, const Image<T>& img);

// ---- JSON / JSONL ---------------------------------------------------------------
json read_json(const std::filesystem::path& path);
void write_json(const std::filesystem::path& path, const json& j, int indent = 2);

std::vector<json> read_jsonl(const std::filesystem::path& path);
void append_jsonl(const std::filesystem::path& path, const json& j);

}  // namespace cctvlog::io
