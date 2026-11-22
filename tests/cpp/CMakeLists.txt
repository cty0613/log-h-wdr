find_package(GTest CONFIG REQUIRED)
include(GoogleTest)

add_executable(cctvlog_tests
  test_image.cpp
  test_npy.cpp
  test_codec_env.cpp
  test_random.cpp
  test_bracket_merge.cpp
  test_dcg.cpp
  test_curves.cpp
)
target_link_libraries(cctvlog_tests PRIVATE
  cctvlog::core cctvlog_warnings GTest::gtest GTest::gtest_main)
gtest_discover_tests(cctvlog_tests WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})

# Python 교차 검증용 테스트 도구 → build/<preset>/tests/bin (pytest가 사용)
add_executable(npy_roundtrip tools/npy_roundtrip.cpp)
target_link_libraries(npy_roundtrip PRIVATE cctvlog::core cctvlog_warnings)
set_target_properties(npy_roundtrip PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/tests/bin)
