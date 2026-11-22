"""Phase 3 수락 기준: C++ 곡선 float vs Python 기준 모델 상대오차 ≤ 1e-5, LUT vs float ≤ 1 LSB.

C++ `curve_eval` 출력(v.npy는 float32)과 Python(float64)을 같은 float32 입력에서 비교한다.
"""

import json

import numpy as np
import pytest

from cctvlog import cli
from cctvlog.calib import curve_params as cp
from cctvlog.ref import curves as rc

REL_TOL = 1e-5


def _noise(name):
    return json.loads((cli.REPO_ROOT / "configs" / f"noise_{name}.json").read_text())


CURVES = {
    "hlog_R16": lambda: cp.hlog_params(_noise("bracket_R16"), "hlog_R16"),
    "hlog_R16_2f": lambda: cp.hlog_params(_noise("bracket_R16_2f"), "hlog_R16_2f"),
    "hlog_dcg": lambda: cp.hlog_params(_noise("dcg"), "hlog_dcg"),
    "hlog_single": lambda: cp.hlog_params(_noise("single_g1"), "hlog_single"),
    "rec709": lambda: cp.comparison_params("rec709", 16.0),
    "hlg": lambda: cp.comparison_params("hlg", 16.0),
    "slog3": lambda: cp.comparison_params("slog3", 16.0),
}


def _run(tmp_path, curve, lut=False):
    path = tmp_path / "curve.json"
    cp.write(curve, path)
    out = tmp_path / "out"
    cli.run_cpp("curve_eval", config=path, out=out, bits="8,10,12", ramp_n=50001, lut=lut)
    return out


@pytest.mark.parametrize("name", list(CURVES))
def test_float_curve_matches_reference(tmp_path, name):
    curve = CURVES[name]()
    out = _run(tmp_path, curve)
    x = np.load(out / "x.npy").ravel().astype(np.float64)
    v_cpp = np.load(out / "v.npy").ravel().astype(np.float64)
    v_py = rc.encode(curve, x)
    nz = v_py > 0
    assert np.all(v_cpp[~nz] == 0)
    assert np.max(np.abs(v_cpp[nz] - v_py[nz]) / v_py[nz]) <= REL_TOL
    for bits in (8, 10, 12):
        codes = np.load(out / f"codes_{bits}.npy").ravel()
        ref = rc.to_code(v_py, bits)
        # 반올림 경계에 아주 가까운 점(1e-9 LSB 안)만 다를 수 있다
        diff = codes != ref
        frac = (v_py * (2**bits - 1)) % 1.0
        assert np.all(np.abs(frac[diff] - 0.5) < 1e-9), name
        levels = np.load(out / f"levels_{bits}.npy").ravel().astype(np.float64)
        ref_lv = rc.decode(curve, np.arange(2**bits) / (2**bits - 1))
        pos = ref_lv > 0
        assert np.max(np.abs(levels[pos] - ref_lv[pos]) / ref_lv[pos]) <= REL_TOL
    run = json.loads((out / "run.json").read_text())
    assert run["bits"]["10"]["float_roundtrip_max_rel_err"] < 1e-9


@pytest.mark.parametrize("name", [n for n in CURVES if n.startswith("hlog")])
def test_fixed_lut_within_one_lsb(tmp_path, name):
    curve = CURVES[name]()
    out = _run(tmp_path, curve, lut=True)
    lut = json.loads((out / "run.json").read_text())["lut"]
    assert lut["max_abs_err_lsb"] <= 1
    assert lut["monotonic"]
    assert lut["inverse_max_rel_err_vs_float"] < 1e-6
    fwd = np.load(out / "lut_forward.npy").ravel()
    assert fwd.size == 65536 and np.all(fwd & 0x3F == 0)
    assert np.load(out / "lut_inverse.npy").size == 1024
    # 램프 위에서도 float 코드와 1 LSB 안
    x = np.load(out / "x.npy").ravel().astype(np.float64)
    lc = np.load(out / "lut_codes.npy").ravel().astype(np.int64)
    assert np.max(np.abs(lc - rc.to_code(rc.encode(curve, x), 10))) <= 1


def test_rejects_inconsistent_curve(tmp_path):
    curve = CURVES["hlog_R16"]()
    curve["k"] *= 1.01
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(curve))
    with pytest.raises(cli.CppRunError):
        cli.run_cpp("curve_eval", config=path, out=tmp_path / "out")
