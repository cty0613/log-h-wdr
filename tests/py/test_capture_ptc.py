"""capture/capture_ptc.py 순수 로직 테스트 (Picamera2 없이 PC에서 실행)."""

import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "capture"))
import capture_ptc as ptc
import flatsession as fs


def test_parse_levels() -> None:
    assert ptc.parse_levels("0.01,0.1, 1") == (0.01, 0.1, 1.0)
    for bad in ("", "0.1,0.1", "0.2,0.1", "0,0.5"):
        with pytest.raises(ValueError):
            ptc.parse_levels(bad)


def test_default_levels_reach_saturation_of_dim_channels() -> None:
    # 플랫필드 판정: R·B는 초록의 약 0.5배 → 최고 단계가 2 이상이어야 R·B도 포화
    assert ptc.DEFAULT_LEVELS[0] <= 0.01
    assert ptc.DEFAULT_LEVELS[-1] >= 2.0
    assert len(ptc.DEFAULT_LEVELS) >= 10  # CLAUDE.md Phase 1: 노출 10단계 이상


def test_distinguishable_uses_tolerance_and_quantization_margin() -> None:
    tol = 0.02
    # 짧은 노출: 허용치 25 µs + 여유 27 µs
    assert not ptc.distinguishable(35, 69, tol)
    assert ptc.distinguishable(35, 173, tol)
    # 긴 노출: 2% 허용치가 지배
    assert not ptc.distinguishable(50000, 51000, tol)
    assert ptc.distinguishable(50000, 60000, tol)


def test_stale_frame_never_accepted_for_planned_steps() -> None:
    """계획된 연속 단계에서, 이전 설정으로 찍힌 프레임(라인 양자화 ±20 µs 포함)이 새 설정의
    exposure_close 판정을 통과하면 안 된다."""
    tol = 0.02
    for t_sat in (3457.0, 5630.0, 48797.0, 79339.0, 400.0):
        steps, _ = ptc.plan_exposures(t_sat, ptc.DEFAULT_LEVELS, tol)
        for prev, new in itertools.pairwise(steps):
            p, n = prev["exposure_us_requested"], new["exposure_us_requested"]
            for q in (-20.0, 0.0, 20.0):
                assert not fs.exposure_close(p + q, n, tol), (t_sat, p, n, q)


def test_plan_exposures_clamps_and_skips() -> None:
    steps, skipped = ptc.plan_exposures(3457.0, ptc.DEFAULT_LEVELS, 0.02)
    ts = [s["exposure_us_requested"] for s in steps]
    assert ts == sorted(ts)
    assert ts[0] == round(0.01 * 3457)
    assert [s["level"] for s in skipped] == [0.02]  # 35 → 69 µs는 구분 불가
    assert ts[-1] == round(2.2 * 3457)
    # 너무 밝으면 최소 노출로 잘리고, 같은 값이 된 단계는 빠진다
    steps, skipped = ptc.plan_exposures(500.0, ptc.DEFAULT_LEVELS, 0.02)
    assert steps[0]["exposure_us_requested"] == fs.EXPOSURE_MIN_US
    assert skipped
    # 상한
    steps, _ = ptc.plan_exposures(1e6, (0.5, 2.0), 0.02)
    assert steps[-1]["exposure_us_requested"] == ptc.EXPOSURE_MAX_US


def test_next_probe_exposure_converges_on_linear_sensor() -> None:
    t_sat = 48797.0
    t = 20000.0 * 16  # 아주 밝은 쪽에서 시작
    for _ in range(ptc.PROBE_MAX):
        m = min(t / t_sat, 1.0)
        nxt = ptc.next_probe_exposure(t, m)
        if nxt is None:
            assert t / m == pytest.approx(t_sat)
            return
        t = nxt
    pytest.fail("did not converge")


def test_next_probe_exposure_from_dark_side() -> None:
    t_sat, t = 3457.0, 13.0
    for _ in range(ptc.PROBE_MAX):
        m = min(t / t_sat, 1.0)
        nxt = ptc.next_probe_exposure(t, m)
        if nxt is None:
            assert ptc.PROBE_LOW <= m < ptc.PROBE_HIGH
            return
        t = nxt
    pytest.fail("did not converge")


def test_dark_exposures_from_plan() -> None:
    plan = {
        "gains": {
            "g16": {"steps": [{"exposure_us_requested": t} for t in (35, 173, 7605)]},
            "g1": {"steps": [{"exposure_us_requested": 488}]},
        }
    }
    assert ptc.dark_exposures(plan, "g16") == [35, 7605]
    assert ptc.dark_exposures(plan, "g1") == [488]


def test_parse_args_requires_plan_for_dark() -> None:
    with pytest.raises(SystemExit):
        ptc.parse_args(["--part", "dark", "--out", "x"])
    a = ptc.parse_args(["--part", "flat", "--out", "x", "--gains", "g1,g16"])
    assert a.gains == ["g1", "g16"]
    assert a.levels == ptc.DEFAULT_LEVELS
    with pytest.raises(SystemExit):
        ptc.parse_args(["--part", "flat", "--out", "x", "--gains", "g2"])


class FakeSession:
    """선형 센서 흉내: 신호 = 노출 / t_sat(게인) (1.0에서 포화). 실제 노출은 요청 그대로."""

    def __init__(self, t_sat_g1: float, dark: bool = False) -> None:
        self.t_sat_g1, self.dark = t_sat_g1, dark
        self.gain, self.exposure = fs.GAIN_MIN, 1000.0
        self.cap = type("Cap", (), {"args": type("A", (), {"tolerance": 0.02})()})()

    def set(self, gain: float, exposure_us: float) -> None:
        self.gain, self.exposure = gain, float(exposure_us)

    def grab(self, out_dir: Path, index: int, step: str) -> dict:
        t_sat = self.t_sat_g1 * fs.GAIN_MIN / self.gain
        m = 0.0 if self.dark else min(self.exposure / t_sat, 1.0)
        return {
            "file": f"{out_dir.name}/{index:06d}.npy",
            "gain": self.gain,
            "exposure_time_us": self.exposure,
            "roi_means": [0.8 * m, m, m, 0.5 * m],
            "roi_max_channel_mean": m,
        }


def test_run_flat_and_dark_with_fake_sensor(tmp_path: Path) -> None:
    args = ptc.parse_args(["--part", "flat", "--out", str(tmp_path), "--pairs", "2"])
    run = ptc.Run(FakeSession(48797.0), tmp_path)  # type: ignore[arg-type]
    plan = {"gains": {}}
    ptc.run_flat(run, args, plan)
    assert set(plan["gains"]) == {"g1", "g4", "g16"}
    for label, g in ptc.GAINS.items():
        expect = 48797.0 * fs.GAIN_MIN / g
        assert plan["gains"][label]["t_sat_us"] == pytest.approx(expect, rel=1e-6)
    flats = [f for f in run.frames if f["part"] == "flat"]
    n_steps = sum(len(v["steps"]) for v in plan["gains"].values())
    assert len(flats) == n_steps * 2 * 2
    assert {f["frame"] for f in flats} == {0, 1}
    mons = [f for f in run.frames if f["part"] == "monitor"]
    assert [f["tag"] for f in mons][::2] == ["start", "after_g1", "after_g4", "after_g16"]
    assert all(f["roi_max_channel_mean"] == pytest.approx(0.5) for f in mons)

    dargs = ptc.parse_args(
        ["--part", "dark", "--out", str(tmp_path), "--plan", "p.json", "--dark-frames", "3"]
    )
    drun = ptc.Run(FakeSession(48797.0, dark=True), tmp_path)  # type: ignore[arg-type]
    leak = ptc.run_dark(drun, dargs, plan)
    assert len(leak) == 6 and all(v == 0 for v in leak.values())
    assert len(drun.frames) == 6 * 3

    # 빛이 새면 중단
    lrun = ptc.Run(FakeSession(48797.0), tmp_path)  # type: ignore[arg-type]
    with pytest.raises(SystemExit, match="light leak"):
        ptc.run_dark(lrun, dargs, plan)
