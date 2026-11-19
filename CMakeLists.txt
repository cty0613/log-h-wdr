cmake_minimum_required(VERSION 3.24)
project(cctvlog VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# CLAUDE.md 4절: Release -O3, -ffast-math 금지
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG")
set(CMAKE_C_FLAGS_RELEASE "-O3 -DNDEBUG")

# --- 의존성 (vcpkg manifest) -------------------------------------------------
find_package(OpenMP REQUIRED COMPONENTS CXX)
find_package(Eigen3 CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)
find_package(OpenCV CONFIG REQUIRED COMPONENTS core imgproc)
find_package(FFMPEG REQUIRED)  # vcpkg wrapper: FFMPEG_INCLUDE_DIRS / FFMPEG_LIBRARIES

add_library(cctvlog_ffmpeg INTERFACE)
target_include_directories(cctvlog_ffmpeg INTERFACE ${FFMPEG_INCLUDE_DIRS})
target_link_directories(cctvlog_ffmpeg INTERFACE ${FFMPEG_LIBRARY_DIRS})
target_link_libraries(cctvlog_ffmpeg INTERFACE ${FFMPEG_LIBRARIES})

# 공통 경고 옵션 (core, apps, tests)
add_library(cctvlog_warnings INTERFACE)
target_compile_options(cctvlog_warnings INTERFACE
  $<$<COMPILE_LANGUAGE:CXX>:-Wall -Wextra -Wpedantic -Wconversion -Wno-sign-conversion>)

# --- git commit 정보: 빌드할 때마다 갱신 (run.json 기록용) ---------------------
set(CCTVLOG_GIT_INFO_CPP ${CMAKE_BINARY_DIR}/generated/git_info.cpp)
add_custom_target(cctvlog_git_info ALL
  COMMAND ${CMAKE_COMMAND}
          -DSRC_DIR=${CMAKE_SOURCE_DIR}
          -DIN_FILE=${CMAKE_SOURCE_DIR}/cmake/git_info.cpp.in
          -DOUT_FILE=${CCTVLOG_GIT_INFO_CPP}
          -DPROJECT_VERSION=${PROJECT_VERSION}
          -P ${CMAKE_SOURCE_DIR}/cmake/GitInfo.cmake
  BYPRODUCTS ${CCTVLOG_GIT_INFO_CPP}
  COMMENT "Updating git_info.cpp")

add_subdirectory(core)
add_subdirectory(apps)

include(CTest)
if(BUILD_TESTING)
  add_subdirectory(tests/cpp)
endif()
