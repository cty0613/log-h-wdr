# Raspberry Pi 5 + Camera Module 3 메모

Pi에서 직접 확인한 사실만 적는다. 추측 금지 (CLAUDE.md 2절, 8절). 각 항목에 확인 날짜와 명령을 남긴다.

## 0. Pi 작업 안내 (Phase 0 확인 단계)

> **자동화**: `scripts/pi.sh`가 PC에서 SSH로 동기화·실행·회수를 한 번에 한다. 아래 수동 `scp` 절차 대신 쓴다.
> ```bash
> cp .pi.env.example .pi.env      # PI_HOST(IP), PI_USER 채우기
> scripts/pi.sh setup             # 1회: SSH 키 등록 (Pi 비밀번호 입력, 사용자 터미널에서)
> scripts/pi.sh agent             # 키에 passphrase가 있으면: WSL 시작 후 1회 (사용자 터미널에서)
> scripts/pi.sh check             # 연결·picamera2·카메라 확인
> scripts/pi.sh probe --steps tuning_files,controls,manual_gain,white_level
>                                 # → data/pi_probe/<YYYYMMDD_HHMMSS>/ (+ pi_job.json)
> scripts/pi.sh job <name> [--dest DIR] <script.py> [args]   # 일반 촬영 스크립트
> ```
> - `job`은 Pi에서 `capture/<script> --out ~/cctvlog/runs/<name> ...`을 실행한다. 그래서 capture 스크립트는 모두 `--out`을 받아야 한다.
> - WSL2(NAT)에서는 `raspberrypi.local`이 Pi로 풀리지 않는다(통신사 DNS가 대신 응답). `PI_HOST`에는 IP를 쓴다.

### 0.1 준비 (1회)
```bash
# Pi에서
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-picamera2 python3-numpy rpicam-apps
rpicam-hello --list-cameras
```
기대 출력: `0 : imx708 [4608x2592 10-bit RGGB] ...` 형태와 모드 목록(1536x864, 2304x1296, 4608x2592 등).
Bayer 순서나 모드 목록이 이와 달라도 그대로 기록한다.

```bash
# PC 저장소 루트에서 (<pi-host>는 Pi 주소)
ssh <pi-host> 'mkdir -p ~/cctvlog/capture'
scp capture/pi_probe.py capture/requirements-pi.txt <pi-host>:~/cctvlog/capture/
```

### 0.2 확인 스크립트 실행
- 촬영 조건: 카메라를 고정하고 움직임 없는 실내 장면. 다른 카메라 프로그램(rpicam-*, 다른 Picamera2 스크립트)은 모두 종료.
```bash
# Pi에서
cd ~/cctvlog/capture
python3 pi_probe.py --out ~/pi_probe --sensor-hdr
```
- 기대 출력: 단계마다 `[OK  ] <step>`이 나오고 마지막에 `failed steps: none`. 1~3분 정도 걸린다(sensor_modes를 조회하는 데 시간이 걸림).
- 일부 단계가 `FAIL`이어도 괜찮다. 오류와 traceback이 보고서에 남으니 그대로 가져온다.
- `sensor_hdr` 단계는 끝날 때 HDR을 끈다. 이 단계가 FAIL이면 다음 작업 전에 Pi를 재부팅한다.

### 0.3 결과 전송
```bash
# PC 저장소 루트에서
mkdir -p data/pi_probe && scp -r <pi-host>:~/pi_probe data/pi_probe/20261120
```
결과 파일:
| 파일 | 내용 |
|---|---|
| `pi_probe_report.json` | 버전, sensor_modes, RAW 포맷 시도 결과, raw+main 동시 획득, 노출 지연, HDR 모드 |
| `tuning/<target>__imx708*.json` | 튜닝 파일 사본 (pisp = Pi 5) |
| `sample/000000.npy` / `.json` / `_isp.png` | 실제 RAW 샘플 1장 (C++ npy 로더 실데이터 확인용) |

### 0.4 이후 Phase를 위한 준비물 (지금 촬영하지 않음)
- 삼각대 또는 고정 마운트 (브래킷은 정지 장면 한정)
- 균일 광원: 확산판(또는 흰 아크릴/종이) + 밝기 조절 가능한 LED. Phase 1 PTC에서 밝기 10단계, Phase 2 플랫필드 15단계 이상
- 다크 프레임용 렌즈 캡 또는 완전 차광
- 고휘도 대비 장면: 실내 창문 역광 등. Phase 2·5
- 인쇄 번호판 20장 이상 + 강한 조명. Phase 6

### 0.5 2차 확인 (게인·화이트 레벨·제어 목록)
- 촬영 조건: 카메라를 고정하고, **화면 일부에 밝은 광원**(스탠드 전구, 창밖 하늘 등)이 직접 들어오게 한다. 나머지는 보통 실내.
```bash
# PC (자동화)
scripts/pi.sh probe --steps tuning_files,controls,manual_gain,white_level
# 수동으로 할 때
scp capture/pi_probe.py <pi-host>:~/cctvlog/capture/                       # PC
cd ~/cctvlog/capture && python3 pi_probe.py --out ~/pi_probe2 --steps tuning_files,controls,manual_gain,white_level   # Pi
scp -r <pi-host>:~/pi_probe2 data/pi_probe/20261120_2                      # PC
```
- 기대 출력: `[OK  ] manual_gain`, `[OK  ] white_level`, 마지막에 `failed steps: none`. `manual_gain`은 카메라를 10~20번 다시 여닫아서 1~2분 걸린다.

## 1. 설치 버전 (확인: 2026-11-20, `pi_probe.py` 1차, `data/pi_probe/20261120/`)
| 항목 | 값 |
|---|---|
| 보드 | Raspberry Pi 5 Model B Rev 1.0 |
| OS | Debian GNU/Linux 13 (trixie) |
| libcamera | v0.8.0+rpt20261104 |
| rpicam-apps | 1.15.0 |
| python3-picamera2 | 0.3.39-1 |

## 2. 카메라와 센서 모드
- **모델: `imx708_wide` (Camera Module 3 Wide)**. `camera_properties`: `Rotation = 180`, `ColorFilterArrangement = 0`.
- `Picamera2.sensor_modes` (일반 모드). 세 모드 모두 `format = SRGGB10_CSI2P`, `unpacked = SRGGB10`, 10bit:

| size | fps | exposure_limits (µs) |
|---|---|---|
| 1536×864 | 120.13 | 9 ~ 77,208,145 |
| **2304×1296** (기준) | 56.03 | 13 ~ 112,015,096 |
| 4608×2592 | 14.35 | 26 ~ 220,416,802 |

- **센서 HDR 모드**: `picamera2.devices.imx708.IMX708(cam).set_sensor_hdr_mode(True)`로 켠다. 켜면 모드가 **2304×1296 하나뿐이고 30 fps, 10bit**다. 확인이 끝나면 다시 껐다(`reset_to_hdr_off = True`).

## 3. RAW 포맷
| 요청 `raw={"format": ...}` | 실제 `camera_configuration()["raw"]` | 배열 |
|---|---|---|
| 지정 안 함 / `SRGGB10_CSI2P` | `BGGR_PISP_COMP1`, stride 2304 | uint8 (1296, 2304), **압축** — 사용 금지 |
| **`SRGGB10`** (= mode `unpacked`) / `SRGGB16` | **`SBGGR16`**, stride 4608 | uint8 (1296, 4608) → `.view(np.uint16)` → (1296, 2304) |

- **MSB 정렬 확인**: `SBGGR16`에서 하위 6bit가 0인 화소 비율이 1.0 → `>> 6`으로 10bit를 얻는다.
- **Bayer 순서: 실제 RAW는 `BGGR`**이고, `sensor_modes`에 보이는 `SRGGB`와 다르다. **확인(v4l2)**: 센서 subdev의 `horizontal_flip = 1`, `vertical_flip = 1`, `camera_sensor_rotation = 180`이다. 원래 배열 RGGB가 상하좌우로 뒤집혀 BGGR이 된다.
  → **Bayer 순서는 반드시 `camera_configuration()["raw"]["format"]`에서 읽는다.** `sensor_modes` 값을 쓰면 안 된다.
- 실데이터 C++/NumPy npy 비트 동일성: `sample/000000.npy`(uint16 2304×1296)로 통과.

## 4. RAW + main 동시 획득
- `req = picam2.capture_request()` → `req.make_array("raw")`, `req.make_array("main")`, `req.get_metadata()` → `req.release()`.
- main `RGB888` (1296, 2304, 3) uint8. 한 request에 metadata(`SensorTimestamp`)가 하나다. 같은 프레임이라는 것은 request 구조상 보장되며, 이를 따로 검증하지는 않았다.
- 2304×1296 모드의 `FrameDuration`은 17,849 µs (≈ 56 fps).
- 메타데이터 키: AeState, AfPauseState, AfState, AnalogueGain, ColourCorrectionMatrix, ColourGains, ColourTemperature, ControlListSequence, DigitalGain, ExposureTime, FocusFoM, FrameDuration, FrameWallClock, LensPosition, Lux, ScalerCrop, SensorBlackLevels, SensorTemperature, SensorTimestamp.

## 5. 노출 변경 지연
- 실행 중에 `set_controls({"ExposureTime": t})`를 하면 `capture_metadata()` 기준으로 **5프레임 뒤**에 반영된다(`buffer_count = 2`, 4회 시도 모두 동일).
- 실제 노출은 라인 단위로 양자화된다: 5000→4996, 10000→9993, 20000→19987, 40000→39975 (−0.07%). 2% 기준 안이다. **합성에는 메타데이터의 실제 노출을 쓴다.**

## 6. 튜닝 파일
- 위치: `/usr/share/libcamera/ipa/rpi/pisp/` (Pi 5), `vc4/` (Pi 4 이하). imx708, imx708_noir, imx708_wide, imx708_wide_noir가 있다.
- **이 카메라에 맞는 파일은 `pisp/imx708_wide.json`**이다(libcamera는 센서 모델 이름으로 고른다). imx708.json과 내용이 다르다.
- 구조 (version 2.0, target pisp): `algorithms`는 `{이름: 본문}` 딕셔너리의 리스트다.
  - `rpi.ccm.ccms[] = {ct, ccm[9]}`, 행 우선 3×3. ct = 2868, 2965, 3603, 4620, 5901, 7610 K.
  - `rpi.alsc`: `calibrations_Cr[]`/`calibrations_Cb[] = {ct, table[1024]}`, ct = 3000, 5000 K. `luminance_lut[1024]`(1.001~4.716), `luminance_strength = 0.65`, omega 1.3, n_iter 100, sigma 0.00152, sigma_Cb 0.00172.
  - 1024 = 32×32 격자로 **추정**한다. 격자 크기와 방향(행 우선 여부)은 libcamera PiSP 소스로 확인 필요.
  - `rpi.black_level.black_level = 4096` (16bit 스케일).
- Wide 렌즈는 luminance_lut 최대값이 4.716(imx708: 3.041)으로 주변부 감광이 크다. LSC 영향이 크다.

## 7. 블랙/화이트 레벨, 게인 (2차 확인: `data/pi_probe/20261120_2/`)
- `SensorBlackLevels = (4096, 4096, 4096, 4096)` (16bit 스케일) → **10bit 64**. 어두운 샘플의 평균은 65.04.
- **화이트 레벨 = 1023** (10bit). 포화 화소는 정확히 1023으로 몰린다. 예: 400 ms × 16배에서 46%가 1023, 바로 아래 1019~1022는 화소값마다 약 1,000개뿐이다. → 유효 범위는 1023 − 64 = 959 코드.
- **수동 게인**: 요청한 2, 4, 8, 16은 메타데이터와 **정확히 일치**한다(비율 1.0). 제어 4방식 모두 같다(`AeEnable=False` / `ExposureTimeMode=AnalogueGainMode=1`, 각각 config 또는 `set_controls`로 적용).
  - **1.0만 1.1228070259가 된다**(4방식 모두). IMX708 게인 공식 `1024/(1024 − code)`에서 code = 112일 때의 float32 값과 정확히 같다.
  - **확인(v4l2)**: `v4l2-ctl -d /dev/v4l-subdev2 --list-ctrls` → `analogue_gain min=112 max=960`. 따라서 **아날로그 게인 범위는 1.1228 ~ 16.0**이다(1024/64 = 16).
  - `DigitalGain = 1.0006`은 항상 일정하다. 센서 `digital_gain = 256`(= 1.0)이므로 ISP 쪽 값이고 RAW에는 영향이 없다.
- AWB: `AwbEnable=False`일 때 `ColourGains = [1.990, 1.928]`, `ColourTemperature = 4000`.

## 7a. V4L2 서브디바이스 (확인: 2026-11-20, `scripts/pi.sh exec`)
| 장치 | 이름 | 주요 값 |
|---|---|---|
| `/dev/v4l-subdev2` | imx708_wide | analogue_gain 112~960, digital_gain 256~65535 (기본 256), hflip=1, vflip=1, rotation 180, wide_dynamic_range(센서 HDR) 0, pixel_rate 595.2 MHz |
| `/dev/v4l-subdev3` | dw9807 (초점 VCM) | focus_absolute 0~1023 |
| `/dev/v4l-subdev0`, `1` | csi2, pisp-fe | |

커널 6.18.50+rpt-rpi-2712.

## 7b. 제어 목록 (`camera_controls`, configure 전 기본 모드 기준)
| 제어 | min | max | default | 메모 |
|---|---|---|---|---|
| `ExposureTimeMode` / `AnalogueGainMode` | 0 | 1 | 0 | 1 = Manual |
| `AeEnable` | False | True | True | 호환용. 위 Mode 제어와 같은 결과 |
| `AnalogueGain` | 1.0 | 16.0 | 1.0 | 실제 하한은 1.1228 (7절) |
| `ExposureTime` (µs) | 1 | 66,666 | 20,000 | 범위는 구성에 따라 다르다. still 구성에서는 400 ms도 적용됨(399,991 µs) |
| `FrameDurationLimits` (µs) | 33,333 | 250,000,000 | 33,333 | |
| `AfMode` | 0 | 2 | 0 | 0 = Manual |
| `LensPosition` (dioptre) | 0.0 | 32.0 | 1.0 | 촬영 시 고정하고 기록 |
| `ColourGains` | 0.0 | 32.0 | — | |
| `HdrMode` | 0 | 4 | 0 | **ISP(소프트웨어) HDR**이다. 센서 HDR 모드와 다르며 항상 0으로 둔다 |

전체 목록: AeConstraintMode, AeEnable, AeExposureMode, AeFlickerMode, AeFlickerPeriod, AeMeteringMode, AfMetering, AfMode, AfPause, AfRange, AfSpeed, AfTrigger, AfWindows, AnalogueGain, AnalogueGainMode, AwbEnable, AwbMode, Brightness, CnnEnableInputTensor, ColourCorrectionMatrix, ColourGains, ColourTemperature, Contrast, ExposureTime, ExposureTimeMode, ExposureValue, FrameDurationLimits, HdrMode, LensPosition, NoiseReductionMode, Saturation, ScalerCrop, ScalerCrops, Sharpness, StatsOutputEnable, SyncFrames, SyncMode.

## 8. 미확인 / 다음 확인
- [x] `camera_controls` 목록 (7b절)
- [x] 수동 게인 반영: 2~16은 정확, 1.0은 1.1228로 하한 (7절)
- [x] 포화 RAW 값 = 1023 (7절)
- [x] 튜닝 파일: Picamera2/libcamera가 `pisp/imx708_wide.json`을 로드함
- [x] 최소 게인 code 112 (v4l2 `analogue_gain min=112 max=960`)
- [ ] ALSC 격자 크기·방향 (libcamera 소스)
- [x] BGGR은 Rotation 180에 따른 센서 hflip+vflip 때문 (v4l2)

## 9. 촬영 스크립트 `capture/capture_raw.py` (확인: 2026-11-20)
```bash
# PC에서 (scripts/pi.sh job이 --out을 넣고 결과를 회수한다)
scripts/pi.sh job <name> --dest data/raw/<YYYYMMDD>_<scene>_<mode> capture_raw.py \
    --mode {single|bracket|sensor_hdr} --exposures-us 16000,4000,1000 --gain 1.1228 --count N --scene <scene>
```
- 구성:
  - still 구성, `sensor={output_size, bit_depth}`, `main=RGB888`, `raw={"format": mode["unpacked"]}`, `buffer_count=2`
  - 제어: `ExposureTimeMode=1`, `AnalogueGainMode=1`, `AfMode=0`, `HdrMode=0`
- WB: `--colour-gains`가 없으면 AWB를 30프레임 돌린 뒤 그 값으로 고정한다(`session.json` `locked_colour_gains`).
- 프레임 선택: request의 메타데이터 노출이 요청값 ±2% 안이면 저장하고 아니면 버린다. 실측으로는 노출을 바꿀 때마다 5프레임을 버린다.
- 브래킷 세트는 3노출 기준 768 ms 걸린다(16/4/1 ms, 버리는 프레임 포함).
- 실제 노출 양자화 (2304×1296):

| 모드 | 요청 → 실제 (µs) |
|---|---|
| 일반 | 16000 → 15979, 4000 → 3981, 1000 → 988 (−1.2%) |
| 센서 HDR | 10000 → 9873 (−1.3%) |

  노출이 짧을수록 상대 오차가 커진다.
- 센서 HDR 모드는 30 fps 모드로 촬영되고, 끝나면 `wide_dynamic_range = 0`으로 복구된다(확인).
- `sensor_flip`은 촬영 때 v4l2에서 읽어 session과 각 프레임 JSON에 기록한다. 현재 h = v = True.
