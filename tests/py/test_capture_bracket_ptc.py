"""capture/capture_bracket_ptc.py 순수 로직 테스트 (Picamera2 없이 PC에서 실행)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "capture"))
import capture_bracket_ptc as cb

T_SAT = 46_100.0  # Phase 1 셋업의 g1 포화 노출 (decisions.md 2026-11-21)


def test_default_levels_cover_spec_and_boundaries() -> None:
    lv = cb.DEFAULT_LEVELS
    assert len(lv) >= 15  # CLAUDE.md 6.3: 밝기 단계 15개 이상
    # 각 경계 띠에 3단계 이상
    assert sum(0.72 <= x <= 0.9 for x in lv) >= 3
    assert sum(2.88 <= x <= 3.6 for x in lv) >= 3
    # 세 구간 모두 띠 밖 점이 있다
    assert any(x < 0.72 for x in lv) and any(0.9 < x < 2.88 for x in lv)
    assert any(x > 3.6 for x in lv)


def test_short_exposure_at_16to1_boundary_above_flicker_limit() -> None:
    # decisions.md 2026-11-22: 16:1 경계(x 0.72~0.9)의 짧은 노출 ≥ 1.7 ms
    steps, _ = cb.plan_steps(T_SAT, cb.DEFAULT_LEVELS, cb.DEFAULT_RATIOS)
    for st in steps:
        if 0.72 <= st["level"] <= 0.9:
            assert st["exposures_us_requested"][-1] >= 1700


def test_plan_skips_out_of_range() -> None:
    steps, skipped = cb.plan_steps(100_000.0, (0.5, 5.0, 20.0), (1.0, 4.0, 16.0))
    assert [s["level"] for s in steps] == [0.5, 5.0]
    assert skipped[0]["level"] == 20.0 and "max" in skipped[0]["reason"]
    steps, skipped = cb.plan_steps(400.0, (0.5, 1.0), (1.0, 16.0))
    assert [s["level"] for s in skipped] == [0.5]  # 200/16 = 12.5 < 13 µs
    assert steps[0]["exposures_us_requested"] == [400.0, 25.0]


def test_default_plan_is_distinguishable_and_fits() -> None:
    steps, skipped = cb.plan_steps(T_SAT, cb.DEFAULT_LEVELS, cb.DEFAULT_RATIOS)
    assert not skipped
    assert max(s["exposures_us_requested"][0] for s in steps) <= cb.EXPOSURE_MAX_US
    for st in steps:
        assert cb.within_set_distinguishable(st["exposures_us_requested"], 0.02)


def test_parse_floats() -> None:
    assert cb.parse_floats("1,4,16") == (1.0, 4.0, 16.0)
    for bad in ("", "4,1", "1,1", "0,1"):
        with pytest.raises(ValueError):
            cb.parse_floats(bad)


def test_bracket_json_format_matches_capture_raw() -> None:
    rows = [
        {"exposure_time_us": t, "gain": 1.1228, "timestamp_ns": i}
        for i, t in enumerate([15979, 3981, 988])
    ]
    bj = cb.bracket_json(0, rows, [16000.0, 4000.0, 1000.0], 0.02)
    assert bj["files"] == ["000000.npy", "000001.npy", "000002.npy"]
    assert bj["exposure_time_us_actual"] == [15979, 3981, 988]
    assert bj["ratios_actual"][2] == pytest.approx(15979 / 988)
