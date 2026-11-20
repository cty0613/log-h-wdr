"""capture/rawutil.py (Pi 촬영 순수 로직) 테스트. Picamera2 없이 PC에서 실행."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "capture"))
import rawutil as ru


def test_bayer_order_and_unpacked_format():
    assert ru.bayer_order_from_format("SBGGR16") == "BGGR"
    assert ru.bayer_order_from_format("SRGGB10_CSI2P") == "RGGB"
    assert ru.bayer_order_from_format("BGGR_PISP_COMP1") == "BGGR"
    assert ru.is_unpacked16("SBGGR16")
    assert not ru.is_unpacked16("BGGR_PISP_COMP1")
    assert not ru.is_unpacked16("SRGGB10_CSI2P")
    with pytest.raises(ValueError):
        ru.bayer_order_from_format("YUV420")


def test_raw_to_uint16_from_pi_byte_layout():
    # Pi 실측 레이아웃: uint8 (H, stride=2W) 배열, 10bit 값이 MSB 정렬(<<6)
    rng = np.random.default_rng(0)
    w, h = 2304, 4
    vals = rng.integers(0, 1024, size=(h, w), dtype=np.uint16)
    arr = (vals << 6).view(np.uint8).reshape(h, 2 * w)
    out = ru.raw_to_uint16(arr, w, 10)
    assert out.dtype == np.uint16 and out.flags.c_contiguous
    assert np.array_equal(out, vals)


def test_raw_to_uint16_with_row_padding():
    w, h, stride_px = 6, 3, 8
    vals = np.arange(h * stride_px, dtype=np.uint16).reshape(h, stride_px) % 1024
    arr = (vals << 6).view(np.uint8).reshape(h, 2 * stride_px)
    assert np.array_equal(ru.raw_to_uint16(arr, w, 10), vals[:, :w])


def test_raw_to_uint16_rejects_non_msb_aligned_or_packed():
    arr = np.full((2, 8), 1, dtype=np.uint16).view(np.uint8).reshape(2, 16)
    with pytest.raises(ValueError, match="MSB"):
        ru.raw_to_uint16(arr, 8, 10)
    with pytest.raises(ValueError, match="too short"):
        ru.raw_to_uint16(np.zeros((2, 8), np.uint8), 8, 10)  # 압축(8bit/px) 버퍼


def test_black_level_from_metadata():
    assert ru.black_level_from_metadata({"SensorBlackLevels": (4096,) * 4}, 10) == 64
    assert ru.black_level_from_metadata({"SensorBlackLevels": (4096, 4160, 4096, 4096)}, 10) == [
        64,
        65,
        64,
        64,
    ]
    assert ru.black_level_from_metadata({}, 10) is None


def test_exposure_and_gain_match():
    assert ru.exposure_matches(9993, 10000, 0.02)  # 실측 라인 양자화 -0.07%
    assert not ru.exposure_matches(4996, 10000, 0.02)
    assert not ru.exposure_matches(None, 10000, 0.02)
    assert ru.gain_matches(1.1228070259, 1.1228)
    assert not ru.gain_matches(1.1228070259, 1.0)  # 최소 게인 하한 (pi_notes 7절)


def test_bracket_ratio_summary_uses_long_exposure_reference():
    s = ru.bracket_ratio_summary([16000, 4000, 1000], [15988, 3996, 999], 0.02)
    assert s["long_index"] == 0
    assert s["ratios_requested"] == [1.0, 4.0, 16.0]
    assert s["ratio_within_tolerance"]
    assert s["max_abs_ratio_rel_error"] < 0.002

    # 순서와 무관하게 가장 긴 노출 기준, 3% 어긋나면 실패
    s = ru.bracket_ratio_summary([1000, 16000], [1030, 16000], 0.02)
    assert s["long_index"] == 1
    assert s["ratios_requested"] == [16.0, 1.0]
    assert not s["ratio_within_tolerance"]


def test_frame_json_has_required_fields():
    md = {
        "SensorTimestamp": 123,
        "ExposureTime": 9993,
        "AnalogueGain": 1.1228,
        "DigitalGain": 1.0006,
        "ColourGains": (1.99, 1.93),
        "SensorBlackLevels": (4096,) * 4,
        "ColourTemperature": 4000,
        "SensorTemperature": 34.0,
        "LensPosition": 1.0,
    }
    fj = ru.frame_json(
        frame_index=0,
        md=md,
        size=(2304, 1296),
        bit_depth=10,
        hdr=False,
        bayer_order="BGGR",
        exposure_requested_us=10000,
        raw_format="SBGGR16",
    )
    required = {
        "frame_index",
        "timestamp_ns",
        "sensor_mode",
        "bayer_order",
        "exposure_time_us_requested",
        "exposure_time_us",
        "analogue_gain",
        "digital_gain",
        "colour_gains",
        "black_level",
        "white_level",
        "colour_temperature",
        "sensor_temperature",
        "notes",
    }
    assert required <= fj.keys()
    assert fj["black_level"] == 64 and fj["white_level"] == 1023
    assert fj["sensor_mode"] == {"size": [2304, 1296], "bit_depth": 10, "hdr": False}
    assert fj["colour_gains"] == [1.99, 1.93]
    # 메타데이터에 없는 값은 None (추정 금지)
    assert (
        ru.frame_json(
            frame_index=1,
            md={},
            size=(2, 2),
            bit_depth=10,
            hdr=False,
            bayer_order="BGGR",
            exposure_requested_us=1,
            raw_format="SBGGR16",
        )["sensor_temperature"]
        is None
    )
