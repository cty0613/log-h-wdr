"""Phase 1: PTC → 게인별 노이즈 모델, 센서 요약.

사용: python experiments/p1_ptc.py configs/p1_ptc.yaml
출력: results/p1/<timestamp>/{report.json, points.csv, dark.csv, ptc_*.png, linearity.png,
      sensor_summary.md} + configs/noise_single_{g1,g4,g16}.json + results/p1/sensor_summary.md
수락 기준 (CLAUDE.md Phase 1): 각 게인 R² ≥ 0.99 → 실패 시 종료 코드 1.
"""

from __future__ import annotations

import csv
import hashlib
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
from cctvlog.sensor import noise_model, ptc

COLORS = {"R": "tab:red", "B": "tab:blue", "G1": "tab:green", "G2": "tab:olive"}


def _p(path: str) -> Path:
    q = Path(path)
    return q if q.is_absolute() else cli.REPO_ROOT / q


def plot_ptc(rep: dict[str, Any], label: str, out: Path) -> None:
    g = rep["gains"][label]
    pts = [r for r in rep["points"] if r["gain_label"] == label]
    fs = rep["full_scale_dn"]
    fig, (ax, axr) = plt.subplots(
        2, 1, figsize=(7.5, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1.3]}
    )
    for ch in COLORS:
        u = [r for r in pts if r["channel"] == ch and r["use"]]
        # 제외 점 (x 표시). 거의 전부 포화된 쌍은 분산이 0에 가까워 그리지 않는다.
        x = [
            r
            for r in pts
            if r["channel"] == ch and not r["use"] and r["mean_dn"] > 0 and r["sat_fraction"] < 0.5
        ]
        if u:
            ax.loglog(
                [r["mean_dn"] for r in u],
                [r["var_dn2"] for r in u],
                "o",
                ms=4,
                color=COLORS[ch],
                label=ch,
            )
        if x:
            ax.loglog(
                [r["mean_dn"] for r in x],
                [max(r["var_dn2"], 1e-3) for r in x],
                "x",
                ms=5,
                color=COLORS[ch],
                alpha=0.5,
            )
    xx = np.logspace(0, np.log10(fs), 200)
    for key, ls in (("fixed_b", "-"), ("free", "--")):
        f = g[key]
        ax.loglog(
            xx,
            f["K"] * xx + f["b"],
            "k" + ls,
            lw=1,
            label=f"{key}: σ² = {f['K']:.4f}·μ + {f['b']:.3f}  (R² {f['r2']:.5f})",
        )
    ax.axhline(g["b_dark_dn2"], color="gray", ls=":", lw=1, label=f"dark b = {g['b_dark_dn2']:.3f}")
    ax.set_ylabel("temporal variance σ² [DN²]")
    ax.set_title(
        f"PTC {label} (analogue gain {g['analogue_gain']:.4f}), ROI {rep['params']['roi']} px"
    )
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    f = g["fixed_b"]
    for ch in COLORS:
        u = [r for r in pts if r["channel"] == ch and r["use"]]
        if u:
            m = np.array([r["mean_dn"] for r in u])
            v = np.array([r["var_dn2"] for r in u])
            axr.semilogx(m, (v / (f["K"] * m + f["b"]) - 1) * 100, "o", ms=4, color=COLORS[ch])
    axr.axhline(0, color="k", lw=0.8)
    axr.set_xlabel("mean signal μ [DN above black]")
    axr.set_ylabel("residual vs fixed_b [%]")
    axr.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def plot_linearity(rep: dict[str, Any], out: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 4))
    for label, g in rep["gains"].items():
        lin = g["linearity"].get("G1")
        if lin:
            ax.semilogx(
                lin["exposure_us"],
                np.array(lin["deviation"]) * 100,
                "o-",
                ms=4,
                label=f"{label} G1",
            )
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xlabel("actual exposure [µs]")
    ax.set_ylabel("mean / (proportional fit) − 1 [%]")
    ax.set_title("response linearity (mean vs exposure)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r if not isinstance(r[k], (list, dict))))
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def summary_md(rep: dict[str, Any], cfg: dict[str, Any], acc: dict[str, bool]) -> str:
    G = rep["gains"]
    labels = list(G)
    L = ["# Phase 1 센서 요약 (IMX708 wide, 2304×1296 10bit)", ""]
    L += [
        f"- flat: `{rep['flat_dir']}`",
        f"- dark: `{rep['dark_dir']}`",
        f"- ROI: 중앙 {rep['params']['roi']}×{rep['params']['roi']} 센서 화소, 채널 B·G1·G2·R",
        f"- 풀스케일: {rep['full_scale_dn']:.0f} DN (white − black)",
        f"- 노이즈 모델 방식: `{cfg['model_method']}` (docs/decisions.md 2026-11-21)",
        "",
    ]
    L += [
        "## 수락 기준",
        "",
        "| 게인 | R² (fixed_b) | R² (free) | ≥ " + f"{cfg['acceptance_r2']} |",
        "|---|---|---|---|",
    ]
    for k in labels:
        L.append(
            f"| {k} | {G[k]['fixed_b']['r2']:.5f} | {G[k]['free']['r2']:.5f} | "
            f"{'PASS' if acc[k] else 'FAIL'} |"
        )
    L += [
        "",
        "## 센서 특성",
        "",
        "| 항목 | " + " | ".join(labels) + " |",
        "|---" * (len(labels) + 1) + "|",
    ]

    def row(name: str, f: Any) -> None:
        L.append(f"| {name} | " + " | ".join(f(k) for k in labels) + " |")

    D = rep["dark"]
    row("아날로그 게인", lambda k: f"{G[k]['analogue_gain']:.4f}")
    row("블랙 레벨 [DN] (B, G1, G2, R)", lambda k: ", ".join(f"{v:.2f}" for v in D[k]["black_dn"]))
    row("K [DN/e⁻] (fixed_b)", lambda k: f"{G[k]['fixed_b']['K']:.4f}")
    row("K [DN/e⁻] (free)", lambda k: f"{G[k]['free']['K']:.4f}")
    row("변환 이득 [e⁻/DN]", lambda k: f"{G[k]['conversion_gain_e_per_dn']:.2f}")
    row("판독 노이즈 [DN]", lambda k: f"{G[k]['read_noise_dn']:.3f}")
    row("판독 노이즈 [e⁻]", lambda k: f"{G[k]['read_noise_e']:.2f}")
    row(
        "free 피팅 b [DN²] / 다크 b",
        lambda k: f"{G[k]['free']['b']:.3f} / {G[k]['b_dark_dn2']:.3f}",
    )
    row("포화 전자 수 [e⁻] (ADC 클립)", lambda k: f"{G[k]['saturation_e']:,.0f}")
    row("단일 노출 DR [stop / dB]", lambda k: f"{G[k]['dr_stops']:.2f} / {G[k]['dr_db']:.1f}")
    row(
        "PTC 휨 K_high/K_low",
        lambda k: f"{G[k]['curvature'].get('high_over_low', float('nan')):.3f}",
    )
    row("채널 간 K 차이", lambda k: f"{G[k].get('channel_K_spread', float('nan')) * 100:.1f}%")
    row(
        "피팅 점 / 제외",
        lambda k: (
            f"{G[k]['n_points']} / " + ", ".join(f"{r} {n}" for r, n in G[k]["n_excluded"].items())
        ),
    )
    row(
        "응답 선형성 최대 편차 (G1, ≥5% FS)",
        lambda k: f"{G[k]['linearity']['G1']['max_abs_dev_above_5pct'] * 100:.2f}%",
    )
    row(
        "암전류 (최장 노출 블랙 이동 [DN])",
        lambda k: (
            f"{D[k]['dark_current']['black_shift_dn']:+.3f} @ "
            f"{D[k]['dark_current']['exposure_us']} µs"
            if "dark_current" in D[k]
            else "—"
        ),
    )
    L += ["", "## 게인 비 검사", ""]
    base = labels[0]
    for k in labels[1:]:
        gr = G[k]["analogue_gain"] / G[base]["analogue_gain"]
        kr = G[k]["fixed_b"]["K"] / G[base]["fixed_b"]["K"]
        L.append(
            f"- {k}/{base}: 아날로그 게인 비 {gr:.3f}, K 비 {kr:.3f} ({(kr / gr - 1) * 100:+.1f}%)"
        )
    mon = rep["monitor"]
    if mon["n"]:
        L.append(f"- 광원 모니터: {mon['n']}장, 범위 {mon['range_rel'] * 100:.2f}%")
    L += [
        "",
        "## 노이즈 모델 (5.4, 정규화 x = (v − black)/FS)",
        "",
        "| 게인 | a | b | 파일 |",
        "|---|---|---|---|",
    ]
    for k in labels:
        m = rep["noise_models"][k]
        s = m["model"]["segments"][0]
        L.append(f"| {k} | {s['a']:.6g} | {s['b']:.6g} | `{m['path']}` |")
    L += ["", "그림: " + ", ".join(f"ptc_{k}.png" for k in labels) + ", linearity.png", ""]
    return "\n".join(L)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    exp = runner.start_experiment(argv[0])
    cfg = exp.config
    params = ptc.PtcParams(**cfg.get("ptc", {}))
    flat_dir, dark_dir = _p(cfg["flat_dir"]), _p(cfg["dark_dir"])
    rep = ptc.analyze(flat_dir, dark_dir, params)
    rep["flat_dir"], rep["dark_dir"] = cfg["flat_dir"], cfg["dark_dir"]

    source = {
        "flat_dir": cfg["flat_dir"],
        "dark_dir": cfg["dark_dir"],
        "flat_ptc_json_sha256": hashlib.sha256((flat_dir / "ptc.json").read_bytes()).hexdigest(),
        "dark_ptc_json_sha256": hashlib.sha256((dark_dir / "ptc.json").read_bytes()).hexdigest(),
        "analysis": "experiments/p1_ptc.py",
        "results_dir": str(exp.out_dir.relative_to(cli.REPO_ROOT)),
        "git": runner.git_info(),
    }
    rep["noise_models"] = {}
    for label, g in rep["gains"].items():
        model = noise_model.single_noise_model(g, rep["full_scale_dn"], cfg["model_method"], source)
        path = _p(cfg["noise_model_out"].format(label=label))
        noise_model.write(model, path)
        rep["noise_models"][label] = {"path": str(path.relative_to(cli.REPO_ROOT)), "model": model}

    acc = {k: g[cfg["model_method"]]["r2"] >= cfg["acceptance_r2"] for k, g in rep["gains"].items()}
    out = exp.out_dir
    (out / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    write_csv(rep["points"], out / "points.csv")
    write_csv(
        [
            {"gain_label": k, "exposure_us": s["exposure_us"], **c}
            for k, d in rep["dark"].items()
            for s in d["sets"]
            for c in s["channels"]
        ],
        out / "dark.csv",
    )
    for label in rep["gains"]:
        plot_ptc(rep, label, out / f"ptc_{label}.png")
    plot_linearity(rep, out / "linearity.png")
    md = summary_md(rep, cfg, acc)
    (out / "sensor_summary.md").write_text(md, encoding="utf-8")
    copy = _p(cfg["summary_copy"])
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out / "sensor_summary.md", copy)
    print(md)
    print(f"→ {out}")
    return 0 if all(acc.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
