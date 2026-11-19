# Raspberry Pi 5 + Camera Module 3 메모

Pi에서 직접 확인한 사실만 적는다. 추측 금지 (CLAUDE.md 2절, 8절). 각 항목에 확인 날짜와 명령을 남긴다.

## 0. Pi 작업 안내 (Phase 0 확인 단계)

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

## 1. 설치 버전
- [ ] Raspberry Pi OS 버전 (`cat /etc/os-release`):
- [ ] libcamera 버전 (`rpicam-hello --version`):
- [ ] Picamera2 버전 (`python3 -c "import picamera2, importlib.metadata as m; print(m.version('picamera2'))"`):

## 2. 센서 모드 (`Picamera2().sensor_modes`)
- [ ] 모드 목록 (size, bit_depth, format, fps 범위):
- [ ] 센서 HDR 모드 전환 방법과 그때의 모드 목록:

## 3. RAW 포맷
- [ ] unpacked(비압축 16bit) 요청 방법과 실제 format 문자열:
- [ ] MSB 정렬 확인 (LSB 0 패딩, `>> (16 - bit_depth)`):
- [ ] Bayer 순서 (캡처 설정에서 읽은 값):

## 4. RAW + main 동시 획득
- [ ] 한 request에서 raw와 main을 함께 받는 구성 코드:
- [ ] 동일 프레임 확인 방법과 결과:

## 5. 노출 변경 지연
- [ ] 노출 설정 후 메타데이터의 `ExposureTime`이 바뀌기까지 프레임 수:

## 6. 튜닝 파일
- [ ] IMX708 튜닝 JSON 경로:
- [ ] CCM 키 경로 (`rpi.ccm` …):
- [ ] ALSC 키 경로 (`rpi.alsc` …), 격자 크기:

## 7. 블랙/화이트 레벨
- [ ] 메타데이터 `SensorBlackLevels` 값과 스케일:
