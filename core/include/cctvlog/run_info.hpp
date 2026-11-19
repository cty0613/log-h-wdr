#pragma once

#include <chrono>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace cctvlog {

// 모든 CLI가 출력 폴더에 남기는 run.json (CLAUDE.md 1절):
// 버전, git commit, 파라미터, 처리 시간(블록별), 라이브러리 버전.
class RunInfo {
 public:
  RunInfo(std::string app, int argc, char** argv);

  void set_params(nlohmann::json params) { params_ = std::move(params); }
  void set(const std::string& key, nlohmann::json value) { extra_[key] = std::move(value); }
  // 블록별 처리 시간 누적 (ms)
  void add_timing_ms(const std::string& block, double ms) { timings_ms_[block] += ms; }

  nlohmann::json to_json() const;
  // out_dir/run.json 작성 (out_dir이 없으면 생성)
  void write(const std::filesystem::path& out_dir) const;

 private:
  std::string app_;
  std::vector<std::string> argv_;
  std::chrono::system_clock::time_point started_wall_;
  std::chrono::steady_clock::time_point started_;
  nlohmann::json params_ = nlohmann::json::object();
  nlohmann::json extra_ = nlohmann::json::object();
  std::map<std::string, double> timings_ms_;
};

// 범위 시간 측정 → RunInfo::add_timing_ms
class ScopedTimer {
 public:
  ScopedTimer(RunInfo& info, std::string block)
      : info_(info), block_(std::move(block)), t0_(std::chrono::steady_clock::now()) {}
  ~ScopedTimer() {
    const auto dt = std::chrono::steady_clock::now() - t0_;
    info_.add_timing_ms(block_, std::chrono::duration<double, std::milli>(dt).count());
  }
  ScopedTimer(const ScopedTimer&) = delete;
  ScopedTimer& operator=(const ScopedTimer&) = delete;

 private:
  RunInfo& info_;
  std::string block_;
  std::chrono::steady_clock::time_point t0_;
};

// 링크된 외부 라이브러리 버전 (FFmpeg, x265, OpenCV, Eigen, nlohmann_json, OpenMP)
nlohmann::json library_versions();

}  // namespace cctvlog
