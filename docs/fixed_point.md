# 고정소수점 규칙 (H-Log LUT)

Phase 3에서 채운다. `core/include/cctvlog/fixed.hpp`와 항상 일치해야 한다.

## 1. 정방향 LUT (초안 — Phase 3에서 확정)
- 입력 매핑: 선형 x ∈ [0, x_max] → uint16 인덱스 `i = round_half_up(x / x_max · 65535)`, 범위 밖은 포화.
- 엔트리 수: 65536. 출력: uint16 코드(상위 10bit 유효).
- 반올림: round-half-up. 포화: [0, 2^bits − 1].

## 2. 역방향 LUT
- 엔트리 수: 1024 (10bit 코드) → float 선형값.

## 3. 오차 표
| 곡선 | 입력 구간 | LUT vs float 최대 오차 (10bit LSB) | 위치 |
|---|---|---|---|
| (Phase 3) | | | |
