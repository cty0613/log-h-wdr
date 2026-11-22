"""merged_noise: 브래킷·DCG 해석 노이즈 모델 (CLAUDE.md 6.3, 5.4) + C++ dcg_sim JSON 일치."""

import json
import math

import numpy as np
import pytest

from cctvlog import cli
from cctvlog.sensor import merged_noise as mn

A, B = 5.660737407263459e-05, 4.0833976754083865e-07
SINGLE = {
    "kind": "single",
    "scale": "normalized_long_exposure",
    "x_max": 1.0,
    "segments": [{"x_start": 0.0, "a": A, "b": B}],
    "assumptions": {"analogue_gain": 1.1228},
}
DCG_CFG = cli.REPO_ROOT / "configs" / "dcg_assumptions.json"


def _numeric_integral(model, n=2_000_001):
    x = np.linspace(0.0, model["x_max"], n)
    return float(np.trapezoid(1.0 / np.sqrt(mn.model_variance(model, x)), x))


def test_bracket_segments_follow_5_4_schema():
    m = mn.bracket_model(SINGLE, [1, 4, 16])
    assert m["kind"] == "bracket"
    assert m["x_max"] == 16.0
    segs = m["segments"]
    assert [s["x_start"] for s in segs] == pytest.approx([0.0, 0.9, 3.6])
    assert segs[1]["a"] == pytest.approx(4 * A) and segs[1]["b"] == pytest.approx(16 * B)
    assert segs[2]["a"] == pytest.approx(16 * A) and segs[2]["b"] == pytest.approx(256 * B)


@pytest.mark.parametrize("ratios", [[1, 4], [1, 16], [1, 4, 16], [1, 4.0138, 16.1731]])
def test_noise_integral_closed_form(ratios):
    m = mn.bracket_model(SINGLE, ratios)
    assert mn.noise_integral(m) == pytest.approx(_numeric_integral(m), rel=1e-4)


def test_numbers_quoted_in_decisions():
    # docs/decisions.md 2026-11-22 결정 1 (N, 비트, dip)
    r4 = mn.summary(mn.bracket_model(SINGLE, [1, 4]))
    r16_2 = mn.summary(mn.bracket_model(SINGLE, [1, 16]))
    r16_3 = mn.summary(mn.bracket_model(SINGLE, [1, 4, 16]))
    assert round(r4["N"]) == 369 and r4["required_bits"] == 9
    assert round(r16_2["N"]) == 430 and round(r16_3["N"]) == 494
    assert r16_2["dips"][0]["dip_db"] == pytest.approx(12.53, abs=0.01)
    assert [round(d["dip_db"], 2) for d in r16_3["dips"]] == [6.12, 6.12]
    assert r16_3["dr_stops"] == pytest.approx(14.61, abs=0.01)


def test_band_variance_matches_jump_outside_band_and_is_continuous():
    ratios = [1, 4, 16]
    m = mn.bracket_model(SINGLE, ratios)
    x = np.concatenate([np.linspace(0.01, 0.71, 50), np.linspace(0.91, 2.87, 50)])
    np.testing.assert_allclose(mn.band_variance(x, A, B, ratios), mn.model_variance(m, x))
    # 띠 양 끝에서 연속
    for edge in (0.72, 0.9, 2.88, 3.6):
        lo, hi = mn.band_variance(np.array([edge - 1e-9, edge + 1e-9]), A, B, ratios)
        assert hi == pytest.approx(lo, rel=1e-6)
    # 띠 끝 σ 비율 (decisions.md 결정 2)
    r = math.sqrt(mn.band_variance(0.899, A, B, [1, 16]) / mn.model_variance(m, 0.899))
    assert r == pytest.approx(4.21, abs=0.01)


def test_dcg_model_numbers():
    p = json.loads(DCG_CFG.read_text())
    m = mn.dcg_model(p)
    s = mn.summary(m)
    assert m["x_max"] == pytest.approx(16.0)
    assert m["segments"][1]["x_start"] == pytest.approx(3.6)
    assert m["segments"][0]["a"] == m["segments"][1]["a"]  # 샷 노이즈 공유
    assert s["dr_stops"] == pytest.approx(12.61, abs=0.01)
    assert s["dips"][0]["dip_db"] == pytest.approx(0.13, abs=0.01)
    assert round(s["N"]) == 259
    # 띠 밖에서는 점프 모델과 같다
    x = np.array([0.1, 2.0, 5.0, 12.0])
    np.testing.assert_allclose(mn.dcg_band_variance(x, p), mn.model_variance(m, x))


@pytest.mark.parametrize("ratio", [2, 4, 8])
def test_cpp_dcg_noise_json_matches_python(tmp_path, ratio):
    p = json.loads(DCG_CFG.read_text())
    p["conversion_gain_ratio"] = ratio
    cfg = tmp_path / "dcg.json"
    cfg.write_text(json.dumps(p))
    cli.run_cpp("dcg_sim", config=cfg, out=tmp_path / "out")
    cpp = json.loads((tmp_path / "out" / "noise_dcg.json").read_text())
    py = mn.dcg_model(p)
    assert cpp["kind"] == py["kind"]
    assert cpp["x_max"] == pytest.approx(py["x_max"], rel=1e-12)
    for c, q in zip(cpp["segments"], py["segments"], strict=True):
        for k in ("x_start", "a", "b"):
            assert c[k] == pytest.approx(q[k], rel=1e-12)
    for k in ("hcg_full_well_e", "hcg_read_noise_e", "hcg_e_per_dn", "lcg_e_per_dn"):
        assert cpp["assumptions"][k] == pytest.approx(py["assumptions"][k], rel=1e-12)
