"""Phase 2: 해석 노이즈 모델 (브래킷 R=4, R=16 3장/2장, DCG) + DCG 몬테카를로 + SNR 그래프.

사용: python experiments/p2_models.py configs/p2_models.yaml
출력: configs/noise_bracket_{R4,R16,R16_2f}.json, configs/noise_dcg.json,
      results/p2_models/<timestamp>/{summary.json, summary.md, snr_models.png/csv,
      dcg_sensitivity.png/csv, dcg_mc.csv, dcg_sim_g*/run.json}
수락 기준 (Phase 2 일부): DCG 몬테카를로 분산이 해석값과 ±3% (레벨당 샘플이 적어 4σ 허용으로 판정)
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from cctvlog import cli, runner
from cctvlog.sensor import merged_noise as mn
from cctvlog.sensor import noise_model

# dataviz 기본 팔레트 (light), 고정 순서
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _p(path: str) -> Path:
    q = Path(path)
    return q if q.is_absolute() else cli.REPO_ROOT / q


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def style(ax: plt.Axes) -> None:
    ax.grid(True, color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2)


def write_csv(path: Path, header: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def dcg_mc(cfg: dict[str, Any], p: dict[str, Any], out: Path) -> list[dict[str, float]]:
    """상수 밝기 블록 장면 → dcg_sim → 레벨별 평균·분산."""
    mc = cfg["dcg"]["mc"]
    xs = np.geomspace(mc["x_min"], mc["x_max"], mc["levels"])
    rows = mc["rows_per_level"]
    scene = np.repeat(xs, rows)[:, None].repeat(mc["width"], 1).astype(np.float32)
    out.mkdir(parents=True, exist_ok=True)
    np.save(out / "scene.npy", scene)
    pc = dict(p)
    pc["seed"] = mc["seed"]
    (out / "dcg.json").write_text(json.dumps(pc, indent=2))
    cli.run_cpp("dcg_sim", config=out / "dcg.json", in_=out / "scene.npy", out=out)
    sim = np.load(out / "dcg.npy").astype(np.float64)
    res = []
    for i, x in enumerate(xs):
        blk = sim[i * rows : (i + 1) * rows].ravel()
        v = float(blk.var(ddof=1))
        va = float(mn.dcg_band_variance(x, p))
        res.append(
            {
                "x": float(x),
                "mean": float(blk.mean()),
                "var": v,
                "var_analytic": va,
                "ratio": v / va,
                "n": int(blk.size),
            }
        )
    for f in ("scene.npy", "dcg.npy", "dcg_source.npy"):
        (out / f).unlink()
    return res


def plot_snr(models: dict[str, Any], cfg: dict[str, Any], hdr: list[dict[str, Any]], out: Path):
    a, b = models["single"]["segments"][0]["a"], models["single"]["segments"][0]["b"]
    fig, ax = plt.subplots(figsize=(9, 5.6))
    style(ax)
    rows: list[list[Any]] = []
    x_all = np.geomspace(2.0**-12, 16.0, 1200)
    curves = [("single", "single exposure (g1)", None)]
    curves += [(br["name"], br["label"], br["ratios"]) for br in cfg["brackets"]]
    curves += [("dcg", "DCG g=4 (option B: x=16 → LCG full)", "dcg")]
    for ci, (key, label, kind) in enumerate(curves):
        m = models[key]
        x = x_all[x_all <= m["x_max"]]
        if kind is None:
            v = mn.model_variance(m, x)
        elif kind == "dcg":
            v = mn.dcg_band_variance(x, models["dcg_params"])
        else:
            v = mn.band_variance(x, a, b, kind, cfg["tau"], cfg["band_ratio"])
        snr = mn.snr_db(x, v)
        ax.plot(np.log2(x), snr, color=SERIES[ci], lw=2, label=label)
        rows += [[key, float(xx), float(s)] for xx, s in zip(x, snr, strict=True)]
    # 참고선: DCG 옵션 A
    pa = dict(models["dcg_params"])
    pa["scene_scale_e_per_unit"] = cfg["dcg"]["option_a_scene_scale_e_per_unit"]
    xa = x_all[x_all <= 1.0]
    sa = mn.snr_db(xa, mn.dcg_band_variance(xa, pa))
    ax.plot(
        np.log2(xa),
        sa,
        color=INK2,
        lw=1.2,
        ls="--",
        label="DCG g=4, option A (x=1 → LCG full), reference",
    )
    rows += [["dcg_option_a", float(xx), float(s)] for xx, s in zip(xa, sa, strict=True)]
    if hdr:
        hx = np.array([h["x"] for h in hdr])
        hs = np.array([h["snr_in_db"] for h in hdr])
        ax.plot(
            np.log2(hx),
            hs,
            "o",
            ms=5,
            color=SERIES[6],
            mfc="white",
            mew=1.5,
            label="sensor HDR mode (measured, input-referred)",
        )
        rows += [["sensor_hdr_measured", float(xx), float(s)] for xx, s in zip(hx, hs, strict=True)]
    ax.set_xlabel("scene brightness [stop]  (0 = single long-exposure saturation)", color=INK)
    ax.set_ylabel("SNR [dB]  (signal / temporal noise, Bayer domain)", color=INK)
    ax.set_title("Merged-noise SNR: single vs bracket vs DCG (analytic, band included)", color=INK)
    ax.set_xlim(-12, 4.2)
    ax.set_ylim(-5, 50)
    ax.legend(fontsize=8.5, loc="upper left", frameon=False, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(out / "snr_models.png", dpi=130)
    plt.close(fig)
    write_csv(out / "snr_models.csv", ["model", "x_linear", "snr_db"], rows)


def plot_dcg(
    p: dict[str, Any], ratios: list[int], mcs: dict[int, list[dict[str, float]]], out: Path
):
    fig, (ax, axr) = plt.subplots(
        2, 1, figsize=(9, 6.4), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]}
    )
    style(ax)
    style(axr)
    rows: list[list[Any]] = []
    for ci, g in enumerate(ratios):
        pg = dict(p, conversion_gain_ratio=g, hcg_full_well_e=None, hcg_read_noise_e=None)
        x = np.geomspace(2.0**-12, mn.dcg_model(pg)["x_max"], 800)
        snr = mn.snr_db(x, mn.dcg_band_variance(x, pg))
        s = mn.summary(mn.dcg_model(pg))
        ax.plot(
            np.log2(x),
            snr,
            color=SERIES[ci],
            lw=2,
            label=f"g={g}: DR {s['dr_stops']:.1f} stop, dip {s['dips'][0]['dip_db']:.2f} dB",
        )
        mc = mcs[g]
        mx = np.array([r["x"] for r in mc])
        ax.plot(
            np.log2(mx),
            mn.snr_db(mx, np.array([r["var"] for r in mc])),
            "o",
            ms=4,
            color=SERIES[ci],
            mfc="white",
            mew=1.2,
        )
        axr.plot(
            np.log2(mx),
            (np.array([r["ratio"] for r in mc]) - 1) * 100,
            "o-",
            ms=3.5,
            lw=1,
            color=SERIES[ci],
        )
        rows += [[g, r["x"], r["mean"], r["var"], r["var_analytic"], r["n"]] for r in mc]
    axr.axhspan(-3, 3, color=GRID, alpha=0.6, lw=0)
    axr.axhline(0, color=INK2, lw=0.8)
    axr.set_ylabel("MC / analytic − 1 [%]", color=INK)
    axr.set_xlabel("scene brightness [stop]  (0 = single long-exposure saturation)", color=INK)
    ax.set_ylabel("SNR [dB]", color=INK)
    ax.set_title(
        "DCG conversion-gain ratio sensitivity (lines: analytic, dots: dcg_sim)", color=INK
    )
    ax.legend(fontsize=8.5, loc="lower right", frameon=False, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(out / "dcg_sensitivity.png", dpi=130)
    plt.close(fig)
    write_csv(out / "dcg_mc.csv", ["g", "x", "mean", "var", "var_analytic", "n"], rows)


def sensor_hdr_points(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    rep = json.loads(path.read_text())
    t_sat = float(rep["normal_mode_t_sat_us"])
    run0 = Path(rep["runs"][0]).name  # 최소 게인 실행
    return [
        {"x": s["E_us"] / t_sat, "snr_in_db": s["snr_in_db"]}
        for s in rep["steps"]
        if s["run"] == run0 and s.get("snr_in_db") is not None and s["sat_fraction"] == 0
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    exp = runner.start_experiment(argv[0])
    cfg, out = exp.config, exp.out_dir
    single_path = _p(cfg["single_noise"])
    single = noise_model.load(single_path)
    source = {
        "single_noise": cfg["single_noise"],
        "single_noise_sha256": _sha(single_path),
        "generator": "experiments/p2_models.py",
        "results_dir": str(out.relative_to(cli.REPO_ROOT)),
        "git": runner.git_info(),
    }
    models: dict[str, Any] = {"single": single}
    summ: dict[str, Any] = {"single": mn.summary(single)}
    for br in cfg["brackets"]:
        m = mn.bracket_model(
            single,
            br["ratios"],
            cfg["tau"],
            cfg["band_ratio"],
            {"name": br["name"], "source": source},
        )
        noise_model.write(m, _p(br["out"]))
        models[br["name"]] = m
        summ[br["name"]] = {**mn.summary(m), "path": br["out"]}

    dcg_path = _p(cfg["dcg"]["config"])
    p = runner.load_config(dcg_path)
    p = {k: v for k, v in p.items() if not k.startswith("_")}
    m = mn.dcg_model(p)
    m["assumptions"]["source"] = {
        **source,
        "dcg_assumptions": cfg["dcg"]["config"],
        "dcg_assumptions_sha256": _sha(dcg_path),
    }
    noise_model.write(m, _p(cfg["dcg"]["out"]))
    models["dcg"], models["dcg_params"] = m, p
    summ["dcg"] = {**mn.summary(m), "path": cfg["dcg"]["out"]}

    # C++ dcg_sim의 해석 JSON이 같은지 (교차 확인)
    cli.run_cpp("dcg_sim", config=dcg_path, out=out / "dcg_noise_cpp")
    cpp = json.loads((out / "dcg_noise_cpp" / "noise_dcg.json").read_text())
    seg_err = max(
        abs(c[k] - q[k]) / abs(q[k]) if q[k] else abs(c[k])
        for c, q in zip(cpp["segments"], m["segments"], strict=True)
        for k in ("x_start", "a", "b")
    )
    summ["dcg_cpp_json_max_rel_err"] = seg_err

    ratios = cfg["dcg"]["sensitivity_ratios"]
    mcs: dict[int, list[dict[str, float]]] = {}
    sens: dict[str, Any] = {}
    for g in ratios:
        pg = dict(p, conversion_gain_ratio=g, hcg_full_well_e=None, hcg_read_noise_e=None)
        mcs[g] = dcg_mc(cfg, pg, out / f"dcg_sim_g{g}")
        dm = mn.dcg_model(pg)
        r = np.array([x["ratio"] for x in mcs[g]])
        n = np.array([x["n"] for x in mcs[g]])
        se = np.sqrt(2.0 / (n - 1))
        sens[str(g)] = {
            **mn.summary(dm),
            "hcg_read_noise_e": dm["assumptions"]["hcg_read_noise_e"],
            "hcg_full_well_e": dm["assumptions"]["hcg_full_well_e"],
            "mc_max_abs_rel_err": float(np.max(np.abs(r - 1))),
            "mc_median_abs_rel_err": float(np.median(np.abs(r - 1))),
            "mc_max_z": float(np.max(np.abs(r - 1) / se)),
        }
    summ["dcg_sensitivity"] = sens
    pa = dict(p, scene_scale_e_per_unit=cfg["dcg"]["option_a_scene_scale_e_per_unit"])
    summ["dcg_option_a"] = mn.summary(mn.dcg_model(pa))

    hdr = sensor_hdr_points(_p(cfg["sensor_hdr_report"]) if cfg.get("sensor_hdr_report") else None)
    plot_snr(models, cfg, hdr, out)
    plot_dcg(p, ratios, mcs, out)
    # 섀도우 비교 (decisions.md 결정 3 수치)
    a0, b0 = single["segments"][0]["a"], single["segments"][0]["b"]
    summ["shadow_snr_db"] = {
        str(x): {
            "bracket_long": float(mn.snr_db(x, a0 * x + b0)),
            "dcg_option_b": float(mn.snr_db(x, mn.dcg_band_variance(x, p))),
        }
        for x in (0.001, 0.01, 0.1)
    }
    (out / "summary.json").write_text(json.dumps(summ, indent=2, ensure_ascii=False))
    md = summary_md(summ, cfg)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"→ {out}")
    ok = all(v["mc_max_z"] <= 4.0 for v in sens.values()) and seg_err < 1e-12
    return 0 if ok else 1


def summary_md(s: dict[str, Any], cfg: dict[str, Any]) -> str:
    L = [
        "# Phase 2 해석 노이즈 모델 요약",
        "",
        "| 모델 | 구간 | x_max | DR [stop] | 경계 dip [dB] | N | 필요 비트 | R(x) @10bit |",
        "|---|---|---|---|---|---|---|---|",
    ]
    names = ["single", *[b["name"] for b in cfg["brackets"]], "dcg"]
    for k in names:
        v = s[k]
        dips = ", ".join(f"{d['dip_db']:.2f}" for d in v["dips"]) or "—"
        L.append(
            f"| {k} | {v['n_segments']} | {v['x_max']:.4g} | {v['dr_stops']:.2f} | {dips} | "
            f"{v['N']:.0f} | {v['required_bits']} | {v['R_at_bits']['10']:.3f} |"
        )
    L += [
        "",
        f"C++ dcg_sim 해석 JSON vs Python: 최대 상대오차 {s['dcg_cpp_json_max_rel_err']:.2e}",
        "",
        "## DCG 변환 게인 비 민감도 (HCG 값 = LCG ÷ g, 이상적 규칙)",
        "",
        "| g | HCG 판독 [e⁻] | HCG full well [e⁻] | DR [stop] | dip [dB] | N | 비트 | "
        "MC 분산 오차 최대 / 중앙값 | 최대 z |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for g, v in s["dcg_sensitivity"].items():
        L.append(
            f"| {g} | {v['hcg_read_noise_e']:.2f} | {v['hcg_full_well_e']:.0f} | "
            f"{v['dr_stops']:.2f} | {v['dips'][0]['dip_db']:.2f} | {v['N']:.0f} | "
            f"{v['required_bits']} | {v['mc_max_abs_rel_err'] * 100:.2f}% / "
            f"{v['mc_median_abs_rel_err'] * 100:.2f}% | {v['mc_max_z']:.2f} |"
        )
    a = s["dcg_option_a"]
    L += [
        "",
        f"DCG 옵션 A(참고): DR {a['dr_stops']:.2f} stop, N {a['N']:.0f}",
        "",
        "섀도우 SNR [dB] (브래킷 긴 노출 vs DCG 옵션 B):",
        "",
    ]
    for x, v in s["shadow_snr_db"].items():
        L.append(f"- x = {x}: {v['bracket_long']:.1f} vs {v['dcg_option_b']:.1f}")
    L += ["", "그림: snr_models.png, dcg_sensitivity.png", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
