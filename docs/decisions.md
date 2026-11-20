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

---

## 2026-11-20 — npy C++/Python 비트 동일성 검증 (Phase 0 수락 기준)

**결과: 통과.** `tests/py/test_npy_crosscheck.py` 15개 케이스.
- NumPy `np.save` → C++ `read_npy`가 읽은 버퍼 == `arr.tobytes()`, C++ `write_npy` → `np.load` == 원본. 두 방향 모두 바이트 단위로 비교.
- 케이스: uint16 2304×1296(전 범위, 10bit RAW), 1×1, float32 3채널, 특수 float(±0, ±inf, NaN payload, subnormal, max), float32 HDR Bayer(0~16), uint8 3채널. npy format v1/v2/v3, 비연속 원본 배열.
- 거부 확인: big-endian, int16, 1차원, Fortran order는 오류로 끝난다.
- 민감도 확인: C++ 로더에 1비트 변형을 넣으면 10개 케이스가 실패한다(높이 1인 케이스와 거부 케이스는 이 변형과 무관).

**결정**
- 교차 검증용 C++ 도구 `npy_roundtrip`은 `tests/cpp/tools/`에 두고 `build/<preset>/tests/bin`에 빌드한다(apps 목록과 분리).
- `run_cpp(..., directory=...)`로 호출 폴더를 바꿀 수 있게 했다(`cli.tools_bin_dir()`, 환경변수 `CCTVLOG_TEST_BIN`). C++ 호출 경로는 여전히 `run_cpp` 하나뿐이다.
- `run_cpp` 키워드 인자 끝의 `_`는 떼고 플래그를 만든다(`in_=` → `--in`).

**남은 일**: Pi 실데이터(`data/pi_probe/*/sample/000000.npy`)로도 같은 검증을 1회 수행한다.

---

## 2026-11-20 — Pi 1차 확인 결과에 따른 결정 (`data/pi_probe/20261120/`, 상세: `pi_notes.md`)

**사실**
- 카메라는 **Camera Module 3 Wide (`imx708_wide`)**, `Rotation = 180`.
- 비압축 RAW: `raw={"format": "SRGGB10"}`로 요청하면 실제 포맷은 `SBGGR16`(stride 4608)이다. MSB 정렬이 확인되어 `>> 6`.
- 실제 Bayer 순서는 `BGGR`로, `sensor_modes`의 `SRGGB`와 다르다.
- 블랙 레벨은 16bit 스케일 4096 → 10bit 64.
- 노출 변경은 5프레임 뒤에 반영되고, 실제 노출은 요청보다 −0.07% 작다.
- 센서 HDR 모드는 2304×1296 @30fps 하나뿐이다.

**결정**
- 튜닝 값은 `pisp/imx708_wide.json`에서 가져온다(`tuning_import.py` 기본 입력). `imx708.json`은 CCM ct 개수와 값, ALSC luminance_lut 최대값(3.04 vs 4.72)이 달라서 쓰지 않는다.
- `capture_raw.py`는 `raw={"format": mode["unpacked"]}`로 요청하고, Bayer 순서는 `camera_configuration()["raw"]["format"]`에서 읽어 프레임 JSON에 저장한다. 실제 포맷이 `*16`이 아니면 촬영을 중단한다(압축 포맷 방지).
- 프레임 JSON의 `black_level`은 `SensorBlackLevels >> (16 − bit_depth)`로 계산해 기록한다(메타데이터에서 유도하는 값이므로 추정이 아님). `white_level`은 2차 확인 전까지 `null`.
- 브래킷 촬영은 `set_controls` 후 최소 5프레임을 버린다. 그 뒤 프레임의 메타데이터 노출이 설정값과 2% 안에 들어오는지 확인한다.

**보류**: 수동 게인 1.0 요청 → 메타데이터 1.1228. 원인을 확인하기 전까지 Phase 1 PTC 촬영 스크립트를 확정하지 않는다(2차 확인 `manual_gain`).
