"""capture_raw.py의 순수 로직 (Picamera2에 의존하지 않음 → PC에서 pytest로 검증).

근거 사실은 docs/pi_notes.md:
- 비압축 RAW는 실제 포맷 `S????16`, uint8 (H, stride) 배열 → uint16 view, MSB 정렬
- Bayer 순서는 실제 raw 포맷 문자열에서 읽는다 (센서 flip 때문에 sensor_modes와 다름)
- SensorBlackLevels는 16bit 스케일, white level은 2^bit_depth - 1 (포화 실측)
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

BAYER_ORDERS = ("RGGB", "GRBG", "GBRG", "BGGR")


def jsonable(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return repr(obj)


def bayer_order_from_format(fmt: str) -> str:
    for order in BAYER_ORDERS:
        if order in fmt:
            return order
    raise ValueError(f"cannot determine Bayer order from raw format {fmt!r}")


def is_unpacked16(fmt: str) -> bool:
    """`SBGGR16` 같은 비압축 16bit 포맷인지. `*_PISP_COMP*`, `*_CSI2P`는 False."""
    return fmt.startswith("S") and fmt.endswith("16") and bayer_order_from_format(fmt) in fmt


def raw_to_uint16(arr: np.ndarray, width: int, bit_depth: int) -> np.ndarray:
    """make_array("raw") 결과 → right-shift 완료 uint16 (H, W), C-연속.

    MSB 정렬 가정을 검사한다: 하위 (16 - bit_depth)bit가 0이 아니면 오류.
    """
    if arr.dtype == np.uint8:
        if arr.shape[1] < 2 * width:
            raise ValueError(f"raw row too short for 16bit: {arr.shape} width={width}")
        v = np.ascontiguousarray(arr).view(np.uint16)[:, :width]
    elif arr.dtype == np.uint16:
        v = arr[:, :width]
    else:
        raise ValueError(f"unexpected raw dtype {arr.dtype}")
    shift = 16 - bit_depth
    if shift < 0:
        raise ValueError(f"bit_depth {bit_depth} > 16")
    if shift and np.any(v & ((1 << shift) - 1)):
        raise ValueError("raw LSB padding is not zero: data is not MSB-aligned 16bit")
    return np.ascontiguousarray(v >> shift).astype(np.uint16, copy=False)


def black_level_from_metadata(md: Mapping[str, Any], bit_depth: int) -> int | list[int] | None:
    """SensorBlackLevels(16bit 스케일) → bit_depth 스케일. 4채널이 같으면 정수 하나."""
    levels = md.get("SensorBlackLevels")
    if not levels:
        return None
    shift = 16 - bit_depth
    vals = [int(v) >> shift for v in levels]
    return vals[0] if len(set(vals)) == 1 else vals


def exposure_matches(actual_us: float | None, requested_us: float, tol: float) -> bool:
    return actual_us is not None and abs(actual_us - requested_us) <= tol * requested_us


def gain_matches(actual: float | None, requested: float, tol: float = 0.005) -> bool:
    return actual is not None and abs(actual - requested) <= tol * requested


def bracket_ratio_summary(
    requested_us: Sequence[float], actual_us: Sequence[float], tol: float
) -> dict[str, Any]:
    """브래킷 노출비 검사 (Phase 0 수락 기준: 실제 노출비가 설정값과 tol 이내).

    노출비는 가장 긴 노출 기준 R_i = t_long / t_i (CLAUDE.md 6.1).
    """
    if len(requested_us) != len(actual_us) or not requested_us:
        raise ValueError("requested/actual length mismatch")
    i_long_req = int(np.argmax(requested_us))
    t_long_req = requested_us[i_long_req]
    t_long_act = actual_us[i_long_req]
    ratios_req = [t_long_req / t for t in requested_us]
    ratios_act = [t_long_act / t for t in actual_us]
    errs = [(a - r) / r for a, r in zip(ratios_act, ratios_req, strict=True)]
    return {
        "long_index": i_long_req,
        "ratios_requested": ratios_req,
        "ratios_actual": ratios_act,
        "ratio_rel_errors": errs,
        "max_abs_ratio_rel_error": max(abs(e) for e in errs),
        "tolerance": tol,
        "ratio_within_tolerance": all(abs(e) <= tol for e in errs),
    }


def frame_json(
    *,
    frame_index: int,
    md: Mapping[str, Any],
    size: Sequence[int],
    bit_depth: int,
    hdr: bool,
    bayer_order: str,
    exposure_requested_us: float,
    raw_format: str,
    notes: str = "",
) -> dict[str, Any]:
    """CLAUDE.md 5.2 프레임 JSON. 얻을 수 없는 값은 None. 원본 메타데이터는 `metadata`에 보존."""
    return jsonable(
        {
            "frame_index": frame_index,
            "timestamp_ns": md.get("SensorTimestamp"),
            "sensor_mode": {"size": list(size), "bit_depth": bit_depth, "hdr": hdr},
            "bayer_order": bayer_order,
            "exposure_time_us_requested": exposure_requested_us,
            "exposure_time_us": md.get("ExposureTime"),
            "analogue_gain": md.get("AnalogueGain"),
            "digital_gain": md.get("DigitalGain"),
            "colour_gains": md.get("ColourGains"),
            "black_level": black_level_from_metadata(md, bit_depth),
            "white_level": (1 << bit_depth) - 1,
            "colour_temperature": md.get("ColourTemperature"),
            "sensor_temperature": md.get("SensorTemperature"),
            "notes": notes,
            "raw_format": raw_format,
            "lens_position": md.get("LensPosition"),
            "metadata": dict(md),
        }
    )
