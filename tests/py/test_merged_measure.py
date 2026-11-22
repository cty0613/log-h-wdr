"""merged_measure: 노이즈를 알고 있는 합성 플랫필드 브래킷 스윕으로 실측 파이프라인을 검증한다.

합성 노이즈는 단일 노출 모델 σ² = a·n + b를 그대로 따르므로, 분석 결과는 해석 모델(띠 포함)과
통계 오차 안에서 같아야 한다. hdr_merge(C++)를 실제로 호출한다.
"""

import json

import numpy as np
import pytest

from cctvlog import cli
from cctvlog.io import bracket as bio
from cctvlog.sensor import merged_measure as mm

A, B = 5.660737407263459e-05, 4.0833976754083865e-07
SINGLE = {"kind": "single", "x_max": 1.0, "segments": [{"x_start": 0.0, "a": A, "b": B}]}
FS = 959.0
LEVELS = [0.1, 0.3, 0.6, 0.76, 0.8, 0.86, 1.5, 2.5, 2.95, 3.25, 3.5, 6.0, 11.0]
VARIANTS = [
    {"name": "R4", "frames": [0, 1], "ratios": [1, 4]},
    {"name": "R16", "frames": [0, 1, 2], "ratios": [1, 4, 16]},
    {"name": "R16_2f", "frames": [0, 2], "ratios": [1, 16]},
]


def _make_sweep(root, rng, size=560):
    h = w = size
    yy, xx = np.mgrid[0:h, 0:w]
    grad = 0.9 + 0.2 * (xx + yy) / (h + w)  # ±10% 밝기 기울기
    chan = np.where((yy % 2 == 1) & (xx % 2 == 1), 1.0, 0.55)  # 초록 한 채널만 밝게 (R·B 흉내)
    chan = np.where((yy % 2) != (xx % 2), 1.0, chan)
    # 양자화(1/12 DN²)를 합쳐 총 분산이 모델 b가 되도록
    b_gen = B - 1.0 / (12 * FS**2)
    steps = []
    for si, lv in enumerate(LEVELS):
        exps = [46000.0 * lv, 46000.0 * lv / 4, 46000.0 * lv / 16]
        for k in range(2):
            raws = []
            for t in exps:
                n = lv * grad * chan * t / exps[0]
                n = n + rng.normal(0, 1, n.shape) * np.sqrt(A * n + b_gen)
                raws.append(np.clip(np.floor(n * FS + 64 + 0.5), 0, 1023).astype(np.uint16))
            bio.write_bracket(root / f"flat/step_{si:02d}/bracket_{k:04d}", raws, exps)
        steps.append({"level": lv, "status": "ok", "dir": f"flat/step_{si:02d}"})
    (root / "plan.json").write_text(json.dumps({"steps": steps}))


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("sweep")
    _make_sweep(root, np.random.default_rng(20261122))
    p = mm.MeasureParams(min_pixels=1500)
    return mm.analyze_sweep(
        root, VARIANTS, SINGLE, cli.REPO_ROOT / "configs" / "hdr_merge.json", root / "work", p
    )


@pytest.mark.parametrize("name", ["R4", "R16", "R16_2f"])
def test_segments_and_bands_match_model(result, name):
    regs = result["variants"][name]["regions"]
    assert all(r["pass"] for r in regs if r["kind"] == "seg")
    for r in regs:
        assert r["all"] is not None, r
        assert abs(r["all"]["rel_err"]) < 0.03, (name, r["kind"], r["frame"], r["all"])


def test_band_is_measured_densely(result):
    # 16:1 띠에서 노이즈가 최대 4배(σ)까지 바뀌는 구간이 실제로 측정되는가
    bins = result["variants"]["R16_2f"]["bins"]["all"]
    band = [r for r in bins if 0.74 < r["x"] < 0.88]
    assert len(band) >= 3
    assert max(r["model"] for r in band) / min(r["model"] for r in band) > 3
    assert all(abs(r["rel_err"]) < 0.06 for r in band)


def test_dips_match_prediction(result):
    for name, n_dips in (("R4", 1), ("R16", 2), ("R16_2f", 1)):
        dips = result["variants"][name]["dips"]
        assert len(dips) == n_dips
        for d in dips:
            assert d["dip_meas_db"] == pytest.approx(d["dip_pred_db"], abs=0.3), (name, d)


def test_pixel_ratio_matches_metadata(result):
    assert result["ratio_checks"]
    assert all(abs(r["rel_diff"]) < 0.003 for r in result["ratio_checks"])
    assert not any(v["flicker"] for s in result["steps"] for v in s["variants"].values())


def test_box_mean_and_regions():
    img = np.arange(30.0).reshape(5, 6)
    out = mm.box_mean(img, 3)
    assert out.shape == (3, 4)
    assert out[0, 0] == pytest.approx(img[:3, :3].mean())
    regs = mm.regions([1, 4, 16], 0.9, 0.8, 0.0)
    assert [r["kind"] for r in regs] == ["seg", "band", "seg", "band", "seg"]
    np.testing.assert_allclose(
        [(r["x0"], r["x1"]) for r in regs],
        [(0, 0.72), (0.72, 0.9), (0.9, 2.88), (2.88, 3.6), (3.6, 14.4)],
    )
