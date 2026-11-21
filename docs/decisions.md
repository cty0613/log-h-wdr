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

---

## 2026-11-20 — Pi 원격 작업 자동화 (`scripts/pi.sh`)

**결정**
- PC(WSL)에서 SSH로 Pi에 접속해 `capture/` 동기화 → 실행 → `data/`로 회수하는 일을 Bash 스크립트 하나로 한다. 접속 정보는 `.pi.env`(git 제외)에 둔다.
- 전송: 양쪽에 rsync가 있으면 rsync, 없으면 `tar | ssh`.
- SSH ControlMaster를 10분 유지해서, 여러 번 호출해도 접속을 다시 맺지 않는다.
- `job NAME`의 결과는 Pi의 `$PI_ROOT/runs/NAME` → PC의 `data/pi_runs/NAME`(또는 `--dest`)으로 간다. 같은 이름이 있으면 거부해 덮어쓰기를 막는다.
- 회수한 폴더에 `pi_job.json`(스크립트, 인자, 시각, 종료 코드, git commit, capture/ 수정 여부)을 남긴다.
- capture/ 스크립트는 모두 `--out DIR` 인자를 받는다(`job` 규약).

**버린 대안**
- Claude Code hook으로 capture/ 수정 때마다 자동 동기화: `run`/`job`이 실행 직전에 항상 동기화하므로 중복이다. 또 Pi가 꺼져 있으면 편집할 때마다 오류가 난다.
- Python(paramiko/fabric): Pi와 PC 모두에 의존성이 늘어난다. ssh/rsync만으로 충분하다.

**검증**: 가짜 ssh shim(명령을 임시 원격 홈에서 실행)으로 확인했다. sync/run/job/probe/fetch, rsync와 tar 경로, 인자 인용(공백 포함), 잘못된 스크립트·작업 이름 거부, 중복 결과 거부. 실제 Pi 접속은 아직 하지 않았다.

---

## 2026-11-20 — Pi 2차 확인 결과에 따른 결정 (`data/pi_probe/20261120_2/`)

**사실**
- 수동 게인 2, 4, 8, 16은 요청값 그대로 적용된다. 1.0은 1.1228070259로 하한에 걸린다. 이 값은 `1024/(1024−112)`의 float32 값과 정확히 같다(센서 최소 게인 code 112로 판단, v4l2 확인 대기).
- `AeEnable=False`와 `ExposureTimeMode/AnalogueGainMode=Manual`의 결과는 같고, config와 `set_controls` 적용도 차이가 없다.
- 화이트 레벨은 10bit 1023이다. 포화는 1023에서 깨끗하게 잘린다.
- 튜닝 파일 `pisp/imx708_wide.json` 로드를 확인했다.

**결정**
- **Phase 1 PTC의 "게인 1"은 센서 최소 게인 1.1228로 촬영한다.** 게인 집합은 {1.1228(최소), 4, 16}. 1.0은 물리적으로 설정할 수 없다. 노이즈 모델 JSON과 리포트에는 메타데이터의 실제 게인을 쓰고, "gain 1"이라는 이름 대신 실제 값으로 표기한다.
  - 버린 대안: 디지털 게인으로 1.0 맞추기. RAW는 ISP 전 단계라 해당하지 않고, 맞춘다 해도 노이즈 특성은 그대로다.
- 수동 제어는 `ExposureTimeMode=1, AnalogueGainMode=1, AwbEnable=False`로 지정한다(libcamera 0.5+ 방식). `AeEnable`에 의존하지 않는다.
- `AfMode=0`(Manual)과 `LensPosition`을 고정하고 프레임 JSON `notes`에 기록한다. `HdrMode`(ISP HDR)는 항상 0으로 둔다.
- 프레임 JSON `white_level = 1023`(10bit 모드). 모드의 `bit_depth`에서 `2^bit_depth − 1`로 쓴다. 포화가 1023에서 잘리는 것을 실측했으므로 추정이 아니다.
- 정규화 분모는 `white − black = 959`(5.3절).

---

## 2026-11-20 — Phase 0 완료: 수락 기준 결과

| 기준 | 결과 | 근거 |
|---|---|---|
| 브래킷 실제 노출비가 설정값과 2% 이내 | **PASS** — 최대 1.082% | `data/raw/20261120_desk_bracket/` 16/4/1 ms × 5세트. 실제 15979/3981/988 µs → R = 4.0138, 16.1731. 재촬영 0회 |
| C++ npy 로더와 Python 로더 비트 동일 | **PASS** | `tests/py/test_npy_crosscheck.py` 15케이스 + Pi 실 RAW 샘플 |
| `ctest`, `pytest` 통과 | **PASS** | ctest 12/12, pytest 33/33, 컴파일 경고 0 |
| `pi_notes.md`에 명령·모드·튜닝 파일 위치 | **PASS** | pi_notes 0~9절 |

**산출물**
- `capture/capture_raw.py`(single/bracket/sensor_hdr), `capture/rawutil.py`(PC 테스트 가능한 순수 로직)
- `python/cctvlog/calib/tuning_import.py` → `configs/tuning_ccm.json`, `configs/tuning_alsc.json`(입력은 `pisp/imx708_wide.json`, sha256 기록)

**결정**
- ALSC/CCM 해석은 raspberrypi/libcamera `main` 소스(2026-11-20 읽음)를 따른다.
  - 격자 32×32, 행 우선. 보정 이득 = cal/min(cal)·L′, ct는 K 단위 선형 보간 후 범위 밖 고정.
  - CCM은 행 우선, out = M·RGB.
- 튜닝 테이블은 센서 원래 방향이다. 현재 RAW는 센서 h+v flip 상태이므로 **Phase 4 LSC는 프레임 JSON의 `sensor_flip`에 따라 테이블을 뒤집어 적용한다.**
- `luminance_strength`는 튜닝 값이 0.65다(Pi ISP는 감광을 65%만 보정). 보관용 선형 경로에 1.0(완전 보정)을 쓸지는 Phase 4에서 정하고, 지금은 JSON에 원래 값을 그대로 둔다.

**관찰 (Phase 1·2에서 다시 확인)**
- 화소값으로 구한 노출비가 메타데이터 노출비보다 0.4~0.6% 크다(4.037 vs 4.014, 16.24 vs 16.17; 긴 노출 100~850 DN 화소의 중앙값). 블랙 레벨 오차, 비선형성, 비율 중앙값의 편향을 후보로 본다. Phase 1 다크 프레임·PTC에서 블랙 레벨과 선형성을 측정한 뒤 판단한다. 합성은 계속 메타데이터의 실제 노출비를 쓴다.
- 짧은 노출의 양자화 오차가 커서(1 ms에서 −1.2%), R이 큰 브래킷은 짧은 노출이 1 ms보다 짧아지지 않게 구성한다(예: R = 16이면 긴 노출 ≥ 16 ms).

---

## 2026-11-21 — Phase 1 PTC: 셋업, 결과, 결정

**셋업 (플랫필드 확인 `results/p1/flatfield_check/`, `capture/check_flatfield.py`)**
- 첫 광원(LED 트레이싱 패드)은 약 3 kHz로 깜빡였다(센서 24행 주기의 가로 줄무늬). 최대 밝기에서도 그대로였다.
  - 짧은 노출에서 쌍 차분 분산을 +48~+450% 부풀렸다.
  - 프레임 전체 평균은 0.01% 수준으로 안정적이라, 행 단위로 분석해야만 보였다.
- 두 번째 광원은 깜빡임이 없었지만 30초에 −4.3%로 어두워져서 버렸다.
- 최종 광원은 깜빡임이 약하게 남아 있다(80 µs에서 행 진폭 약 4%). 노출 1.7 ms 이상에서는 분산 부풀림이 +0.1% 이하다.
  - 남은 영향은 분석에서 깜빡임 판정으로 뺀다(아래).
  - 버린 대안: 광원 추가 교체. 다크 프레임으로 b를 따로 구하면 측정으로 분리할 수 있어서 교체하지 않았다.
- ROI는 중앙 256×256 센서 화소다. 화면 중앙이 밝아서 512×512에서는 블록 편차가 16%, 256×256에서는 7%다. 노이즈 모델이 선형이라 이 편차는 분산 추정에 영향이 없다.
- 실제 최소 노출은 53 µs다(`pi_notes.md` 5절). 그래서 게인 16의 최저 밝기 단계는 1.75%다.

**데이터**
- flat: `data/pi_runs/ptc_flat_20261121_2044`
  - 게인 1.1228, 4, 16 × 13단계(게인 16은 12단계) × 2쌍, 168장
  - 광원 모니터 범위 0.34%
- dark: `data/pi_runs/ptc_dark_20261121_2052`
  - 게인마다 최단·최장 노출 × 10장, 60장
  - 빛 누출 없음(최장과 최단의 블랙 차이 ≤ 0.05 DN)
- 분석: `python experiments/p1_ptc.py configs/p1_ptc.yaml` → `results/p1/<timestamp>/`, `results/p1/sensor_summary.md`

**결과 (수락 기준: 각 게인 R² ≥ 0.99 → PASS)**

| 항목 | 게인 1.1228 | 게인 4 | 게인 16 |
|---|---|---|---|
| R² (fixed_b / free) | 0.99722 / 0.99768 | 0.99888 / 0.99886 | 0.99772 / 0.99773 |
| K [DN/e⁻] | 0.0543 | 0.1945 | 0.7707 |
| 판독 노이즈 | 0.613 DN = 11.3 e⁻ | 0.797 DN = 4.10 e⁻ | 1.703 DN = 2.21 e⁻ |
| 포화 전자 수 (ADC 클립) | 17,666 e⁻ | 4,931 e⁻ | 1,244 e⁻ |
| 단일 노출 DR | 10.61 stop (63.9 dB) | 10.23 stop (61.6 dB) | 9.14 stop (55.0 dB) |
| 블랙 레벨 (B, G1, G2, R) | 63.97, 63.96, 63.96, 63.97 | 64.04, 64.04, 64.05, 64.05 | 64.17, 64.15, 64.20, 64.21 |
| 노이즈 모델 a / b (정규화) | 5.66e-5 / 4.08e-7 | 2.03e-4 / 6.90e-7 | 8.04e-4 / 3.15e-6 |

- 게인 비와 K 비가 맞는다: g4/g1은 3.562 대 3.583(+0.6%), g16/g1은 14.250 대 14.198(−0.4%).
- 암전류는 무시할 수 있다: 최장 노출에서 블랙 이동 ≤ +0.045 DN, 분산 증가 ≤ 0.013 DN².

**결정**
1. **노이즈 모델 (a, b)는 `fixed_b` 방식으로 정한다.**
   - b는 같은 게인의 다크 프레임(최단 노출) 쌍 차분 분산이다. K는 네 채널을 함께 쓴 가중 최소제곱(가중치 1/σ⁴, 상대오차 균일 가정)으로 구한다.
   - 근거: 다크 b는 깜빡임과 PTC 휨 어느 쪽에도 오염되지 않은 직접 측정값이다.
   - 이유: 미리보기처럼 전체 구간을 자유 피팅하면, 휨 때문에 b가 부풀었다(게인 16에서 4.75 대 다크 2.93). 최종 제외 규칙을 적용하면 free와 fixed_b의 차이는 b에서 2~6%, K에서 1% 미만이다.
   - 대안(free)의 a, b는 노이즈 JSON의 `assumptions.alternative`에 같이 남긴다.
2. **포화 제외는 쌍 단위로 한다.** 같은 쌍에서 한 채널이라도 포화 화소가 있거나 평균이 0.9 FS를 넘으면 네 채널을 모두 뺀다.
   - 근거: 초록이 포화된 단계에서 이웃 R·B 화소의 분산이 약 20% 떨어지고, 차분 영상의 이웃 상관이 0.01에서 0.2로 커졌다.
   - 넘친 전하가 이웃으로 번지는 현상(blooming)으로 추정한다.
   - 채널별로만 제외했을 때는 게인 16의 R²가 0.953이었다.
3. **깜빡임 제외도 쌍 단위로 한다.**
   - 판정: 원래 분산 / 행 평균을 뺀 분산 − 1이, 다크 프레임의 같은 값(센서 자체 행 노이즈, 약 1%)보다 1% 넘게 크면 깜빡임으로 본다.
   - 제외된 쌍: 게인 4의 최저 2단계, 게인 16의 최저 2단계.
   - 분산 추정치는 행 평균을 빼지 않은 원래 쌍 차분을 쓴다. 센서의 진짜 행 노이즈(약 1%)를 지우지 않기 위해서다.
   - 버린 대안: 모든 점에 행 평균 제거를 적용하는 것. 진짜 행 노이즈까지 지워서 다크 b와 기준이 어긋난다.
4. **K는 채널 공통 하나로 둔다.**
   - 채널별 K 차이는 약 8%다(B는 공통값보다 약 −5%, R은 +3~6%). σ로는 4% 이내다.
   - 채널별 K는 노이즈 JSON `assumptions.per_channel_K_dn_per_e`에 기록한다.
   - 노이즈 기준점(LSC·WB·CCM 이후)을 정하는 Phase 4에서 다시 본다.

**관찰 (원인 미확정)**
- **PTC 휨은 전자 수에 따라 달라진다.** 밝은 구간 K / 어두운 구간 K가 게인 1에서 0.946(최대 1.77만 e⁻), 게인 4에서 0.966(4.9천 e⁻), 게인 16에서 0.992(1.2천 e⁻)다.
  - DN이 아니라 전자 수에 비례하므로, 전하 영역의 효과(brighter-fatter 또는 큰 전하에서 변환 이득이 비선형)와 맞는 경향이다.
  - 영향은 σ 기준 최대 약 2.7%다. 단일 직선 모델을 유지한다.
- **짧은 노출에서 응답이 비례식보다 크다.** 게인 16에서 53 µs는 +6.4%, 160 µs는 +2.0%, 320 µs는 +1.4%다.
  - 오프셋으로 환산하면 게인 16은 +3.5 µs(≈ +1 DN), 게인 4는 약 −2 µs, 게인 1은 약 −10 µs다. 게인마다 일관되지 않아, 노광 시간 오프셋인지 블랙 오프셋인지 구분되지 않는다.
  - PTC는 측정된 평균을 x로 쓰므로 영향이 없다.
  - Phase 2 브래킷 합성(짧은 노출 ≥ 988 µs, 게인 최소)에서는 게인 1 기준 0.2~0.8% 범위다. 합성 실측에서 다시 확인한다.
- **Phase 0의 노출비 관찰(화소 기준 노출비가 0.4~0.6% 큼)**
  - 블랙 레벨 오차로는 설명되지 않는다. 게인 1의 블랙은 63.96으로, 64와 −0.04 DN 차이뿐이다.
  - 위의 짧은 노출 응답 편차와 같은 방향인지 Phase 2에서 확인한다.

**Phase 1에 남은 것**
- CLAUDE.md Phase 1의 센서 HDR 모드 측정(노출 대비 평균의 선형성과 경계 위치)

---

## 2026-11-21 — Phase 1 센서 HDR 모드 특성, Phase 1 완료

**데이터·분석**
- 같은 플랫필드 셋업에서 센서 HDR을 켜고 노출을 스윕했다. `capture/capture_sensor_hdr.py`
  - 최소 게인: `data/pi_runs/sensor_hdr_g1_20261121_2115`. 858 µs ~ 1 s, 35단계, 단계당 2쌍
  - 게인 16: `data/pi_runs/sensor_hdr_g16_20261121_2117`. 858 µs ~ 212 ms, 포화까지
- 분석: `python experiments/p1_sensor_hdr.py configs/p1_sensor_hdr.yaml` → `results/p1_sensor_hdr/<timestamp>/`, `results/p1/sensor_hdr_summary.md`
- 유효 노출 E = (게인 / 1.1228) × 노출. 이 가정은 실측으로 확인했다. 두 게인이 겹치는 20점(E 12 ms ~ 844 ms)에서 응답 차이가 최대 1.33%, 중앙값 0.32%였다.
- HDR 모드 다크 프레임은 찍지 않았다. 블랙은 메타데이터 64를 쓰고, 선형 구간 직선의 절편 +1.65 DN으로 검증했다.

**결과 (초록 G1 채널, 중앙 256×256)**
| 항목 | 값 |
|---|---|
| 선형 구간 | E ≤ 5.15 ms, 출력 ≤ 0.110 (105 DN). 기울기는 일반 모드의 0.968배(짧은 노출 메타데이터 오차 ±6% 포함) |
| 첫 경계 (압축 시작) | E 5.15 ~ 6.44 ms, 출력 0.110 ~ 0.122. 다음 점은 일반 모드 직선보다 −12.5% |
| 경계 뒤 응답 | 기울기가 선형 구간의 1/14(6~14 ms) → 1/26(18~80 ms) → 1/39(89~300 ms) → 1/49(0.36~1 s) → 1/31(1.0~1.3 s). 구간별 직선 7개, BIC로 선택 |
| 포화 | E ≈ 1.6 s (1.29 s에서 0.80, 1.60 s에서 0.995) |
| 밝은 쪽 DR 확장 | 일반 모드 포화 46.1 ms 대비 **34.6배 = 5.1 stop** |
| 선형 구간 노이즈 | 같은 출력 레벨에서 분산이 일반 모드 모델의 **3.5배** (SNR −5.5 dB, 9점 범위 −3.7 ~ −6.4 dB) |
| 첫 경계 전후 노이즈 | 출력 σ는 거의 그대로(28.9 → 29.8 dB 출력 SNR)인데 응답 기울기는 1/14 → 입력 기준 SNR이 약 20 dB 낮아짐 (구간 직선 기울기를 쓴 근사) |

**결정**
- 센서 HDR 모드는 CLAUDE.md대로 **비교군으로만** 쓴다.
  - 출력이 비선형으로 압축(꺾은선 형태로 추정)되어 있다. 선형 RAW로 되돌리려면 위의 응답 곡선을 역변환해야 한다.
  - 첫 경계 바로 위에서는 입력 기준 노이즈가 크게 뛴다. 노출이 짧아지는 브래킷 합성과 같은 방향이다.
  - Phase 2 SNR 그래프에 비교군을 넣는다면, 이 결과의 **입력 기준 SNR 곡선**(`report.json` `steps[].snr_in_db`)을 쓴다. 출력 SNR은 압축 때문에 실제보다 커 보이므로 쓰지 않는다.
- 노출 대응을 예측할 수 없는 모드에서는 "요청값 일치"가 아니라 "메타데이터가 안정된 실제값 일치"로 프레임을 고른다(`Session.settle`).

**관찰 (원인 미확정)**
- 선형 구간 노이즈가 3.5배인 이유는 HDR 모드가 내부적으로 낮은 변환 이득이나 디지털 스케일을 쓰기 때문일 수 있지만 확인할 방법이 없다(블랙박스).
- 경계 뒤 출력이 평평해지는 구간(6~14 ms)이 있다. 내부 합성 가중치의 전이 구간으로 보인다.
- 최대 RAW 코드는 1023이다. 완전 포화 단계의 ROI 평균은 0.9990(≈ 1022 DN)이다.

**Phase 1 수락 기준 결과**
| 기준 | 결과 | 근거 |
|---|---|---|
| 각 게인 R² ≥ 0.99 | **PASS**: 0.99722 / 0.99888 / 0.99772 (게인 1.1228 / 4 / 16) | `results/p1/sensor_summary.md` |
| `results/p1/sensor_summary.md` | **PASS** | `experiments/p1_ptc.py` |
| 블랙 레벨, read noise, full well, 단일 노출 DR | **PASS** | 위 2026-11-21 PTC 항목 |
| 센서 HDR 모드 선형성·경계 위치 | **PASS** | `results/p1/sensor_hdr_summary.md` |
| `pytest` | **PASS**: 65/65 | |
