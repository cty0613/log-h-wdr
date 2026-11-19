#include <fstream>
#include <stdexcept>

#include "cctvlog/io.hpp"

namespace cctvlog::io {

json read_json(const std::filesystem::path& path) {
  std::ifstream f(path);
  if (!f) throw std::runtime_error("json: cannot open " + path.string());
  try {
    return json::parse(f);
  } catch (const json::parse_error& e) {
    throw std::runtime_error("json " + path.string() + ": " + e.what());
  }
}

void write_json(const std::filesystem::path& path, const json& j, int indent) {
  std::ofstream f(path);
  if (!f) throw std::runtime_error("json: cannot open for writing " + path.string());
  f << j.dump(indent) << '\n';
}

std::vector<json> read_jsonl(const std::filesystem::path& path) {
  std::ifstream f(path);
  if (!f) throw std::runtime_error("jsonl: cannot open " + path.string());
  std::vector<json> out;
  std::string line;
  std::size_t lineno = 0;
  while (std::getline(f, line)) {
    ++lineno;
    if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
    try {
      out.push_back(json::parse(line));
    } catch (const json::parse_error& e) {
      throw std::runtime_error("jsonl " + path.string() + ":" + std::to_string(lineno) + ": " +
                               e.what());
    }
  }
  return out;
}

void append_jsonl(const std::filesystem::path& path, const json& j) {
  std::ofstream f(path, std::ios::app);
  if (!f) throw std::runtime_error("jsonl: cannot open for append " + path.string());
  f << j.dump() << '\n';
}

}  // namespace cctvlog::io
