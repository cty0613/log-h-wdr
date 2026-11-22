"""curve_params + ref/curves: H-Log 파라미터와 비교 곡선 기준 모델 (CLAUDE.md 6.4)."""

import json

import numpy as np
import pytest

from cctvlog import cli
from cctvlog.calib import curve_params as cp
from cctvlog.ref import curves as rc
from cctvlog.sensor import merged_noise as mn

NOISE = {
    name: json.loads((cli.REPO_ROOT / "configs" / f"noise_{name}.json").read_text())
    for name in ("single_g1", "bracket_R4", "bracket_R16", "bracket_R16_2f", "dcg")
}


@pytest.mark.parametrize("name", list(NOISE))
def test_hlog_params_consistent(name):
    noise = NOISE[name]
    c = cp.hlog_params(noise, name)
    assert c["N"] == pytest.approx(mn.noise_integral(noise), rel=1e-12)
    assert c["required_bits"] == mn.summary(noise)["required_bits"]
    assert rc.encode(c, np.array([c["x_max"]]))[0] == pytest.approx(1.0, abs=1e-12)
    assert rc.encode(c, np.array([0.0]))[0] == 0.0
    # C'(x)·σ(x) = k (노이즈 매칭), 구간 경계에서 값 연속
    x = np.geomspace(1e-3, c["x_max"] * 0.999, 400)
    h = 1e-7 * x
    d = (rc.encode(c, x + h) - rc.encode(c, x - h)) / (2 * h)
    np.testing.assert_allclose(d * np.sqrt(mn.model_variance(noise, x)), c["k"], rtol=2e-5)
    for s in c["segments"][1:]:
        lo, hi = rc.encode(c, np.array([s["x_start"] - 1e-12, s["x_start"] + 1e-12]))
        assert hi == pytest.approx(lo, abs=1e-10)


@pytest.mark.parametrize("name", list(NOISE))
def test_hlog_roundtrip_and_monotonic(name):
    c = cp.hlog_params(NOISE[name], name)
    x = np.geomspace(1e-7, c["x_max"], 20001)
    v = rc.encode(c, x)
    assert np.all(np.diff(v) > 0)
    np.testing.assert_allclose(rc.decode(c, v), x, rtol=1e-9)


def test_numbers_match_phase2():
    assert round(cp.hlog_params(NOISE["bracket_R16"], "r16")["N"]) == 494
    assert cp.hlog_params(NOISE["bracket_R16"], "r16")["required_bits"] == 9
    ext = cp.single_extended(NOISE["single_g1"], 16.0)
    assert round(cp.hlog_params(ext, "s16")["N"]) == 1041  # 합성 노이즈를 무시하면 11 bit


def test_reference_values():
    assert rc.slog3_encode(np.array(0.18)) * 1023 == pytest.approx(420.0)
    assert round(float(rc.slog3_encode(np.array(0.9)) * 1023)) == 598
    assert rc.hlg_oetf(np.array(1 / 12)) == pytest.approx(0.5)
    assert rc.hlg_oetf(np.array(1.0)) == pytest.approx(1.0, abs=1e-7)
    assert rc.rec709_oetf(np.array(1.0)) == pytest.approx(1.0)
    for t in cp.COMPARISON_TYPES:
        c = cp.comparison_params(t, 16.0)
        x = np.geomspace(1e-6, 16, 5001)
        v = rc.encode(c, x)
        assert v[-1] == pytest.approx(1.0, abs=1e-7)
        assert np.all(np.diff(v) > 0)
        np.testing.assert_allclose(rc.decode(c, v), x, rtol=1e-9)


def test_params_hash_depends_only_on_curve_values():
    c = cp.hlog_params(NOISE["bracket_R16"], "a")
    d = cp.hlog_params(NOISE["bracket_R16"], "b", source={"x": 1})
    assert cp.params_hash(c) == cp.params_hash(d)
    assert cp.params_hash(c) != cp.params_hash(cp.hlog_params(NOISE["bracket_R4"], "a"))
