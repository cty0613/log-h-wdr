"""Phase 2: 플랫필드 브래킷 스윕 → 합성 후 노이즈 실측 vs 해석 모델 (CLAUDE.md 6.3).

사용: python experiments/p2_merged_noise.py configs/p2_merged_noise.yaml
출력: results/p2_merged_noise/<timestamp>/{report.json, bins.csv, regions.csv, var_ratio.png,
      snr_measured.png, summary.md, noise_<variant>_measured.json}
      + results/p2/merged_noise_summary.md
수락 기준 (CLAUDE.md Phase 2): 단독 구간마다 실측 분산이 해석 모델과 ±10% → 아니면 종료 코드 1.
못 맞추면 기준을 바꾸지 않고, 실측 직선 피팅 JSON(noise_<variant>_measured.json)을 남겨
원인 분석과 채택 여부를 docs/decisions.md에서 정한다.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from cctvlog import cli, runner
from cctvlog.sensor import merged_measure as mm
from cctvlog.sensor import merged_noise as mn
from cctvlog.sensor import noise_model

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def _p(path: str) -> Path:
    q = Path(path)
    return q if q.is_absolute() else cli.REPO_ROOT / q


def style(ax: plt.Axes) -> None:
    ax.grid(True, color=GRID, lw=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=INK2)


def shade_bands(ax: plt.Axes, ratios: list[float], tau: float, band_ratio: float) -> None:
    for r in ratios[:-1]:
        ax.axvspan(np.log2(band_ratio * tau * r), np.log2(tau * r), color=GRID, alpha=0.7, lw=0)


def plot_ratio(rep: dict[str, Any], cfg: dict[str, Any], out: Path) -> None:
    vs = cfg["variants"]
    fig, axes = plt.subplots(len(vs), 1, figsize=(9, 2.6 * len(vs)), sharex=True)
    tol = rep["params"]["tolerance"] * 100
    for ax, v in zip(np.atleast_1d(axes), vs, strict=True):
        style(ax)
        shade_bands(ax, v["ratios"], rep["tau"], rep["band_ratio"])
        ax.axhspan(-tol, tol, color="#1baf7a", alpha=0.08, lw=0)
        ax.axhline(0, color=INK2, lw=0.8)
        bins = rep["variants"][v["name"]]["bins"]
        for ci, ch in enumerate(k for k in bins if k != "all"):
            b = bins[ch]
            ax.plot(
                np.log2([r["x"] for r in b]),
                [r["rel_err"] * 100 for r in b],
                ".",
                ms=3,
                color=SERIES[ci],
                alpha=0.6,
                label=ch,
            )
        b = bins["all"]
        ax.plot(
            np.log2([r["x"] for r in b]),
            [r["rel_err"] * 100 for r in b],
            "-",
            lw=2,
            color=INK,
            label="all channels",
        )
        ax.set_ylabel("measured / model − 1 [%]", color=INK, fontsize=8.5)
        ax.set_title(
            f"{v['name']} (gray = blend band, model includes band)", color=INK, fontsize=10
        )
        ax.set_ylim(-30, 30)
    np.atleast_1d(axes)[0].legend(fontsize=8, ncol=5, frameon=False, loc="upper left")
    np.atleast_1d(axes)[-1].set_xlabel("x [stop] (0 = long-exposure saturation)", color=INK)
    fig.tight_layout()
    fig.savefig(out / "var_ratio.png", dpi=130)
    plt.close(fig)


def plot_snr(rep: dict[str, Any], cfg: dict[str, Any], out: Path) -> None:
    a, b = rep["single"]["a"], rep["single"]["b"]
    fig, ax = plt.subplots(figsize=(9, 5.6))
    style(ax)
    for ci, v in enumerate(cfg["variants"]):
        x = np.geomspace(2.0**-7, v["ratios"][-1], 800)
        snr = mn.snr_db(x, mn.band_variance(x, a, b, v["ratios"], rep["tau"], rep["band_ratio"]))
        ax.plot(np.log2(x), snr, color=SERIES[ci], lw=2, label=f"{v['name']} model")
        bins = rep["variants"][v["name"]]["bins"]["all"]
        ax.plot(
            np.log2([r["x"] for r in bins]),
            [r["snr_db"] for r in bins],
            "o",
            ms=3.5,
            color=SERIES[ci],
            mfc="white",
            mew=1.1,
            label=f"{v['name']} measured",
        )
    ax.set_xlabel("x [stop] (0 = long-exposure saturation)", color=INK)
    ax.set_ylabel("SNR [dB] (Bayer domain, all channels)", color=INK)
    ax.set_title("Merged SNR: flat-field bracket sweep vs analytic model", color=INK)
    ax.legend(fontsize=8.5, ncol=2, frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(out / "snr_measured.png", dpi=130)
    plt.close(fig)


def measured_model(rep: dict[str, Any], v: dict[str, Any], source: dict[str, Any]) -> dict:
    """실측 단독 구간 직선 σ² = a·x + b (가중 최소제곱) → 5.4 JSON (채택은 decisions.md에서)."""
    dips = rep["variants"][v["name"]]["dips"]
    fits: list[Any] = [d.get("fit_before") for d in dips] + [dips[-1].get("fit_after")]
    segs = []
    for k, f in enumerate(fits):
        x0 = 0.0 if k == 0 else rep["tau"] * v["ratios"][k - 1]
        segs.append({"x_start": x0, "a": f[0] if f else None, "b": f[1] if f else None})
    return {
        "kind": "bracket",
        "scale": "normalized_long_exposure",
        "x_max": float(v["ratios"][-1]),
        "segments": segs,
        "assumptions": {
            "source": "measured (flat-field bracket sweep)",
            **source,
            "note": "band ignored (jump at tau); not adopted unless decided",
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def summary_md(rep: dict[str, Any], cfg: dict[str, Any], ok: bool) -> str:
    tol = rep["params"]["tolerance"]
    L = [
        "# Phase 2 합성 후 노이즈 실측 요약",
        "",
        f"- 스윕: `{cfg['sweep_dir']}` ({len(rep['steps'])} 단계)",
        f"- ROI {rep['params']['roi']} px, 구간 {rep['params']['bins_per_stop']}/stop, "
        f"국소 평균 {rep['params']['box']}×{rep['params']['box']}",
        f"- 수락 기준: 단독 구간 실측/모델 − 1 이 ±{tol * 100:.0f}% 이내 → "
        f"**{'PASS' if ok else 'FAIL'}**",
        "",
    ]
    for v in cfg["variants"]:
        V = rep["variants"][v["name"]]
        L += [
            f"## {v['name']} (노출비 {v['ratios']})",
            "",
            "| 영역 | x 범위 | 구간 수 | 화소 | 실측/모델 − 1 (전체) | 최대 구간 오차 | "
            "채널별 (" + ", ".join(k for k in V["bins"] if k != "all") + ") | 판정 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in V["regions"]:
            s = r["all"]
            name = f"{r['kind']} {r['frame']}" + (" (띠, 정확한 식)" if r["kind"] == "band" else "")
            if s is None:
                L.append(f"| {name} | {r['x0']:.3g} ~ {r['x1']:.3g} | 0 | — | — | — | — | — |")
                continue
            chs = ", ".join(
                f"{c['rel_err'] * 100:+.1f}%" if c else "—" for c in r["channels"].values()
            )
            verdict = "—" if r["kind"] == "band" else ("PASS" if r["pass"] else "FAIL")
            L.append(
                f"| {name} | {s['x_range'][0]:.3g} ~ {s['x_range'][1]:.3g} | {s['n_bins']} | "
                f"{s['n_pixels']:,} | {s['rel_err'] * 100:+.1f}% | "
                f"{s['max_abs_bin_err'] * 100:.1f}% | {chs} | {verdict} |"
            )
        L += [
            "",
            "| 경계 x | 예측 dip [dB] | 샷 노이즈 한계 10·log10 R | 실측 dip [dB] |",
            "|---|---|---|---|",
        ]
        for d in V["dips"]:
            meas = f"{d['dip_meas_db']:.2f}" if "dip_meas_db" in d else "—"
            L.append(
                f"| {d['x']:.3g} | {d['dip_pred_db']:.2f} | {d['shot_limit_db']:.2f} | {meas} |"
            )
        L.append("")
    rc = rep["ratio_checks"]
    if rc:
        L += [
            "## 화소 기준 노출비 vs 메타데이터",
            "",
            "| 쌍 | 단계 수 | 중앙값 | 최소 | 최대 |",
            "|---|---|---|---|---|",
        ]
        for key in sorted({(r["ref"], r["frame"]) for r in rc}):
            d = np.array([r["rel_diff"] for r in rc if (r["ref"], r["frame"]) == key]) * 100
            L.append(
                f"| {key[0]}→{key[1]} | {d.size} | {np.median(d):+.2f}% | "
                f"{d.min():+.2f}% | {d.max():+.2f}% |"
            )
        L.append("")
    fl = [(s["step"], n) for s in rep["steps"] for n, v in s["variants"].items() if v["flicker"]]
    L += [
        f"깜빡임으로 제외된 (단계, 구성): {fl or '없음'}",
        "",
        "그림: var_ratio.png, snr_measured.png",
        "",
    ]
    return "\n".join(L)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    exp = runner.start_experiment(argv[0])
    cfg, out = exp.config, exp.out_dir
    single = noise_model.load(_p(cfg["single_noise"]))
    p = mm.MeasureParams(**cfg.get("measure", {}))
    rep = mm.analyze_sweep(
        _p(cfg["sweep_dir"]), cfg["variants"], single, _p(cfg["merge_config"]), out / "work", p
    )
    shutil.rmtree(out / "work", ignore_errors=True)
    source = {
        "sweep_dir": cfg["sweep_dir"],
        "results_dir": str(out.relative_to(cli.REPO_ROOT)),
        "git": runner.git_info(),
    }
    for v in cfg["variants"]:
        noise_model.write(measured_model(rep, v, source), out / f"noise_{v['name']}_measured.json")
    ok = all(
        r["pass"] for v in rep["variants"].values() for r in v["regions"] if r["kind"] == "seg"
    )
    rep["acceptance_pass"] = ok
    (out / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    write_csv(
        out / "bins.csv",
        [
            {"variant": n, "channel": c, **r}
            for n, V in rep["variants"].items()
            for c, rows in V["bins"].items()
            for r in rows
        ],
    )
    write_csv(
        out / "regions.csv",
        [
            {
                "variant": n,
                "kind": r["kind"],
                "frame": r["frame"],
                "x0": r["x0"],
                "x1": r["x1"],
                "pass": r.get("pass"),
                **(r["all"] or {}),
            }
            for n, V in rep["variants"].items()
            for r in V["regions"]
        ],
    )
    plot_ratio(rep, cfg, out)
    plot_snr(rep, cfg, out)
    md = summary_md(rep, cfg, ok)
    (out / "summary.md").write_text(md, encoding="utf-8")
    copy = _p(cfg["summary_copy"])
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out / "summary.md", copy)
    print(md)
    print(f"→ {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
