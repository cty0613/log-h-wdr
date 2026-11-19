# 빌드 시점의 git commit / dirty 상태를 git_info.cpp로 생성한다.
# 내용이 바뀐 경우에만 덮어써서 불필요한 재컴파일을 막는다.
execute_process(
  COMMAND git rev-parse HEAD
  WORKING_DIRECTORY ${SRC_DIR}
  OUTPUT_VARIABLE GIT_COMMIT
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET
  RESULT_VARIABLE GIT_RC)
if(NOT GIT_RC EQUAL 0 OR GIT_COMMIT STREQUAL "")
  set(GIT_COMMIT "unknown")
endif()

execute_process(
  COMMAND git status --porcelain --untracked-files=no
  WORKING_DIRECTORY ${SRC_DIR}
  OUTPUT_VARIABLE GIT_STATUS
  OUTPUT_STRIP_TRAILING_WHITESPACE
  ERROR_QUIET)
if(GIT_STATUS STREQUAL "")
  set(GIT_DIRTY "false")
else()
  set(GIT_DIRTY "true")
endif()

configure_file(${IN_FILE} ${OUT_FILE}.tmp @ONLY)
file(COPY_FILE ${OUT_FILE}.tmp ${OUT_FILE} ONLY_IF_DIFFERENT)
file(REMOVE ${OUT_FILE}.tmp)
