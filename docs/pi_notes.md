# Raspberry Pi 5 + Camera Module 3 메모

Pi에서 직접 확인한 사실만 적는다. 추측 금지 (CLAUDE.md 2절, 8절). 각 항목에 확인 날짜와 명령을 남긴다.

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
