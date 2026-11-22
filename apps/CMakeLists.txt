# 모든 CLI 실행 파일을 build/<preset>/apps/<name> 에 둔다
# (Python run_cpp 기본 탐색 경로, CLAUDE.md 4절).
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR})

function(cctvlog_add_app name)
  add_executable(${name} ${name}/main.cpp)
  target_link_libraries(${name} PRIVATE cctvlog::core cctvlog_warnings)
endfunction()

# 나머지(process, encode, decode, restore)는 해당 Phase에서 main.cpp를 추가하면서 등록한다.
cctvlog_add_app(curve_eval)  # Phase 0 뼈대, Phase 3에서 구현
cctvlog_add_app(hdr_merge)   # Phase 2
cctvlog_add_app(dcg_sim)     # Phase 2
