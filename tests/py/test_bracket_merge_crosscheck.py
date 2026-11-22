"""Phase 2 수락 기준: C++ hdr_merge vs Python 기준 모델 상대오차 ≤ 1e-5, 출처 맵 동일.

합성 입력(경계 띠·포화·음수 포함)과, 있으면 Phase 0 실제 브래킷(data/raw/20261120_desk_bracket).
"""

import json

import numpy as np
import pytest

from cctvlog import cli
from cctvlog.io import bracket as bio
from cctvlog.ref import bracket_merge as ref

CONFIG = cli.REPO_ROOT / "configs" / "hdr_merge.json"
DESK = cli.REPO_ROOT / "data" / "raw" / "20261120_desk_bracket" / "bracket_0000"
REL_TOL = 1e-5


def _run(bdir, out, frames=None):
    cli.run_cpp("hdr_merge", config=CONFIG, in_=bdir, out=out, frames=frames if frames else None)
    return (
        np.load(out / "merged.npy"),
        np.load(out / "source.npy"),
        json.loads((out / "merged.json").read_text()),
    )


def _reference(bdir, frames=None, params=None):
    params = params or json.loads(CONFIG.read_text())
    fr = bio.read_bracket(bdir, frames)
    raws = bio.load_raws(bdir, fr)
    return ref.bracket_merge(
        raws,
        [f.exposure_us for f in fr],
        [f.black for f in fr],
        [f.white for f in fr],
        tau=params["tau"],
        band_ratio=params["band_ratio"],
    )


def _max_rel_err(cpp: np.ndarray, py: np.ndarray) -> float:
    nz = py != 0
    assert np.all(cpp[~nz] == 0)
    return float(np.max(np.abs(cpp[nz].astype(np.float64) - py[nz]) / np.abs(py[nz])))


def _synthetic(rng, shape, exposures):
    """밝기 램프 장면 + 샷/판독 노이즈 → 노출별 10bit 코드 (띠·포화·블랙 아래 포함)."""
    h, w = shape
    x = np.exp(np.linspace(np.log(1e-3), np.log(20.0), w))[None, :].repeat(h, 0)
    t_long = max(exposures)
    raws = []
    for t in exposures:
        n = x * t / t_long
        n = n + rng.normal(0, 1, n.shape) * np.sqrt(5.66e-5 * np.maximum(n, 0) + 4.08e-7)
        raws.append(np.clip(np.round(n * 959 + 64), 0, 1023).astype(np.uint16))
    return raws


@pytest.mark.parametrize(
    ("exposures", "frames"),
    [
        ([15979, 3981, 988], None),
        ([15979, 3981, 988], [0, 1]),
        ([15979, 3981, 988], [0, 2]),
        ([988, 15979, 3981], None),  # 입력 순서 ≠ 노출 순서
        ([16000, 4000], None),
    ],
)
def test_synthetic_matches_reference(tmp_path, exposures, frames):
    rng = np.random.default_rng(20261122)
    bdir = tmp_path / "bracket"
    bio.write_bracket(bdir, _synthetic(rng, (64, 3000), exposures), exposures)
    merged, source, meta = _run(bdir, tmp_path / "out", frames)
    ref_m, ref_s = _reference(bdir, frames)
    assert merged.dtype == np.float32 and source.dtype == np.uint8
    assert _max_rel_err(merged, ref_m) <= REL_TOL
    np.testing.assert_array_equal(source, ref_s)
    # 띠·포화·음수가 실제로 포함됐는지
    assert np.any(source & ref.SOURCE_BLEND) and np.any(source & ref.SOURCE_SATURATED)
    assert np.any(ref_m < 0)
    used = [exposures[i] for i in (frames or range(len(exposures)))]
    assert meta["x_max"] == pytest.approx(max(used) / min(used))


def test_params_are_applied(tmp_path):
    rng = np.random.default_rng(1)
    bdir = tmp_path / "bracket"
    bio.write_bracket(bdir, _synthetic(rng, (16, 2000), [16000, 1000]), [16000, 1000])
    cfg = tmp_path / "p.json"
    cfg.write_text(json.dumps({"tau": 0.8, "band_ratio": 0.5}))
    cli.run_cpp("hdr_merge", config=cfg, in_=bdir, out=tmp_path / "out")
    merged = np.load(tmp_path / "out" / "merged.npy")
    ref_m, _ = _reference(bdir, params={"tau": 0.8, "band_ratio": 0.5})
    assert _max_rel_err(merged, ref_m) <= REL_TOL


def test_rejects_bad_config(tmp_path):
    rng = np.random.default_rng(2)
    bdir = tmp_path / "bracket"
    bio.write_bracket(bdir, _synthetic(rng, (4, 8), [16000, 1000]), [16000, 1000])
    cfg = tmp_path / "p.json"
    cfg.write_text(json.dumps({"tua": 0.9}))
    with pytest.raises(cli.CppRunError):
        cli.run_cpp("hdr_merge", config=cfg, in_=bdir, out=tmp_path / "out")


@pytest.mark.skipif(not DESK.is_dir(), reason="Phase 0 desk bracket data not present")
@pytest.mark.parametrize("frames", [None, [0, 1], [0, 2]])
def test_real_bracket_matches_reference(tmp_path, frames):
    merged, source, _ = _run(DESK, tmp_path / "out", frames)
    ref_m, ref_s = _reference(DESK, frames)
    assert _max_rel_err(merged, ref_m) <= REL_TOL
    np.testing.assert_array_equal(source, ref_s)
