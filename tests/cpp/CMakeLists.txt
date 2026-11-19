find_package(GTest CONFIG REQUIRED)
include(GoogleTest)

add_executable(cctvlog_tests
  test_image.cpp
  test_npy.cpp
  test_codec_env.cpp
)
target_link_libraries(cctvlog_tests PRIVATE
  cctvlog::core cctvlog_warnings GTest::gtest GTest::gtest_main)
gtest_discover_tests(cctvlog_tests WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})
