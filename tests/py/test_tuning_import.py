import json

import numpy as np
import pytest

from cctvlog.calib import tuning_import as ti

W, H = 16, 12  # bcm2835 격자


def _tuning(tmp_path, target="bcm2835", n=W * H):
    rng = np.random.default_rng(1)
    ramp = np.linspace(1.0, 2.0, n)
    tuning = {
        "version": 2.0,
        "target": target,
        "algorithms": [
            {"rpi.black_level": {"black_level": 4096}},
            {
                "rpi.alsc": {
                    "omega": 1.3,
                    "n_iter": 100,
                    "luminance_strength": 0.5,
                    "calibrations_Cr": [
                        {"ct": 3000, "table": (ramp * 2).tolist()},
                        {"ct": 5000, "table": (ramp * 4).tolist()},
                    ],
                    "calibrations_Cb": [
                        {"ct": 3000, "table": rng.uniform(1, 3, n).tolist()},
                        {"ct": 5000, "table": rng.uniform(1, 3, n).tolist()},
                    ],
                    "luminance_lut": (1.0 + ramp).tolist(),
                }
            },
            {
                "rpi.ccm": {
                    "ccms": [
                        {"ct": 3000, "ccm": [2, -0.5, -0.5, -0.5, 2, -0.5, -0.5, -0.5, 2]},
                        {"ct": 6000, "ccm": [1, 0, 0, 0, 1, 0, 0, 0, 1]},
                    ]
                }
            },
        ],
    }
    p = tmp_path / "t.json"
    p.write_text(json.dumps(tuning))
    return p, tuning


def test_extract_shapes_and_source(tmp_path):
    p, _ = _tuning(tmp_path)
    ccm, alsc = ti.import_tuning(p, "imx708_wide")
    assert ccm["source"]["camera_model"] == "imx708_wide"
    assert len(ccm["source"]["sha256"]) == 64
    assert ccm["ccms"][0]["matrix"][0] == [2, -0.5, -0.5]
    assert ccm["ccms"][0]["row_sums"] == [1.0, 1.0, 1.0]
    assert alsc["grid"] == {"width": W, "height": H}
    assert np.asarray(alsc["luminance_lut"]).shape == (H, W)
    # 행 우선: 원래 리스트 인덱스 y*W + x
    flat = np.linspace(1.0, 2.0, W * H) + 1.0
    assert alsc["luminance_lut"][3][5] == pytest.approx(flat[3 * W + 5])


def test_wrong_table_size_rejected(tmp_path):
    p, _ = _tuning(tmp_path, target="pisp", n=W * H)  # pisp는 32x32 필요
    with pytest.raises(ValueError, match="32x32"):
        ti.import_tuning(p, None)


def test_alsc_gains_formula_interp_and_flip(tmp_path):
    p, t = _tuning(tmp_path)
    _, alsc = ti.import_tuning(p, None)
    raw = t["algorithms"][1]["rpi.alsc"]
    lum = (np.asarray(raw["luminance_lut"]).reshape(H, W) - 1) * 0.5 + 1

    g = ti.alsc_gains(alsc, 4000)
    assert g.shape == (3, H, W)
    np.testing.assert_allclose(g[1], lum)
    # R: ct 보간(3000/5000 중간) 후 min 정규화
    cr = (
        np.asarray(raw["calibrations_Cr"][0]["table"])
        + np.asarray(raw["calibrations_Cr"][1]["table"])
    ) / 2
    cr = cr.reshape(H, W)
    np.testing.assert_allclose(g[0], cr / cr.min() * lum)
    assert (g[0] / lum).min() == pytest.approx(1.0)
    assert (g[2] / lum).min() == pytest.approx(1.0)

    # 범위 밖 clamp
    np.testing.assert_allclose(ti.alsc_gains(alsc, 1000), ti.alsc_gains(alsc, 3000))
    np.testing.assert_allclose(ti.alsc_gains(alsc, 9000), ti.alsc_gains(alsc, 5000))

    # flip: 센서 h+v flip RAW → 테이블 양축 반전
    gf = ti.alsc_gains(alsc, 4000, flip_h=True, flip_v=True)
    np.testing.assert_allclose(gf[:, 0, 0], g[:, H - 1, W - 1])
    np.testing.assert_allclose(gf, g[:, ::-1, ::-1])

    # strength 0 → 휘도 보정 없음
    np.testing.assert_allclose(ti.alsc_gains(alsc, 4000, luminance_strength=0.0)[1], 1.0)


def test_ccm_interpolation(tmp_path):
    p, _ = _tuning(tmp_path)
    ccm, _ = ti.import_tuning(p, None)
    np.testing.assert_allclose(ti.ccm_at(ccm, 2000), ti.ccm_at(ccm, 3000))
    np.testing.assert_allclose(
        ti.ccm_at(ccm, 4500), [[1.5, -0.25, -0.25], [-0.25, 1.5, -0.25], [-0.25, -0.25, 1.5]]
    )
    np.testing.assert_allclose(ti.ccm_at(ccm, 7000), np.eye(3))


def test_non_increasing_ct_rejected(tmp_path):
    p, t = _tuning(tmp_path)
    t["algorithms"][2]["rpi.ccm"]["ccms"].reverse()
    p.write_text(json.dumps(t))
    with pytest.raises(ValueError, match="increasing"):
        ti.import_tuning(p, None)
