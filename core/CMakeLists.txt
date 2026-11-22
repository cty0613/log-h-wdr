add_library(cctvlog_core
  src/io/npy.cpp
  src/io/json_io.cpp
  src/io/run_info.cpp
  src/codec/ffmpeg_info.cpp
  src/hdr/bracket_merge.cpp
  src/hdr/dcg_simulate.cpp
  ${CCTVLOG_GIT_INFO_CPP}
)
add_library(cctvlog::core ALIAS cctvlog_core)
add_dependencies(cctvlog_core cctvlog_git_info)

target_include_directories(cctvlog_core PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)

target_link_libraries(cctvlog_core
  PUBLIC
    nlohmann_json::nlohmann_json
    OpenMP::OpenMP_CXX
    Eigen3::Eigen
  PRIVATE
    cctvlog_warnings
    cctvlog_ffmpeg
    opencv_core
    opencv_imgproc
)
