# 영상감시용 WDR(HDR) 대응 노이즈 매칭 Log 곡선 및 보관 스트림

Raspberry Pi 5 + Camera Module 3 RAW에서 브래킷 HDR 합성/DCG 시뮬레이션으로 DR을 확보하고,
합성 후 노이즈 분포에 맞춘 구간별 노이즈 매칭 Log 곡선(**H-Log**)으로 10bit 보관 스트림을 만드는 실험 저장소.
개발 명세는 [CLAUDE.md](CLAUDE.md), 결정 기록은 [docs/decisions.md](docs/decisions.md).
원리와 엔지니어링 설명은 [기술백서](docs/whitepaper/README.md)에 있다.

- **C++** (`core/`, `apps/`): 화소 처리, HDR 합성, 곡선, ISP, 코덱
- **Python** (`python/cctvlog/`, `experiments/`): 파라미터 계산, 실험 관리, 분석
- **Pi** (`capture/`): 촬영 전용, C++ 빌드 안 함

## 1. 시스템 패키지 (Ubuntu, 1회)

```bash
sudo apt update
sudo apt install -y build-essential ninja-build pkg-config curl zip unzip tar git \
    nasm autoconf autoconf-archive automake libtool bison flex \
    python3-venv python3-dev
```

## 2. vcpkg (1회)

```bash
git clone https://github.com/microsoft/vcpkg.git ~/vcpkg
~/vcpkg/bootstrap-vcpkg.sh -disableMetrics
echo 'export VCPKG_ROOT="$HOME/vcpkg"' >> ~/.bashrc && source ~/.bashrc
```

버전은 `vcpkg.json`의 `builtin-baseline`으로 고정된다.

## 3. 빌드·테스트

```bash
# C++ (첫 configure는 FFmpeg·x265·OpenCV 빌드로 오래 걸린다)
cmake --preset release && cmake --build --preset release
ctest --preset release

# Python
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && pytest tests/py
```

Python 테스트는 C++ CLI를 `CCTVLOG_BIN` 환경변수, 없으면 `build/release/apps`에서 찾는다.

## 4. Pi 원격 작업 (`scripts/pi.sh`)

```bash
cp .pi.env.example .pi.env        # PI_HOST(IP), PI_USER
scripts/pi.sh setup               # 1회: SSH 키 생성·등록
scripts/pi.sh agent               # 키에 passphrase가 있으면 WSL 시작 후 1회
scripts/pi.sh check               # 연결·카메라 확인
scripts/pi.sh probe [--steps ...] # Pi 환경 확인 → data/pi_probe/<시각>/
scripts/pi.sh job NAME [--dest DIR] SCRIPT.py [ARGS]   # capture/ 동기화 → 실행 → 결과 회수
```

## 5. Phase 1: 센서 특성화 (촬영 → 분석)

준비물과 배치는 [docs/phase1_equipment.md](docs/phase1_equipment.md)에 있다. 결과와 결정은 [docs/decisions.md](docs/decisions.md) 2026-11-21 항목에 있다.

```bash
# 1) 셋업 확인 (약 30초): 깜빡임·밝기·안정성·균일도 판정
scripts/pi.sh job ffcheck_YYYYMMDD_HHMM check_flatfield.py
python -m cctvlog.sensor.flatfield_check data/pi_runs/ffcheck_YYYYMMDD_HHMM

# 2) PTC 촬영: flat(광원 켬) → dark(렌즈 완전히 가림)
scripts/pi.sh job ptc_flat_YYYYMMDD_HHMM capture_ptc.py --part flat --notes "광원: ..."
scripts/pi.sh job ptc_dark_YYYYMMDD_HHMM capture_ptc.py --part dark \
    --plan ../runs/ptc_flat_YYYYMMDD_HHMM/plan.json

# 3) 센서 HDR 모드 스윕 (같은 셋업, 게인 2개로 포화까지)
scripts/pi.sh job sensor_hdr_g1_YYYYMMDD_HHMM capture_sensor_hdr.py --part flat --gain 1.1228 --sweep 858,1000000,36
scripts/pi.sh job sensor_hdr_g16_YYYYMMDD_HHMM capture_sensor_hdr.py --part flat --gain 16 --sweep 858,400000,30

# 4) 분석 (config에 데이터 폴더 지정)
python experiments/p1_ptc.py configs/p1_ptc.yaml               # → configs/noise_single_{g1,g4,g16}.json, results/p1/sensor_summary.md
python experiments/p1_sensor_hdr.py configs/p1_sensor_hdr.yaml # → results/p1/sensor_hdr_summary.md
```

## 6. 저장소 구조

```
core/        C++ 라이브러리 cctvlog_core (include/cctvlog, src/{io,hdr,isp,curves,codec})
apps/        C++ CLI (hdr_merge, dcg_sim, process, encode, decode, restore, curve_eval)
capture/     Pi 촬영 스크립트
python/      cctvlog 패키지 (cli.run_cpp, runner, sensor, calib, ref, metrics, usecase)
experiments/ p0_ ~ p6_ 실험 스크립트 (config yaml만 인자)
configs/     yaml + JSON 파라미터
tests/       cpp/ (GoogleTest), py/ (pytest)
docs/        pi_notes.md, decisions.md, fixed_point.md, phase1_equipment.md, whitepaper/
scripts/     pi.sh (PC → Pi SSH 원격 작업)
data/ results/  (git 제외)
```

모든 C++ CLI는 `--config <json>`과 입출력 경로를 받고 출력 폴더에 `run.json`(버전, git commit, 파라미터, 처리 시간, 라이브러리 버전)을 남긴다.
