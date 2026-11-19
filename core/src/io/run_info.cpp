#include "cctvlog/run_info.hpp"

#include <ctime>
#include <iomanip>
#include <sstream>

#include <Eigen/Core>
#include <opencv2/core/version.hpp>

#include "cctvlog/codec.hpp"
#include "cctvlog/io.hpp"
#include "cctvlog/version.hpp"

#ifdef _OPENMP
#include <omp.h>
#endif

namespace cctvlog {

namespace {

std::string iso8601_utc(std::chrono::system_clock::time_point tp) {
  const std::time_t t = std::chrono::system_clock::to_time_t(tp);
  std::tm tm{};
  gmtime_r(&t, &tm);
  std::ostringstream os;
  os << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
  return os.str();
}

}  // namespace

RunInfo::RunInfo(std::string app, int argc, char** argv)
    : app_(std::move(app)),
      started_wall_(std::chrono::system_clock::now()),
      started_(std::chrono::steady_clock::now()) {
  for (int i = 0; i < argc; ++i) argv_.emplace_back(argv[i]);
}

nlohmann::json RunInfo::to_json() const {
  const double total_ms =
      std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started_)
          .count();
  nlohmann::json j;
  j["app"] = app_;
  j["version"] = std::string(version());
  j["git_commit"] = std::string(git_commit());
  j["git_dirty"] = git_dirty();
  j["argv"] = argv_;
  j["started_utc"] = iso8601_utc(started_wall_);
  j["params"] = params_;
  j["timings_ms"] = timings_ms_;
  j["total_ms"] = total_ms;
#ifdef _OPENMP
  j["omp_max_threads"] = omp_get_max_threads();
#else
  j["omp_max_threads"] = nullptr;
#endif
  j["libraries"] = library_versions();
  for (const auto& [k, v] : extra_.items()) j[k] = v;
  return j;
}

void RunInfo::write(const std::filesystem::path& out_dir) const {
  std::filesystem::create_directories(out_dir);
  io::write_json(out_dir / "run.json", to_json());
}

nlohmann::json library_versions() {
  nlohmann::json j = codec::ffmpeg_versions();
  j["opencv"] = CV_VERSION;
  j["eigen"] = std::to_string(EIGEN_WORLD_VERSION) + "." + std::to_string(EIGEN_MAJOR_VERSION) +
               "." + std::to_string(EIGEN_MINOR_VERSION);
  j["nlohmann_json"] = std::to_string(NLOHMANN_JSON_VERSION_MAJOR) + "." +
                       std::to_string(NLOHMANN_JSON_VERSION_MINOR) + "." +
                       std::to_string(NLOHMANN_JSON_VERSION_PATCH);
#ifdef _OPENMP
  j["openmp"] = _OPENMP;
#endif
  return j;
}

}  // namespace cctvlog
