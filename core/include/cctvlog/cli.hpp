#pragma once

#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace cctvlog::cli {

// 최소 인자 파서: `--key value`, `--flag`(값 없음), 위치 인자.
// 리스트 값은 Python run_cpp 규약에 따라 쉼표로 구분된 하나의 문자열로 받는다.
class Args {
 public:
  Args(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
      std::string a = argv[i];
      if (a.rfind("--", 0) == 0 && a.size() > 2) {
        std::string key = a.substr(2);
        if (auto eq = key.find('='); eq != std::string::npos) {
          named_[key.substr(0, eq)] = key.substr(eq + 1);
        } else if (i + 1 < argc && std::string(argv[i + 1]).rfind("--", 0) != 0) {
          named_[key] = argv[++i];
        } else {
          named_[key] = "";  // flag
        }
      } else {
        positional_.push_back(std::move(a));
      }
    }
  }

  bool has(const std::string& key) const { return named_.count(key) != 0; }

  std::optional<std::string> get(const std::string& key) const {
    auto it = named_.find(key);
    if (it == named_.end()) return std::nullopt;
    return it->second;
  }

  std::string require(const std::string& key) const {
    auto v = get(key);
    if (!v || v->empty()) throw std::invalid_argument("missing required argument --" + key);
    return *v;
  }

  const std::vector<std::string>& positional() const { return positional_; }
  const std::map<std::string, std::string>& named() const { return named_; }

 private:
  std::map<std::string, std::string> named_;
  std::vector<std::string> positional_;
};

}  // namespace cctvlog::cli
