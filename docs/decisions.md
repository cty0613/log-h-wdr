# 결정 기록

형식: 날짜 / 결정 / 근거(수치) / 버린 대안.

---

## 2026-11-20 — 개발 환경 초기화 (Phase 0 착수 전)

**결정**
- C++ 의존성은 vcpkg manifest(`vcpkg.json`)로 관리, `builtin-baseline = e1ff419d28c7f0a94abaf0a848fe0aeeb69b2de3` (vcpkg master, 2026-11-19).
  - 이 baseline 기준 FFmpeg 포트 버전 9.0.2. 기능: `avcodec`, `avformat`, `x265` (x265가 `gpl`을 끌어온다). swscale은 사용 금지라 빼 두었다.
  - OpenCV4는 `default-features: false` (core·imgproc 모듈은 항상 빌드됨. vcpkg 포트에 `imgproc` feature는 따로 없음).
- triplet은 기본 `x64-linux`(정적 라이브러리). `VCPKG_INSTALLED_DIR = build/vcpkg_installed`로 release/debug preset이 설치본을 공유한다(FFmpeg 중복 빌드 방지).
- git commit 정보는 빌드할 때마다 `cmake/GitInfo.cmake`로 다시 생성 → `run.json`의 `git_commit`, `git_dirty`.
- `run_cpp`의 리스트 인자는 쉼표로 이은 하나의 문자열로 넘긴다(`--bits 8,10,12`). C++ 쪽 `cli::Args`가 같은 규약을 따른다.
- Python 개발 의존성(pytest, ruff)은 `[dev]` extra로 분리 → 설치 명령 `pip install -e ".[dev]"`.

**환경 (PC, WSL2)**
| 항목 | 버전 |
|---|---|
| OS | Ubuntu 26.04.1 LTS (WSL2, kernel 6.18) |
| GCC | 15.2.0 |
| CMake | 4.2.3 |
| Python | 3.14.4 (venv `.venv`, numpy 2.5.5, matplotlib 3.11.2) |
| vcpkg tool | 2026-10-29 |

**vcpkg로 설치된 라이브러리 (`run.json`의 `libraries`에서 확인)**
| 라이브러리 | 버전 |
|---|---|
| FFmpeg | 9.0.2 (libavcodec 63.1.102, libavformat 63.1.102, libavutil 61.1.102) |
| x265 | 4.3 (build 217) |
| OpenCV | 4.14.0 |
| Eigen | 3.5.0 |
| nlohmann_json | 3.12.0 |
| OpenMP (GCC) | 201511 (4.5) |

첫 configure(의존성 소스 빌드) 소요 시간은 403초 (12코어)였다.

**라이선스 메모**: FFmpeg + libx265 정적 링크 → 빌드 결과물은 GPL. 연구용이므로 문제없지만 배포 시 고려.

**버린 대안**
- 시스템 apt FFmpeg/x265: 버전 고정이 안 되어 재현성이 떨어짐.
- vcpkg를 submodule로 포함: 저장소가 무거워짐. `VCPKG_ROOT` 환경변수 방식 채택.
