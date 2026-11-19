# CCTV-Log — HDR 합성 대응 Log 보관 스트림

Raspberry Pi 5 + Camera Module 3 RAW에서 브래킷 HDR 합성/DCG 시뮬레이션으로 DR을 확보하고,
합성 후 노이즈 분포에 맞춘 구간별 노이즈 매칭 Log 곡선(CCTV-Log)으로 10bit 보관 스트림을 만드는 실험 저장소.
개발 명세는 [CLAUDE.md](CLAUDE.md), 결정 기록은 [docs/decisions.md](docs/decisions.md).

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

## 4. 저장소 구조

```
core/        C++ 라이브러리 cctvlog_core (include/cctvlog, src/{io,hdr,isp,curves,codec})
apps/        C++ CLI (hdr_merge, dcg_sim, process, encode, decode, restore, curve_eval)
capture/     Pi 촬영 스크립트
python/      cctvlog 패키지 (cli.run_cpp, runner, sensor, calib, ref, metrics, usecase)
experiments/ p0_ ~ p6_ 실험 스크립트 (config yaml만 인자)
configs/     yaml + JSON 파라미터
tests/       cpp/ (GoogleTest), py/ (pytest)
docs/        pi_notes.md, decisions.md, fixed_point.md
data/ results/  (git 제외)
```

모든 C++ CLI는 `--config <json>`과 입출력 경로를 받고 출력 폴더에 `run.json`(버전, git commit, 파라미터, 처리 시간, 라이브러리 버전)을 남긴다.
