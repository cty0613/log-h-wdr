"""합성 후 노이즈 분포 실측 (CLAUDE.md 6.3, Phase 2).

입력: capture/capture_bracket_ptc.py의 플랫필드 브래킷 스윕 (단계마다 브래킷 세트 2개 이상).

방법
- 같은 단계의 세트 두 개를 C++ hdr_merge로 각각 합성한다(A, B).
  화소 처리는 C++, 여기서는 통계만 낸다.
- 중앙 ROI, Bayer 채널(2x2 위치)별로 차분 d = A − B와 국소 밝기 x_loc = box((A + B) / 2)를 구한다.
  플랫필드의 완만한 밝기 기울기 때문에 ROI 안에서 x가 수 % 퍼진다.
  그래서 화소를 x_loc의 로그 구간으로 묶어 구간별로 분산을 낸다.
  경계 띠처럼 분산이 x에 따라 빠르게 바뀌는 곳도 이렇게 해야 측정된다.
  A − B와 A + B는 분산이 같으면 상관이 없으므로, x_loc로 묶어도 분산 추정이 치우치지 않는다.
- 구간 분산 = Var(d)/2 (구간 안 평균을 뺀다: 프레임 사이 광원 드리프트 제거).
- 모델 기대값 = 같은 화소들의 σ²(x_loc) 평균 (`merged_noise.band_variance`, 그 단계의 실제 노출비).
- 단계 사이에서는 (n − 1) 가중으로 합친다.
- 깜빡임: 차분의 행 평균을 뺀 분산과의 차이가 다크의 같은 값보다 flicker_flag 이상 크면 그 단계 제외
  (Phase 1 ptc.select_points와 같은 기준).
- 포화 표시가 있는 화소(어느 한 세트라도)는 뺀다.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from cctvlog import cli
from cctvlog.io import bracket as bio
from cctvlog.sensor import merged_noise as mn
from cctvlog.sensor import ptc

SATURATED = 0x80


@dataclass(frozen=True)
class MeasureParams:
    roi: int = 512  # 센서 화소, 중앙
    box: int = 9  # 국소 평균 창 (채널 평면 화소)
    bins_per_stop: int = 16
    x_lo: float = 2.0**-7
    x_hi: float = 32.0
    min_pixels: int = 2000  # 구간 하나(단계·채널 합산 후)의 최소 화소 수
    edge_margin: float = 0.03  # 영역 경계에서 상대 여유
    flicker_flag: float = 0.01
    dark_rowdet_excess: float = 0.0037  # Phase 1 g1 다크 (results/p1 report.json)
    tolerance: float = 0.10  # 수락 기준: 구간별 ±10%


def bin_edges(p: MeasureParams) -> np.ndarray:
    n = round(float(np.log2(p.x_hi / p.x_lo)) * p.bins_per_stop)
    return p.x_lo * 2.0 ** (np.arange(n + 1) / p.bins_per_stop)


def box_mean(img: np.ndarray, k: int) -> np.ndarray:
    """k x k 평균 (valid 영역, 결과 크기 (H − k + 1, W − k + 1))."""
    c = np.cumsum(np.cumsum(np.pad(img, ((1, 0), (1, 0))), axis=0), axis=1)
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def rowdet_excess(d: np.ndarray) -> float:
    v, vr = ptc.pair_variance(d)
    return v / vr - 1.0 if vr > 0 else 0.0


@dataclass
class BinAcc:
    """구간별 누적: 화소 수, 자유도 가중 분산 합, 자유도 가중 모델 합, x 합."""

    n: np.ndarray
    dof: np.ndarray
    var_dof: np.ndarray
    model_dof: np.ndarray
    x_sum: np.ndarray

    @classmethod
    def zeros(cls, nb: int) -> BinAcc:
        return cls(*(np.zeros(nb) for _ in range(5)))

    def add(self, other: BinAcc) -> None:
        for f in ("n", "dof", "var_dof", "model_dof", "x_sum"):
            getattr(self, f).__iadd__(getattr(other, f))


def plane_bins(
    a: np.ndarray,
    b: np.ndarray,
    valid: np.ndarray,
    edges: np.ndarray,
    model_var: Callable[[np.ndarray], np.ndarray],
    p: MeasureParams,
) -> tuple[BinAcc, float]:
    """한 채널 평면의 합성 쌍 → 구간 누적 (이 단계 안에서 구간 평균을 뺀 분산)."""
    k = p.box
    h = k // 2
    xl = box_mean((a + b) / 2.0, k)
    d = (a - b)[h : h + xl.shape[0], h : h + xl.shape[1]]
    ok = valid[h : h + xl.shape[0], h : h + xl.shape[1]]
    excess = rowdet_excess(d)
    nb = len(edges) - 1
    acc = BinAcc.zeros(nb)
    idx = np.searchsorted(edges, xl, side="right") - 1
    m = ok & (idx >= 0) & (idx < nb) & (xl > 0)
    idx, dd, xx = idx[m], d[m], xl[m]
    n = np.bincount(idx, minlength=nb).astype(np.float64)
    s1 = np.bincount(idx, dd, minlength=nb)
    s2 = np.bincount(idx, dd * dd, minlength=nb)
    sm = np.bincount(idx, model_var(xx), minlength=nb)
    sx = np.bincount(idx, xx, minlength=nb)
    use = n >= 2
    var = np.zeros(nb)
    var[use] = (s2[use] - s1[use] ** 2 / n[use]) / (n[use] - 1) / 2.0
    dof = np.where(use, n - 1, 0.0)
    acc.n = np.where(use, n, 0.0)
    acc.dof = dof
    acc.var_dof = var * dof
    acc.model_dof = np.where(use, sm / np.maximum(n, 1), 0.0) * dof
    acc.x_sum = np.where(use, sx, 0.0)
    return acc, excess


def regions(ratios: list[float], tau: float, band_ratio: float, m: float) -> list[dict[str, Any]]:
    """영역: 프레임 k 단독 구간(seg)과 k→k+1 경계 띠(band). 경계에서 상대 여유 m."""
    lo = band_ratio * tau
    out = []
    for k, r in enumerate(ratios):
        start = 0.0 if k == 0 else tau * ratios[k - 1] * (1 + m)
        end = (lo * r if k < len(ratios) - 1 else tau * r) * (1 - m)
        out.append({"kind": "seg", "frame": k, "ratio": r, "x0": start, "x1": end})
        if k < len(ratios) - 1:
            out.append(
                {
                    "kind": "band",
                    "frame": k,
                    "ratio": r,
                    "x0": lo * r * (1 + m),
                    "x1": tau * r * (1 - m),
                }
            )
    return out


def wls_line(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    A = np.vstack([x, np.ones_like(x)]).T * np.sqrt(w)[:, None]
    (alpha, beta), *_ = np.linalg.lstsq(A, y * np.sqrt(w), rcond=None)
    return float(alpha), float(beta)


def pixel_ratios(
    bdir: Path,
    bdir_select: Path,
    frames: list[bio.BracketFrame],
    roi: int,
    lo: float = 0.1,
    hi: float = 0.7,
) -> list[dict[str, float]]:
    """화소값으로 구한 노출비 Σn_j / Σn_i vs 메타데이터 t_j / t_i (프레임 쌍 j < i).

    화소는 다른 세트(bdir_select)의 프레임 j가 lo ≤ n < hi인 곳을 고른다.
    두 세트의 노이즈는 독립이라 선택이 비율을 치우치게 하지 않는다.
    (같은 프레임 값으로 고르면 잘린 노이즈만큼 편향된다.)
    """

    def norm(d: Path) -> list[np.ndarray]:
        raws = [ptc.center_crop(r, roi).astype(np.float64) for r in bio.load_raws(d, frames)]
        return [(r - f.black) / (f.white - f.black) for r, f in zip(raws, frames, strict=True)]

    n, sel = norm(bdir), norm(bdir_select)
    out = []
    for j in range(len(frames)):
        m = (sel[j] >= lo) & (sel[j] < hi)
        if m.sum() < 1000:
            continue
        for i in range(j + 1, len(frames)):
            si = n[i][m].sum()
            if si / m.sum() < 0.01:  # 짧은 노출 신호가 너무 작으면 생략
                continue
            rp = float(n[j][m].sum() / si)
            rm = frames[j].exposure_us / frames[i].exposure_us
            out.append(
                {
                    "ref": j,
                    "frame": i,
                    "pixel_ratio": rp,
                    "metadata_ratio": rm,
                    "rel_diff": rp / rm - 1,
                    "n_pixels": int(m.sum()),
                }
            )
    return out


def merge(bdir: Path, frames: list[int], config: Path, out: Path) -> tuple[np.ndarray, np.ndarray]:
    cli.run_cpp("hdr_merge", config=config, in_=bdir, out=out, frames=frames)
    return np.load(out / "merged.npy"), np.load(out / "source.npy")


def analyze_sweep(
    sweep_dir: Path,
    variants: list[dict[str, Any]],
    single: dict[str, Any],
    merge_config: Path,
    work: Path,
    p: MeasureParams | None = None,
) -> dict[str, Any]:
    p = p or MeasureParams()
    plan = json.loads((sweep_dir / "plan.json").read_text())
    mparams = json.loads(merge_config.read_text())
    tau, band_ratio = float(mparams.get("tau", 0.9)), float(mparams.get("band_ratio", 0.8))
    a, b = single["segments"][0]["a"], single["segments"][0]["b"]
    edges = bin_edges(p)
    nb = len(edges) - 1
    labels: list[str] | None = None
    acc: dict[str, dict[str, BinAcc]] = {v["name"]: {} for v in variants}
    steps_out: list[dict[str, Any]] = []
    ratio_checks: list[dict[str, Any]] = []
    for si, st in enumerate(plan["steps"]):
        if st.get("status") != "ok":
            continue
        sdir = sweep_dir / st["dir"]
        sets = sorted(d for d in sdir.iterdir() if d.name.startswith("bracket_"))
        pairs = [(sets[i], sets[i + 1]) for i in range(0, len(sets) - 1, 2)]
        frames_all = bio.read_bracket(sets[0])
        if labels is None:
            labels = ptc.channel_labels(frames_all[0].meta["bayer_order"])
        for rc in pixel_ratios(sets[0], sets[1], frames_all, p.roi):
            ratio_checks.append({"step": si, "level": st["level"], **rc})
        srec: dict[str, Any] = {
            "step": si,
            "level": st["level"],
            "exposures_us": [f.exposure_us for f in frames_all],
            "variants": {},
        }
        for v in variants:
            fr = bio.read_bracket(sets[0], v["frames"])
            ratios = [fr[0].exposure_us / f.exposure_us for f in fr]

            def model_var(x: np.ndarray, ratios: list[float] = ratios) -> np.ndarray:
                return mn.band_variance(x, a, b, ratios, tau, band_ratio)

            excesses = []
            step_acc = {lab: BinAcc.zeros(nb) for lab in labels}
            for pa, pb in pairs:
                ma, sa = merge(pa, v["frames"], merge_config, work / "a")
                mb, sb = merge(pb, v["frames"], merge_config, work / "b")
                ma, mb = ptc.center_crop(ma, p.roi), ptc.center_crop(mb, p.roi)
                sa, sb = ptc.center_crop(sa, p.roi), ptc.center_crop(sb, p.roi)
                valid = ((sa & SATURATED) == 0) & ((sb & SATURATED) == 0)
                for c, lab in enumerate(labels):
                    dy, dx = ptc.CHANNEL_BAYER_POS[c]
                    pa_ = ma[dy::2, dx::2].astype(np.float64)
                    pb_ = mb[dy::2, dx::2].astype(np.float64)
                    bacc, ex = plane_bins(pa_, pb_, valid[dy::2, dx::2], edges, model_var, p)
                    step_acc[lab].add(bacc)
                    excesses.append(ex)
            flick = max(excesses) - p.dark_rowdet_excess > p.flicker_flag
            srec["variants"][v["name"]] = {
                "ratios_actual": ratios,
                "rowdet_excess_max": max(excesses),
                "flicker": bool(flick),
            }
            if not flick:
                for lab, ba in step_acc.items():
                    acc[v["name"]].setdefault(lab, BinAcc.zeros(nb)).add(ba)
        steps_out.append(srec)
        shutil.rmtree(work, ignore_errors=True)

    result: dict[str, Any] = {
        "params": asdict(p),
        "tau": tau,
        "band_ratio": band_ratio,
        "single": {"a": a, "b": b},
        "edges": edges.tolist(),
        "steps": steps_out,
        "ratio_checks": ratio_checks,
        "variants": {},
    }
    for v in variants:
        name = v["name"]
        ch = acc[name]
        allc = BinAcc.zeros(nb)
        for ba in ch.values():
            allc.add(ba)
        nominal = [float(r) for r in v["ratios"]]
        regs = regions(nominal, tau, band_ratio, p.edge_margin)
        result["variants"][name] = {
            "frames": v["frames"],
            "ratios_nominal": nominal,
            "bins": {"all": bins_table(allc, p), **{k: bins_table(x, p) for k, x in ch.items()}},
            "regions": evaluate_regions(allc, ch, regs, p),
            "dips": measure_dips(allc, nominal, a, b, tau, band_ratio, p),
        }
    return result


def bins_table(acc: BinAcc, p: MeasureParams) -> list[dict[str, float]]:
    rows = []
    for j in np.nonzero(acc.n >= p.min_pixels)[0]:
        var = acc.var_dof[j] / acc.dof[j]
        model = acc.model_dof[j] / acc.dof[j]
        x = acc.x_sum[j] / acc.n[j]
        rows.append(
            {
                "bin": int(j),
                "x": float(x),
                "n": int(acc.n[j]),
                "var": float(var),
                "model": float(model),
                "rel_err": float(var / model - 1),
                "snr_db": float(mn.snr_db(x, var)),
            }
        )
    return rows


def _region_stat(acc: BinAcc, r: dict[str, Any], p: MeasureParams) -> dict[str, Any] | None:
    x = np.divide(acc.x_sum, acc.n, out=np.zeros_like(acc.x_sum), where=acc.n > 0)
    m = (acc.n >= p.min_pixels) & (x >= r["x0"]) & (x < r["x1"])
    if not m.any():
        return None
    err = acc.var_dof[m].sum() / acc.model_dof[m].sum() - 1
    be = acc.var_dof[m] / acc.model_dof[m] - 1
    return {
        "rel_err": float(err),
        "max_abs_bin_err": float(np.abs(be).max()),
        "n_bins": int(m.sum()),
        "n_pixels": int(acc.n[m].sum()),
        "x_range": [float(x[m].min()), float(x[m].max())],
    }


def evaluate_regions(
    allc: BinAcc, ch: dict[str, BinAcc], regs: list[dict[str, Any]], p: MeasureParams
) -> list[dict[str, Any]]:
    out = []
    for r in regs:
        s = _region_stat(allc, r, p)
        rec = {**r, "all": s, "channels": {k: _region_stat(a, r, p) for k, a in ch.items()}}
        if r["kind"] == "seg":
            rec["pass"] = None if s is None else abs(s["rel_err"]) <= p.tolerance
        out.append(rec)
    return out


def measure_dips(
    acc: BinAcc,
    ratios: list[float],
    a: float,
    b: float,
    tau: float,
    band_ratio: float,
    p: MeasureParams,
) -> list[dict[str, Any]]:
    """경계 x_b = τ·R_k에서 양쪽 단독 구간의 실측 직선(가중 최소제곱)을 외삽해 SNR 차이를 낸다."""
    regs = [r for r in regions(ratios, tau, band_ratio, p.edge_margin) if r["kind"] == "seg"]
    x = np.divide(acc.x_sum, acc.n, out=np.zeros_like(acc.x_sum), where=acc.n > 0)
    var = np.divide(acc.var_dof, acc.dof, out=np.zeros_like(acc.var_dof), where=acc.dof > 0)
    fits = []
    for r in regs:
        m = (acc.n >= p.min_pixels) & (x >= r["x0"]) & (x < r["x1"])
        if m.sum() < 3:
            fits.append(None)
            continue
        fits.append(wls_line(x[m], var[m], acc.dof[m] / var[m] ** 2))
    out = []
    for k in range(len(ratios) - 1):
        xb = tau * ratios[k]
        pred = 10 * np.log10(
            (a * ratios[k + 1] * xb + b * ratios[k + 1] ** 2)
            / (a * ratios[k] * xb + b * ratios[k] ** 2)
        )
        shot = float(10 * np.log10(ratios[k + 1] / ratios[k]))
        rec: dict[str, Any] = {"x": xb, "dip_pred_db": float(pred), "shot_limit_db": shot}
        if fits[k] and fits[k + 1]:
            v0 = fits[k][0] * xb + fits[k][1]
            v1 = fits[k + 1][0] * xb + fits[k + 1][1]
            rec.update(
                {
                    "dip_meas_db": float(10 * np.log10(v1 / v0)),
                    "snr_before_meas_db": float(mn.snr_db(xb, v0)),
                    "snr_after_meas_db": float(mn.snr_db(xb, v1)),
                    "fit_before": fits[k],
                    "fit_after": fits[k + 1],
                }
            )
        out.append(rec)
    return out
