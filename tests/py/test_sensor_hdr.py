"""cctvlog.sensor.sensor_hdr: 꺾은선 응답 합성 데이터로 경계·구간·겹침 검출 검증."""

from __future__ import annotations

import numpy as np
import pytest

from cctvlog.sensor import sensor_hdr as sh

T_SAT = 46000.0


def response(e: np.ndarray) -> np.ndarray:
    """선형(일반 모드 감도) → 6 ms에서 기울기 1/15 → 100 ms에서 1/40, 1.0에서 포화."""
    k1, k2 = 6000.0, 100000.0
    m = np.where(e <= k1, e / T_SAT, k1 / T_SAT + (e - k1) / T_SAT / 15)
    m2 = k1 / T_SAT + (k2 - k1) / T_SAT / 15
    m = np.where(e <= k2, m, m2 + (e - k2) / T_SAT / 40)
    return np.minimum(m, 1.0)


def test_linear_region_finds_knee_despite_scatter() -> None:
    e = np.geomspace(900, 50000, 30)
    rng = np.random.default_rng(3)
    m = response(e) * (1 + rng.uniform(-0.03, 0.03, e.size))  # 짧은 노출 메타데이터 흔들림
    lin = sh.linear_region(e, m, T_SAT, sh.HdrParams(linear_tol=0.05))
    # 꺾임 직후 점은 기준선 편차가 아직 5% 미만일 수 있다 → 경계는 두 점 사이로 보고된다
    assert lin["last_linear_E_us"] < 6000 * 1.1
    assert 6000 < lin["first_nonlinear_E_us"] < 6000 * 1.3
    assert lin["slope_vs_normal"] == pytest.approx(1.0, abs=0.05)


def test_linear_region_without_knee() -> None:
    e = np.geomspace(900, 5000, 8)
    lin = sh.linear_region(e, e / T_SAT, T_SAT, sh.HdrParams())
    assert lin["first_nonlinear_E_us"] is None
    assert lin["last_linear_E_us"] == pytest.approx(5000)


def test_segment_fit_recovers_breakpoints() -> None:
    e = np.geomspace(900, 900000, 40)
    rng = np.random.default_rng(5)
    m = response(e) * (
        1 + rng.normal(0, 0.002, e.size)
    )  # 잡음 없으면 SSE 0끼리 비교가 돼 BIC 무의미
    seg = sh.segment_fit(e, m, 5)
    assert seg["k"] == 3
    starts = [s["E_us"][0] for s in seg["segments"]]
    assert 6000 < starts[1] < 6000 * 1.4
    assert 100000 < starts[2] < 100000 * 1.4
    slopes = [s["slope_per_us"] * T_SAT for s in seg["segments"]]
    assert slopes == pytest.approx([1.0, 1 / 15, 1 / 40], rel=0.05)


def test_gain_overlap_matches_when_response_depends_on_gain_times_exposure() -> None:
    steps = []
    for g, ts in ((1.1228, np.geomspace(900, 1e6, 20)), (16.0, np.geomspace(900, 1e5, 15))):
        for t in ts:
            e = t * g / 1.1228
            steps.append({"analogue_gain": g, "E_us": e, "level": float(response(np.array(e)))})
    ov = sh.gain_overlap(steps)
    (v,) = ov.values()
    assert v["n"] > 5
    assert v["max_abs_rel_diff"] < 0.02  # 로그 보간 오차만


def test_input_referred_snr_is_continuous_for_pure_companding() -> None:
    """합성 없이 압축만 하는 경우(출력 노이즈도 기울기만큼 줄어듦), 입력 기준 SNR은 경계에서
    연속이어야 한다. 출력 SNR은 경계 뒤 크게 보인다."""
    segs = sh.segment_fit(*(lambda e: (e, response(e)))(np.geomspace(900, 90000, 30)), 4)[
        "segments"
    ]
    fs = 959.0
    steps = []
    for e in (5000.0, 7000.0):
        x = e / T_SAT  # 입력
        sig_in = np.sqrt(5.7e-5 * x + 4e-7)
        slope = 1.0 if e <= 6000 else 1 / 15
        steps.append(
            {
                "E_us": e,
                "level": float(response(np.array(e))),
                "var_dn2": (sig_in * slope * fs) ** 2,
            }
        )
    sh.add_input_referred_snr(steps, segs, T_SAT, fs)
    expect = [20 * np.log10((e / T_SAT) / np.sqrt(5.7e-5 * e / T_SAT + 4e-7)) for e in (5000, 7000)]
    assert [s["snr_in_db"] for s in steps] == pytest.approx(expect, abs=0.05)


def test_snr_vs_normal_same_level() -> None:
    model = {"segments": [{"a": 5.66e-5, "b": 4.08e-7}], "assumptions": {"full_scale_dn": 959.0}}
    steps = []
    for x in (0.02, 0.05, 0.1):
        var = 3.5 * (5.66e-5 * x + 4.08e-7) * 959.0**2
        steps.append({"E_us": x * T_SAT, "level": x, "var_dn2": var})
    r = sh.snr_vs_normal(steps, model, e_max=1e9)
    assert r["var_ratio_median"] == pytest.approx(3.5)
    assert r["median_delta_db"] == pytest.approx(-10 * np.log10(3.5))
