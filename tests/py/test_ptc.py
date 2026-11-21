"""cctvlog.sensor.ptc / noise_model: 알려진 K·판독 노이즈의 합성 센서로 복원 정확도 검증."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from cctvlog.sensor import noise_model, ptc

BLACK, WHITE, FS = 64, 1023, 959
SIZE = 128
# BGGR 위치별 상대 감도 (B, G1, G2, R) — 실측과 비슷하게 R·B가 초록의 절반쯤
SENS = np.array([0.6, 1.0, 1.0, 0.5])
GAINS = {"g1": (1.1228, 0.05, 0.6), "g16": (16.0, 0.75, 1.7)}  # (게인, K DN/e⁻, σ_read DN)


def frame(
    rng: np.random.Generator, k: float, sr: float, mean_dn_green: float, row_mod: float = 0.0
) -> np.ndarray:
    sens = np.empty((SIZE, SIZE))
    for (dy, dx), s in zip(ptc.CHANNEL_BAYER_POS, SENS, strict=True):
        sens[dy::2, dx::2] = s
    mu_e = sens * mean_dn_green / k
    if row_mod:
        mu_e = (
            mu_e
            * (1 + row_mod * np.sin(2 * np.pi * np.arange(SIZE) / 12 + rng.uniform(0, 6.3)))[
                :, None
            ]
        )
    dn = k * rng.poisson(mu_e) + BLACK + rng.normal(0, sr, mu_e.shape)
    return np.clip(np.floor(dn + 0.5), 0, WHITE).astype(np.uint16)


def write_frame(d: Path, i: int, img: np.ndarray, gain: float, t: float) -> str:
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / f"{i:06d}.npy", img)
    meta = {
        "bayer_order": "BGGR",
        "black_level": BLACK,
        "white_level": WHITE,
        "analogue_gain": gain,
        "exposure_time_us": t,
        "sensor_temperature": 40.0,
    }
    (d / f"{i:06d}.json").write_text(json.dumps(meta))
    return f"{d.parent.name}/{d.name}/{i:06d}.npy"


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    rng = np.random.default_rng(7)
    root = tmp_path_factory.mktemp("ptc")
    flat, dark = root / "flat", root / "dark"
    ff, df = [], []
    levels = (0.01, 0.03, 0.1, 0.2, 0.35, 0.5, 0.7, 0.85, 1.2, 2.0)
    for label, (gain, k, sr) in GAINS.items():
        idx = 0
        for step, lv in enumerate(levels):
            t = 1000.0 * lv
            for pair in (0, 1):
                mod = 0.2 if (label == "g16" and step == 0) else 0.0  # 깜빡임 오염 단계
                for f in (0, 1):
                    rel = write_frame(
                        flat / "flat" / label, idx, frame(rng, k, sr, lv * FS, mod), gain, t
                    )
                    ff.append(
                        {
                            "part": "flat",
                            "gain_label": label,
                            "step": step,
                            "pair": pair,
                            "frame": f,
                            "level": lv,
                            "file": rel,
                        }
                    )
                    idx += 1
        for j, t in enumerate((50.0, 2000.0)):
            for i in range(6):
                rel = write_frame(dark / "dark" / label, j * 6 + i, frame(rng, k, sr, 0.0), gain, t)
                df.append(
                    {"part": "dark", "gain_label": label, "exposure_us_requested": t, "file": rel}
                )
    ff.append({"part": "monitor", "roi_max_channel_mean": 0.5})
    (flat / "ptc.json").write_text(json.dumps({"part": "flat", "frames": ff}))
    (dark / "ptc.json").write_text(json.dumps({"part": "dark", "frames": df}))
    return flat, dark


@pytest.fixture(scope="module")
def rep(runs: tuple[Path, Path]) -> dict:
    return ptc.analyze(*runs, ptc.PtcParams(roi=SIZE))


def test_pair_variance_white_noise() -> None:
    rng = np.random.default_rng(1)
    d = rng.normal(0, 2.0, (400, 400))  # Var(A−B) = 4 → σ² = 2
    v, vr = ptc.pair_variance(d)
    assert v == pytest.approx(2.0, rel=0.02)
    assert vr == pytest.approx(v, rel=0.01)


def test_fit_fixed_b_exact() -> None:
    x = np.array([10.0, 50, 200, 800])
    f = ptc.fit_fixed_b(x, 0.3 * x + 2.0, 2.0)
    assert f["K"] == pytest.approx(0.3)
    assert f["r2"] == pytest.approx(1.0)
    g = ptc.fit_free(x, 0.3 * x + 2.0)
    assert (g["K"], g["b"]) == (pytest.approx(0.3), pytest.approx(2.0))


def test_dark_black_and_read_noise(rep: dict) -> None:
    for label, (_, _, sr) in GAINS.items():
        d = rep["dark"][label]
        assert np.allclose(d["black_dn"], BLACK, atol=0.05)
        # 정수 반올림이 1/12 DN² 더한다
        assert d["b_dn2"] == pytest.approx(sr**2 + 1 / 12, rel=0.06)


@pytest.mark.parametrize("label", list(GAINS))
def test_recovers_K(rep: dict, label: str) -> None:
    _, k, _ = GAINS[label]
    g = rep["gains"][label]
    assert g["fixed_b"]["K"] == pytest.approx(k, rel=0.02)
    assert g["free"]["K"] == pytest.approx(k, rel=0.03)
    assert g["fixed_b"]["r2"] > 0.99
    assert g["saturation_e"] == pytest.approx(FS / k, rel=0.02)
    for ch in g["per_channel"].values():
        assert ch["fixed_b"]["K"] == pytest.approx(k, rel=0.05)


def test_saturation_excludes_all_channels_of_pair(rep: dict) -> None:
    pts = rep["points"]
    for key in {(r["gain_label"], r["step"], r["pair"]) for r in pts}:
        rows = [r for r in pts if (r["gain_label"], r["step"], r["pair"]) == key]
        if any(r["sat_fraction"] > 0 for r in rows):
            assert not any(r["use"] for r in rows)
    reasons = {r["exclude_reason"] for r in pts}
    assert "other channel saturated" in reasons  # R·B는 초록이 포화돼도 아직 비포화


def test_flicker_step_is_flagged(rep: dict) -> None:
    pts = [r for r in rep["points"] if r["gain_label"] == "g16" and r["step"] == 0]
    assert pts and all(r["exclude_reason"] == "flicker (row-correlated excess)" for r in pts)
    clean = [r for r in rep["points"] if r["gain_label"] == "g1" and r["step"] == 0]
    assert all(r["use"] for r in clean)


def test_noise_model_schema(rep: dict) -> None:
    g = rep["gains"]["g16"]
    m = noise_model.single_noise_model(g, FS, "fixed_b", {"x": 1})
    assert m["kind"] == "single" and m["x_max"] == 1.0
    (seg,) = m["segments"]
    assert seg["a"] == pytest.approx(g["fixed_b"]["K"] / FS)
    assert seg["b"] == pytest.approx(g["b_dark_dn2"] / FS**2)
    # 정규화 스케일의 분산 = DN 분산 / FS²
    x = 0.4
    assert noise_model.variance(x, seg["a"], seg["b"]) * FS**2 == pytest.approx(
        g["fixed_b"]["K"] * x * FS + g["b_dark_dn2"]
    )
    assert set(m["assumptions"]["per_channel_K_dn_per_e"]) == {"B", "G1", "G2", "R"}
    with pytest.raises(ValueError):
        noise_model.single_noise_model(g, FS, "bogus")


def test_noise_model_roundtrip(tmp_path: Path, rep: dict) -> None:
    m = noise_model.single_noise_model(rep["gains"]["g1"], FS)
    noise_model.write(m, tmp_path / "n.json")
    assert noise_model.load(tmp_path / "n.json") == m
