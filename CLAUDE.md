# CLAUDE.md — H-Log: 영상감시용 WDR(HDR) 대응 노이즈 매칭 Log 곡선 및 보관 스트림

이 파일은 Claude Code가 이 저장소에서 작업할 때 따르는 개발 명세다. 사람용 설명(배경 지식, 유스케이스, 페르소나)은 별도 기획 문서에 있다. 여기서는 **무엇을, 어떤 언어로, 어떤 순서로, 어떤 기준으로 만들지**만 정의한다.

전체 기간: **10주**, Phase 0~6. 실습과 아이디어 구현에 집중하고, 아이디어와 무관한 작업은 최소화한다(10절 "하지 않는 것" 참고).

---

## 0. 프로젝트 정의

Raspberry Pi 5 + Camera Module 3에서 RAW를 얻고, 다음을 구현·검증한다.

1. **DR 확보**: 노출 브래킷 합성(실측)과 DCG(시뮬레이션)로 DR을 넓히고, 각 방식의 **합성 후 노이즈 분포**를 모델링한다.
2. **HDR 대응 H-Log**: 합성 후 노이즈 분포에 맞춘 구간별 노이즈 매칭 Log 곡선을 설계하고 C++(float + 고정소수점 LUT)로 구현한다.
3. **듀얼 파이프라인**: 같은 HDR 데이터에서 8bit 표시용 영상(직접 구현한 NR + Local 톤매핑)과 10bit H-Log 보관용 영상을 만든다.
4. **압축 강건성**: C++ 코덱(FFmpeg libav*, libx265)으로 압축한 뒤에도 보관 영상에서 하이라이트·섀도우를 복원할 수 있는지, 번호판 OCR로 이득을 확인한다.

핵심 가설:
- H1. 합성 후 노이즈 분포에 맞춘 H-Log는 단일 노출 기준 곡선, S-Log3, HLG보다 같은 비트에서 양자화 손실이 작다.
- H2. 확보한 DR에 따라 필요한 비트 수가 정량적으로 정해진다 (예: 브래킷 R=16에서 10bit 충분 여부).
- H3. HEVC 압축(QP 22~37) 후에도 보관 영상의 복원 이득(복원 SNR, OCR 정확도)이 일정 QP까지 유지된다.

반대 결론도 유효한 결과다. 가설에 맞추려고 기준선(표시용 경로, 비교 곡선)을 약하게 만들지 말 것.

---

## 1. 언어 구성과 경계

**처리 핵심과 코덱은 C++, 촬영·파라미터 계산·실험 관리·분석은 Python.** 둘은 **파일(npy, JSON, 영상)과 CLI 호출**로만 연결한다. pybind11은 쓰지 않는다(후속 확장).

| 영역 | 언어 | 위치 |
|---|---|---|
| Pi RAW 촬영 (단일, 브래킷, 센서 HDR 모드, Pi ISP 출력 동시 저장) | Python (Picamera2) | `capture/` |
| 튜닝 파일에서 CCM·ALSC 추출 | Python | `python/cctvlog/calib/tuning_import.py` |
| 노이즈 모델 피팅(PTC), 합성 후 노이즈 분포 측정·모델 | Python | `python/cctvlog/sensor/` |
| 곡선 파라미터 계산 | Python | `python/cctvlog/calib/curve_params.py` |
| 기준 모델: 곡선 4종, 브래킷 합성 | Python (NumPy, float64) | `python/cctvlog/ref/` |
| 브래킷 HDR 합성, DCG 시뮬레이터 | C++ | `core/src/hdr/` |
| ISP: BLC, LSC 적용, bilinear Demosaic, WB, CCM 적용 | C++ | `core/src/isp/` |
| NR (guided filter), Local 톤매핑 (guided base/detail) | C++ + OpenMP | `core/src/isp/` |
| 곡선: 구간별 노이즈 매칭 H-Log, Rec.709, HLG, S-Log3 (float) + H-Log 고정소수점 LUT | C++ | `core/src/curves/` |
| 인코딩·디코딩, RGB↔YCbCr 변환 | C++ (libavcodec, libavformat, libx265) | `core/src/codec/` |
| CLI 앱: hdr_merge, dcg_sim, process, encode, decode, restore, curve_eval | C++ | `apps/` |
| 실험 격자 실행, 지표, 그래프, 리포트, OCR 평가 | Python | `python/cctvlog/`, `experiments/` |

경계 규칙:
- **파라미터 계산은 Python, 파라미터 적용(화소 연산)은 C++.** 파라미터는 `configs/*.json`으로 전달.
- Python 쪽에 화소 처리 로직을 새로 만들지 않는다. 예외: `python/cctvlog/ref/` 기준 모델(검증 전용, 실험 경로에서 사용 금지).
- 모든 C++ CLI는 `--config <json>`과 입출력 경로를 받고, 실행 정보(버전, git commit, 파라미터, 처리 시간)를 출력 폴더의 `run.json`에 남긴다.
- Python은 `python/cctvlog/cli.py`의 `run_cpp(app, **args)` 래퍼 하나로만 C++ CLI를 호출한다 (subprocess, 실패 시 stderr 포함 예외).

### 1.1 검증 범위 (핵심 블록만)

| 블록 | 기준 | 허용 오차 |
|---|---|---|
| 곡선 4종 C++ float | Python 기준 모델 | 상대오차 ≤ 1e-5 |
| H-Log 고정소수점 LUT | C++ float 구현 | 10bit 코드 기준 최대 1 LSB |
| 브래킷 HDR 합성 C++ | Python 기준 모델 | 상대오차 ≤ 1e-5 |
| DCG 시뮬레이터 C++ | 해석적 평균·분산 | 몬테카를로 신뢰구간 안 (예: 1e5 샘플, 분산 ±3%) |
| 기타 블록 (Demosaic, NR, 톤매핑, CCM 등) | 해석적 입력 테스트 (상수, 램프, 체커보드) | 블록별 GoogleTest |

---

## 2. 하드웨어 / 플랫폼 사실

| 항목 | 사실 | 코드에 주는 영향 |
|---|---|---|
| 보드 | Raspberry Pi 5 | 하드웨어 영상 인코더 없음 → 인코딩은 PC에서 C++ 코덱으로 |
| 카메라 | Camera Module 3 (Sony IMX708, Quad Bayer) | 센서 HDR 모드 최대 약 3MP(2304×1296). **기준 해상도 = 2304×1296** |
| Pi 5 RAW | 기본(packed)은 8bit 압축 PiSP 포맷 | **unpacked(비압축 16bit)** 로 요청. MSB 정렬, LSB 0 패딩 → `>> (16 - bit_depth)` |
| 센서 HDR 모드 | 센서 내부 합성(블랙박스) | Phase 1에서 특성만 측정, 비교군으로만 사용 |
| DCG | IMX708/Pi에서 제어 불가 | **시뮬레이션만**. 가정값은 config에 명시하고 리포트에 기재 |
| 노출 변경 지연 | 설정 후 실제 적용까지 몇 프레임 걸릴 수 있음 | 브래킷 시 프레임 메타데이터의 실제 노출 확인, 어긋난 프레임 폐기. 합성에는 **실제 노출비** 사용 |
| 튜닝 파일 | libcamera가 IMX708용 JSON 튜닝 파일 제공 (CCM, ALSC 포함) | 위치·키 구조는 설치 버전에서 확인 후 `docs/pi_notes.md`에 기록 |

검증되지 않은 API 사용 규칙:
- Picamera2 / libcamera / FFmpeg API는 설치된 버전에서 `--help`, 헤더, 소스, `Picamera2.sensor_modes`로 확인 후 사용한다.
- Picamera2에서 RAW 스트림과 처리된 main 스트림을 한 request로 함께 받는 구성을 사용한다(동일 프레임 보장). 동작 확인 결과를 `docs/pi_notes.md`에 기록.
- Bayer 순서는 하드코딩 금지, 캡처 설정에서 읽어 JSON에 저장.

---

## 3. 저장소 구조

```
cctv-log/
├── CLAUDE.md
├── README.md
├── CMakeLists.txt  CMakePresets.json  vcpkg.json
├── pyproject.toml                  # 순수 Python 패키지 (C++ 빌드 포함하지 않음)
├── core/                           # C++ 라이브러리 cctvlog_core
│   ├── include/cctvlog/
│   │   ├── image.hpp               # Image<T>, BayerImage, stride
│   │   ├── io.hpp                  # .npy(uint16/float32) 읽기·쓰기, JSON, 사이드카 JSONL
│   │   ├── params.hpp              # 블록별 Params + JSON 직렬화 (nlohmann)
│   │   ├── hdr.hpp                 # bracket_merge, dcg_simulate
│   │   ├── isp.hpp                 # blc, lsc, demosaic_bilinear, wb, ccm, nr_guided, tonemap_local
│   │   ├── curves.hpp              # Curve 인터페이스, PiecewiseNoiseMatched, Rec709, HLG, SLog3, FixedLut
│   │   ├── fixed.hpp               # 반올림·포화 규칙
│   │   ├── pipeline.hpp            # DisplayPipeline, ArchivePipeline
│   │   └── codec.hpp               # Encoder, Decoder, ycbcr 변환
│   └── src/...
├── apps/                           # C++ CLI
│   ├── hdr_merge/  dcg_sim/  process/  encode/  decode/  restore/  curve_eval/
├── capture/                        # Pi 전용 Python
│   ├── capture_raw.py              # single | bracket | sensor_hdr, + ISP 출력 동시 저장
│   ├── capture_ptc.py
│   └── requirements-pi.txt
├── python/cctvlog/
│   ├── io/  cli.py  runner.py
│   ├── sensor/                     # ptc.py, noise_model.py, merged_noise.py
│   ├── calib/                      # tuning_import.py, curve_params.py
│   ├── ref/                        # curves.py, bracket_merge.py (검증 전용)
│   ├── metrics/                    # snr.py, mtf.py, banding.py, storage.py
│   └── usecase/ocr_eval.py
├── experiments/                    # p0_ ~ p6_ 스크립트, config yaml만 인자
├── configs/                        # yaml + JSON (noise_model, tuning_ccm, tuning_alsc, curves, nr, dcg_assumptions)
├── tests/cpp/  tests/py/
├── data/  results/                 # git 제외
└── docs/
    ├── pi_notes.md
    ├── decisions.md                # 날짜, 결정, 근거 수치, 버린 대안
    └── fixed_point.md              # LUT 비트 폭, 반올림 규칙, 오차 표
```

---

## 4. 빌드와 실행

- C++20, GCC 12+ 또는 Clang 15+, CMake 3.24+, Ninja.
- vcpkg manifest: `eigen3`, `opencv4[core,imgproc]`, `ffmpeg[avcodec,avformat,x265]`, `nlohmann-json`, `gtest`. baseline으로 버전 고정. **FFmpeg+x265 빌드는 Phase 0에서 먼저 통과시킨다.**
- 컴파일: core에 `-ffast-math` 금지. Release `-O3`, OpenMP on.
- 명령 (README와 동일하게 유지):
  - `cmake --preset release && cmake --build --preset release`
  - `ctest --preset release`
  - `pip install -e ".[dev]" && pytest tests/py` (Python 테스트는 빌드된 CLI 경로를 `CCTVLOG_BIN` 환경변수 또는 기본 `build/release/apps`에서 찾는다)
- Pi에서는 C++를 빌드하지 않는다 (촬영만).

---

## 5. 데이터 규약

### 5.1 RAW (Pi → PC)
- 프레임마다 `NNNNNN.npy`(uint16, right-shift 완료) + `NNNNNN.json`. Pi ISP 출력은 `NNNNNN_isp.png`(8bit RGB).
- 브래킷 세트는 하위 폴더 `bracket_KKKK/`에 노출 순서대로 저장, `bracket.json`에 설정 노출과 실제 노출 기록.
- 세션: `data/raw/{YYYYMMDD}_{scene}_{mode}/` + `session.json`.

### 5.2 프레임 JSON 필수 필드
```json
{
  "frame_index": 0, "timestamp_ns": 0,
  "sensor_mode": {"size": [2304, 1296], "bit_depth": 10, "hdr": false},
  "bayer_order": "RGGB",
  "exposure_time_us_requested": 10000,
  "exposure_time_us": 10000,
  "analogue_gain": 1.0, "digital_gain": 1.0,
  "colour_gains": [1.8, 1.5],
  "black_level": 64, "white_level": 1023,
  "colour_temperature": 5000, "sensor_temperature": 40.0,
  "notes": ""
}
```
얻을 수 없는 값은 `null`. 추정값 금지.

### 5.3 C++ 내부 표현
- `Image<T>`: row-major, interleaved, 명시적 stride. `BayerImage` = 1채널 + bayer_order.
- 선형 값: BLC 후 `(v - black) / (white - black)`로 정규화한 float32. **기준 스케일은 브래킷의 가장 긴 노출**. 합성 결과는 1.0을 넘을 수 있다 (최대 ≈ R).
- HDR 합성은 **Bayer 도메인**(Demosaic 전)에서 한다.
- 고정소수점 LUT: 정방향은 입력을 `[0, x_max]` → uint16 균등 인덱스(65536 엔트리)로 매핑, 출력 uint16 코드(상위 10bit 유효). 역방향은 1024 엔트리 → float 선형값. 반올림 round-half-up, 포화. 규칙은 `docs/fixed_point.md`와 일치.

### 5.4 노이즈 분포 JSON (Phase 1~3 공통 스키마)
```json
{
  "kind": "single | bracket | dcg",
  "scale": "normalized_long_exposure",
  "x_max": 16.0,
  "segments": [
    {"x_start": 0.0, "a": 0.0012, "b": 2.0e-6},
    {"x_start": 0.9, "a": 0.0192, "b": 5.1e-4}
  ],
  "assumptions": {}
}
```
구간 i에서 σ²(x) = a_i·x + b_i. 단일 노출은 구간 1개. 브래킷은 노출비 R에 대해 (a·R, b·R²) 구간이 추가된다. DCG는 `assumptions`에 가정값 기록.

### 5.5 보관용 사이드카
영상 1개당 `*.sidecar.jsonl`(한 줄 = 한 프레임: 5.2 필드 + `dr_kind`, `exposure_ratio`, `curve_name`, `curve_params_hash`, `ccm`, `wb_gains`, `nr_strength`, `fixed_point`), 영상 SHA-256은 `*.sha256`.

---

## 6. 핵심 알고리즘 명세

### 6.1 브래킷 HDR 합성 (`hdr::bracket_merge`)
- 입력: 같은 장면 N장(2~3) BayerImage + 실제 노출 시간. 출력: float32 Bayer(긴 노출 스케일) + uint8 출처 맵.
- 각 화소: 긴 노출부터 확인. 정규화 값이 포화 임계 τ(기본 0.9) 미만이면 사용. 경계 띠 [0.8τ, τ]에서는 긴/짧은 노출을 선형 가중 합. 짧은 노출 값은 `t_long / t_i`를 곱해 스케일 맞춤.
- 모든 노출이 포화면 가장 짧은 노출 값(포화 표시 비트 세팅).
- 파라미터(τ, 띠 폭)는 JSON. Python 기준 모델과 동일 로직.

### 6.2 DCG 시뮬레이터 (`hdr::dcg_simulate`)
- 입력: 선형 장면(브래킷 합성 결과를 "참값 장면"으로 사용), 가정 파라미터(`configs/dcg_assumptions.json`): 변환 게인 비(기본 4), HCG/LCG read noise(e⁻), HCG/LCG full well(e⁻), 장면 스케일(e⁻/선형 1.0), ADC 비트, seed.
- 절차: 장면을 전자 수로 변환 → **Poisson 샷 노이즈 1회 생성(두 판독이 공유)** → HCG/LCG 각각 read noise 추가, full well 클립, 양자화 → HCG가 임계 미만이면 HCG, 이상이면 LCG(스케일 맞춤), 경계 띠 가중 합.
- 출력: 합성 선형 이미지 + 5.4 스키마의 해석적 노이즈 분포 JSON.

### 6.3 합성 후 노이즈 분포 (`python/cctvlog/sensor/merged_noise.py`)
- 해석 모델: 단일 노출 (a, b)와 노출비 R(또는 DCG 가정값)로 5.4 구간 생성.
- 실측: 플랫필드 브래킷(밝기 단계 15개 이상)을 C++로 합성 → 프레임 쌍 차분으로 분산 측정 → 해석 모델과 비교 (구간별 오차 %).
- SNR dip: 경계 직전·직후 SNR 차이(dB) 실측 vs 예측(샷 노이즈 지배 시 ≈ 10·log10 R).

### 6.4 구간별 노이즈 매칭 H-Log (`curves::PiecewiseNoiseMatched`)
- 입력: 5.4 노이즈 분포 JSON. 정의: C(x) = k·∫₀ˣ dt/σ(t), k = 1/∫₀^{x_max} dt/σ(t) (C(x_max) = 1).
- 구간 i (x ∈ [x_i, x_{i+1})), a_i > 0: ∫ dt/√(a_i t + b_i) = (2/a_i)(√(a_i x + b_i) − √(a_i x_i + b_i)). a_i = 0이면 선형 (x − x_i)/√b_i.
- C는 구간 경계에서 값 연속(도함수는 불연속 허용 — 노이즈 점프를 그대로 반영하는 것이 설계 의도).
- 역함수: 구간을 C 값으로 찾은 뒤 닫힌 형태로 계산.
- 양자화 지표 **R(x) = (양자화 간격 Δx) / σ(x)**, Δx = (1/(2^bits − 1)) / C'(x). 노이즈 매칭 곡선에서는 R이 상수 = N/(2^bits − 1), N = ∫₀^{x_max} dt/σ(t). **필요 비트 = ⌈log2 N⌉** (R ≤ 1 조건).
- 비교 곡선: Rec.709 OETF, HLG(BT.2100), S-Log3(Sony 공개 수식). 입력 스케일은 x_max를 각 곡선의 정의역 상한에 맞추는 규칙을 `decisions.md`에 명시.

---

## 7. 단계별 작업 명세

각 Phase는 **수락 기준을 자동 검사하는 테스트나 스크립트 출력**이 있어야 완료다. 다음 Phase 전에 `docs/decisions.md`에 결과와 결정을 기록한다.

### Phase 0 — 환경과 촬영 체계 (1주)
- [Py] `capture_raw.py --mode {single,bracket,sensor_hdr} --exposures-us ... --gain ... --count ... --out ...`: AE/AWB 끔, unpacked RAW + ISP main 스트림 동시 저장, 5.2 JSON. bracket 모드는 실제 노출 확인 후 어긋난 프레임 재촬영(최대 재시도 횟수 설정).
- [Py] `tuning_import.py`: Pi의 IMX708 튜닝 JSON에서 CCM(색온도별)과 ALSC 테이블 추출 → `configs/tuning_ccm.json`, `configs/tuning_alsc.json`.
- [C++] CMake/vcpkg(FFmpeg+x265 포함) 빌드 통과, `io`(npy/JSON), CLI 뼈대 1개(`curve_eval` 빈 버전), GoogleTest 1개.
- [Py] `cli.py`의 `run_cpp` 래퍼, `runner.py`(config 로드, 결과 폴더, 환경 기록).

수락 기준: 브래킷 실제 노출비가 설정값과 2% 이내 / C++ npy 로더와 Python 로더 결과 비트 동일 / `ctest`, `pytest` 통과 / `docs/pi_notes.md`에 명령·모드·튜닝 파일 위치 기록.

### Phase 1 — 센서 특성화와 노이즈 모델 (1주, Python)
- [Py] `capture_ptc.py`: 게인 {1, 4, 16} × 노출 10단계(포화 포함) × 플랫필드 쌍 + 다크 10장.
- [Py] `ptc.py`: 쌍 차분 분산, 채널별, 포화 직전 제외 선형 피팅. `noise_model.py`: 게인별 (a, b) → 5.4 스키마(kind=single).
- [Py] 블랙 레벨, read noise, full well, 단일 노출 DR. 센서 HDR 모드: 노출 대비 평균의 선형성과 경계 위치만 측정.

수락 기준: 각 게인 R² ≥ 0.99 / `results/p1/sensor_summary.md`.

### Phase 2 — DR 확보 (2주, 핵심)
- [C++] `hdr::bracket_merge` + `apps/hdr_merge`. [Py] `ref/bracket_merge.py`.
- [C++] `hdr::dcg_simulate` + `apps/dcg_sim`.
- [Py] `merged_noise.py`: 해석 모델(브래킷 R=4, 16 / DCG 가정값), 실측 비교, SNR dip.
- [Py] 그래프: 밝기(stop) 대비 SNR 곡선 — 단일 노출, 브래킷 R=4, R=16, DCG — 한 그림. DCG 변환 게인 비 {2, 4, 8} 민감도.
- 출력: `configs/noise_bracket_R4.json`, `configs/noise_bracket_R16.json`, `configs/noise_dcg.json`.

수락 기준: C++ 합성 vs Python 기준 상대오차 ≤ 1e-5 / 실측 합성 분산이 해석 모델과 구간별 ±10% (못 맞추면 원인 분석 후 실측 분포를 곡선 입력으로 채택, `decisions.md`에 기록) / DCG 시뮬레이터 몬테카를로 테스트 통과.

### Phase 3 — HDR 대응 H-Log (1주, 핵심)
- [Py] `curve_params.py`: 노이즈 분포 JSON → 곡선 JSON (k, 구간별 상수, x_max, N, 필요 비트).
- [Py] `ref/curves.py`: 6.4의 곡선과 비교 곡선 기준 모델.
- [C++] `curves`: PiecewiseNoiseMatched, Rec709, HLG, SLog3 float. H-Log `FixedLut` (정방향 65536, 역방향 1024). `apps/curve_eval`: 램프 입력 → encode → quantize(bits) → decode → 결과 npy.
- [Py] `experiments/p3_curves.py`: 곡선 6종(Rec.709, HLG, S-Log3, H-Log 단일, H-Log 브래킷 R=16, H-Log DCG) × {8, 10, 12}bit의 R(x) 그래프, **DR(stop) 대비 필요 비트 그래프** (브래킷 R을 1~64로 바꿔가며).

수락 기준: C++ float vs Python 기준 ≤ 1e-5 / LUT vs float ≤ 1 LSB (오차 표 `docs/fixed_point.md`) / 왕복 오차, 단조성 테스트 통과 / `results/p3/conclusion.md`에 H1·H2에 대한 수치 결론.

### Phase 4 — 듀얼 파이프라인 (2주, C++)
- [C++] 공통 앞단(합성된 HDR Bayer 입력): BLC(합성 전 수행됨, 여기선 확인만) → LSC(ALSC 격자 bilinear 보간 gain) → `demosaic_bilinear` → WB(colour_gains) → CCM(튜닝 파일, 색온도 선형 보간).
- [C++] `DisplayPipeline`: `nr_guided`(강도 = 표시용) → `tonemap_local`(guided filter로 log 휘도 base/detail 분리, base 압축, detail 유지) → Rec.709 → 8bit.
- [C++] `ArchivePipeline`: `nr_guided`(강도 = 보관용) → H-Log LUT → 10bit + 사이드카.
- [C++] `apps/process`: 세션/브래킷 폴더 → 표시용 8bit 시퀀스 + 보관용 10bit 시퀀스(npy 또는 16bit PNG) + 사이드카. OpenMP, 블록별 처리 시간 `run.json`.
- [Py] NR 강도 간이 스윕: 게인 2구간(낮음=1, 높음=16) × 강도 6단계, SNR·MTF50 → `configs/nr.json`(표시용·보관용 각 2값, 근거 수치 포함).

수락 기준: NR off 보관 경로를 decode하면 공통 앞단 선형 출력과 양자화 오차 수준 일치 / 블록별 GoogleTest(상수·램프·체커보드) 통과 / 표시용 결과를 Pi ISP 출력과 나란히 저장한 비교 이미지.

### Phase 5 — C++ 코덱과 압축 실험 (2주)
- [C++] `codec`: libavcodec+libx265 인코더(고정 QP, `yuv420p10le`/`yuv420p`), libavcodec 디코더. **RGB↔YCbCr 변환은 직접 구현**(BT.709 행렬, full range 명시, 4:2:0 다운샘플·업샘플 필터 명시). swscale 사용 금지. 인코더 전체 설정을 `run.json`에 기록.
- [C++] `apps/encode`, `apps/decode`. 왕복 테스트(QP 0 근처 오차 한계).
- [Py] `experiments/p5_compression.py`: 곡선 4종(HLG, S-Log3, H-Log 단일, H-Log 브래킷 R=16) × QP {22, 30, 37} × 장면 2개(10bit) + 대조군(8bit H-Log 브래킷 × 같은 QP × 장면 2개). 지표: 비트레이트, 섀도우·미드·하이라이트 복원 SNR(선형 원본 대비), 밴딩.
- 막히면: Python FFmpeg CLI로 임시 진행 후 `decisions.md` 기록, C++ 경로로 최종 재생성.

수락 기준: `results/p5/conclusion.md`에 H3 결론 한 줄과 근거 그래프 / 한 명령으로 재현.

### Phase 6 — 복원 도구와 번호판 평가 (1주)
- [C++] `apps/restore INPUT --sidecar X.jsonl --preset highlight_recover --out DIR`: SHA-256 검증 → 디코드 → 역변환 → (옵션) 다중 프레임 평균 NR → 프리셋 톤/노출 → 8bit. `recipe.json`(입력 해시, 파라미터, git commit, 라이브러리 버전) + 출력 해시.
- [Py] `usecase/ocr_eval.py`: 인쇄 번호판 20장 이상 + 강한 조명 장면. 표시용 vs 보관 후처리 OCR 문자 정확도 (EasyOCR 또는 PaddleOCR, 버전 고정).
- 최종 리포트 `results/final_report.md`.

수락 기준: 스레드 수 1과 N에서 restore 출력 해시 동일 / OCR 비교 표.

---

## 8. 코딩 규칙

C++: C++20, `namespace cctvlog`. 예외는 I/O·파싱 경계에서만. 버퍼는 `std::vector`/`unique_ptr`, 원시 포인터는 뷰만. 화소 루프는 연속 메모리 순서, OpenMP `schedule(static)` 행 단위, 출력에 영향 주는 병렬 reduction 금지. Params는 JSON 직렬화 + 기본값. 외부 수식은 주석에 출처 + 기준값 테스트. clang-format.

Python: 3.11+, type hints, ruff, pytest. NumPy 벡터화. 무작위성은 `np.random.Generator` 인자, seed는 config. 실험 스크립트는 config yaml만 인자, 결과 폴더에 config 사본·git commit·C++ `run.json`들 보관. 그래프는 matplotlib, 단위 표기, PNG + CSV.

공통: `data/`, `results/` 커밋 금지. 모르는 하드웨어 동작은 추측하지 말고 측정 스크립트를 먼저 만들거나 사용자에게 Pi 실행을 요청.

## 9. 작업 방식 (Claude Code)

- 한 번에 한 Phase. 시작 시 TODO 작성, 끝나면 수락 기준 결과 보고.
- 새 C++ 블록 순서: Params → (해당 시) Python 기준 모델 → C++ 구현 → 테스트 → CLI 노출.
- Pi 작업은 정확한 명령·기대 출력·결과 파일 위치를 안내하고 결과를 받은 뒤 진행.
- 수락 기준 미달 시 기준을 바꾸지 말고 원인과 선택지를 `decisions.md`에 정리해 사용자에게 묻는다.
- 일정이 밀리면: Phase 4 NR 스윕을 고정값으로, Phase 5 QP를 2개로 축소 가능. **Phase 2·3은 축소하지 않는다.**

## 10. 하지 않는 것 (범위 밖, 후속 확장)

- ColorChecker 기반 CCM·LSC 직접 캘리브레이션 (튜닝 파일 값 사용)
- Malvar 등 고급 Demosaic (bilinear 사용)
- NR 3종 비교, 게인 4구간 이상의 NR 튜닝 테이블
- CCM 고정소수점, 곡선·합성 외 블록의 Python 기준 모델
- pybind11 연동
- 역광 얼굴 유스케이스, Pi 실시간 데모, 이벤트 기반 저장
- 움직이는 장면의 브래킷 합성 (정지 장면 한정)

## 11. 용어

- **DR 확보**: 다중 노출·DCG처럼 물리적으로 범위를 넓히는 것. **DR 보존**: 확보한 범위를 버리지 않고 기록하는 것(Log 곡선).
- **합성 후 노이즈 분포**: 5.4 스키마의 구간별 σ²(x).
- **R(x)**: 양자화 간격 / 노이즈 표준편차. 노이즈 매칭 곡선에서는 상수.
- **N**: ∫ dx/σ(x). 필요 비트 = ⌈log2 N⌉.
- **LSB**: 출력 코드 1단계 (10bit면 1/1023).
