# 모든 CLI 실행 파일을 build/<preset>/apps/<name> 에 둔다
# (Python run_cpp 기본 탐색 경로, CLAUDE.md 4절).
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})

function(cctvlog_add_app name)
  add_executable(${name} ${name}/main.cpp)
  target_link_libraries(${name} PRIVATE cctvlog::core cctvlog_warnings)
endfunction()

# Phase 0: curve_eval 뼈대. 나머지(hdr_merge, dcg_sim, process, encode, decode, restore)는
# 해당 Phase에서 main.cpp를 추가하면서 여기에 등록한다.
cctvlog_add_app(curve_eval)
