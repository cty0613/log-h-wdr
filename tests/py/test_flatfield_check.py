"""cctvlog.sensor.flatfield_check: 합성 데이터로 지표가 기대값을 내는지 확인."""

from __future__ import annotations

import numpy as np
import pytest

from cctvlog.sensor import flatfield_check as ff


def test_channel_labels() -> None:
    assert ff.channel_labels("BGGR") == ["B", "G1", "G2", "R"]
    assert ff.channel_labels("RGGB") == ["R", "G1", "G2", "B"]
    assert ff.channel_labels("GRBG") == ["G1", "R", "B", "G2"]


def test_banding_ratio_white_noise_is_one() -> None:
    rng = np.random.default_rng(1)
    ratios = [ff.row_banding_ratio(rng.normal(0, 5, (600, 256))) for _ in range(20)]
    # 행 600개 → 분산 추정의 상대 표준오차 ≈ √(2/600) ≈ 6%
    assert np.mean(ratios) == pytest.approx(1.0, abs=0.05)


def test_banding_ratio_detects_row_modulation() -> None:
    rng = np.random.default_rng(2)
    rows = np.arange(600)[:, None]
    d = rng.normal(0, 5, (600, 256)) + 1.0 * np.sin(2 * np.pi * rows / 12)
    # 행 성분 분산 0.5, 화소 분산/열 수 = 25/256 → 비 ≈ 1 + 0.5/(25/256) ≈ 6.1
    assert ff.row_banding_ratio(d) == pytest.approx(1 + 0.5 / (25 / 256), rel=0.15)
    assert ff.dominant_row_period(d.mean(axis=1)) == pytest.approx(12.0, rel=0.02)


def test_linear_fit_exact() -> None:
    t = np.array([100.0, 200, 500, 1000])
    fit = ff.linear_fit(t, 2e-4 * t + 0.01)
    assert fit["slope_per_us"] == pytest.approx(2e-4)
    assert fit["intercept"] == pytest.approx(0.01)
    assert fit["r2"] == pytest.approx(1.0)


def test_analyze_sweep_saturation_estimate() -> None:
    labels = ["B", "G1", "G2", "R"]
    t = [100, 200, 500, 1000, 2000, 4000]
    rows = [
        {"exposure_time_us": x, "roi_means": [min(x / 2500 * k, 1.0) for k in (0.8, 1, 1, 0.3)]}
        for x in t
    ]
    s = ff.analyze_sweep(rows, labels)
    assert s["brightest_channel"] == "G1"
    assert s["t_sat_est_us"] == pytest.approx(2500, rel=1e-9)
    assert s["channels"]["G1"]["saturated_at_us"] == 4000
    assert s["lowest_level_at_min_exposure"] == pytest.approx(100 / 2500)


def test_analyze_stability_drift() -> None:
    n = 11
    rows = [
        {
            "roi_max_channel_mean": 0.5 * (1 + 0.001 * i),
            "timestamp_ns": int(i * 6e9),
            "sensor_temperature": 40.0,
        }
        for i in range(n)
    ]
    st = ff.analyze_stability(rows)
    assert st["duration_s"] == pytest.approx(60.0)
    # 6 s마다 0.1% → 1%/min (평균 대비라 약간 작음)
    assert st["drift_rel_per_min"] == pytest.approx(0.01 / 1.005, rel=1e-6)
    assert st["range_rel"] == pytest.approx(0.01 / 1.005, rel=1e-6)


def test_grade() -> None:
    assert ff.grade(1.0, 1.5, 3.0) == "OK"
    assert ff.grade(2.0, 1.5, 3.0) == "WARN"
    assert ff.grade(5.0, 1.5, 3.0) == "FAIL"
    assert ff.grade(None, 1.5, 3.0) == "N/A"
