#pragma once

#include <string_view>

namespace cctvlog {

// 빌드 시 cmake/GitInfo.cmake가 생성하는 git_info.cpp에 정의된다.
std::string_view version();
std::string_view git_commit();
bool git_dirty();

}  // namespace cctvlog
