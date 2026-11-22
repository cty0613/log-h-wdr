"""Phase 3: H-Log 곡선 파라미터 생성 + 곡선 6종의 양자화 지표 R(x) + DR 대비 필요 비트 + LUT 오차.

사용: python experiments/p3_curves.py configs/p3_curves.yaml
출력: configs/curves/*.json (C++ 곡선 입력), docs/fixed_point.md 오차 표,
      results/p3_curves/<timestamp>/{rx_<scenario>.png, rx_metrics.csv, dr_bits.png/csv,
      lut_errors.csv, summary.json, conclusion.md} + results/p3/conclusion.md
R(x)는 C++ curve_eval float 출력 V(x)의 한쪽 미분으로 계산한다 (CLAUDE.md 6.4 정의).
수락 기준: LUT vs float ≤ 1 LSB (모든 H-Log) → 아니면 종료 코드 1.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import itertools

import matplotlib.pyplot as plt
import numpy as np

from cctvlog import cli, runner
from cctvlog.calib import curve_params as cp
from cctvlog.sensor import merged_noise as mn
from cctvlog.sensor import noise_model

# dataviz 기본 팔레트 (light), 고정 순서
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID = "#0b0b0b", "#52514e", "#e4e3df"
STYLE = {  # 곡선별 색·선 (모든 그림에서 같은 곡선 = 같은 색)
    "rec709": (SERIES[4], "-"),
    "hlg": (SERIES[1], "-"),
    "slog3": (SERIES[2], "-"),
    "hlog_single": (INK2, "--"),
    "hlog_R16": (SERIES[0], "-"),
    "hlog_dcg": (SERIES[6], "-"),
}
LABEL = {
    "rec709": "Rec.709",
    "hlg": "HLG",
    "slog3": "S-Log3",
    "hlog_single": "H-Log single model (x_max 16)",
    "hlog_R16": "H-Log bracket R=16",
    "hlog_dcg": "H-Log DCG",
}


def _p(path: str) -> Path:
    q = Path(path)
    return q if q.is_absolute() else cli.REPO_ROOT / q


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def style(ax: plt.Axes) -> None:
    ax.grid(True, which="both", color=GRID, lw=0.7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=INK2)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- 곡선 JSON


def build_curves(cfg: dict[str, Any], noises: dict[str, Any], paths: dict[str, Path]) -> dict:
    out_dir = _p(cfg["curves_dir"])
    curves: dict[str, dict[str, Any]] = {}
    for name, spec in cfg["curves"].items():
        if spec["kind"] == "comparison":
            c = cp.comparison_params(spec["type"], cfg["comparison_x_max"], name)
        else:
            key = spec["noise"]
            src = {"noise": key}
            if key in paths:
                src.update({"noise_json": cfg["noise"][key], "noise_sha256": _sha(paths[key])})
            c = cp.hlog_params(noises[key], name, src)
        cp.write(c, out_dir / f"{name}.json")
        curves[name] = json.loads((out_dir / f"{name}.json").read_text())
    return curves


# ---------------------------------------------------------------- R(x) (C++ 경로)


def eval_curve(name: str, cfg: dict[str, Any], out: Path, lut: bool) -> Path:
    d = out / "curve_eval" / name
    cli.run_cpp(
        "curve_eval",
        config=_p(cfg["curves_dir"]) / f"{name}.json",
        out=d,
        bits=cfg["bits"],
        ramp_n=cfg["ramp_n"],
        ramp_min=cfg["ramp_min"],
        lut=lut,
    )
    return d


def step_from_levels(levels: np.ndarray, codes: np.ndarray) -> np.ndarray:
    """코드 c의 양자화 간격 Δx = (level[c+1] − level[c−1]) / 2. 한쪽이 0 폭(포화·S-Log3 아래)이면
    다른 쪽 간격을 쓴다."""
    lv = levels.astype(np.float64)
    c = codes.astype(np.int64)
    up = lv[np.minimum(c + 1, lv.size - 1)] - lv[c]
    dn = lv[c] - lv[np.maximum(c - 1, 0)]
    both = (up > 0) & (dn > 0)
    return np.where(both, (up + dn) / 2.0, np.maximum(up, dn))


def rx_for(d: Path, bits: int, noise: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """R(x) = Δx/σ(x), Δx = 1 / ((2^bits − 1)·C'(x)) (CLAUDE.md 6.4).
    C'(x)는 C++ float 경로 출력 V(x)(로그 램프)의 수치 미분."""
    x = np.load(d / "x.npy").ravel().astype(np.float64)
    v = np.load(d / "v.npy").ravel().astype(np.float64)
    # 한쪽 미분 두 개 중 큰 기울기: 매끄러운 곳에서는 같고, 구간 경계(기울기 꺾임)에서는 경계를
    # 넘지 않는 쪽이 그 점의 실제 C'(x)다 (중앙 미분은 경계 점 하나에서 위아래 기울기를 섞는다).
    fwd = np.empty_like(v)
    bwd = np.empty_like(v)
    fwd[:-1] = np.diff(v) / np.diff(x)
    fwd[-1] = fwd[-2]
    bwd[1:] = fwd[:-1]
    bwd[0] = fwd[0]
    dv = np.maximum(fwd, bwd)
    dx = 1.0 / ((2**bits - 1) * np.maximum(dv, 1e-300))
    return x, dx / np.sqrt(mn.model_variance(noise, x))


def rx_codebin(d: Path, bits: int, noise: dict[str, Any]) -> np.ndarray:
    """보조: 복호 레벨 간격 (level[c+1] − level[c−1]) / 2로 잰 R. 구간 경계(노이즈 점프)를
    걸친 코드 하나에서 위아래 간격이 섞여 커진다."""
    x = np.load(d / "x.npy").ravel().astype(np.float64)
    codes = np.load(d / f"codes_{bits}.npy").ravel()
    levels = np.load(d / f"levels_{bits}.npy").ravel()
    return step_from_levels(levels, codes) / np.sqrt(mn.model_variance(noise, x))


def exact_variance(noise: dict[str, Any], x: np.ndarray) -> np.ndarray:
    """경계 띠를 포함한 실제 합성 분산 (merged_noise.band_variance / dcg_band_variance)."""
    asm = noise.get("assumptions", {})
    if noise["kind"] == "bracket":
        s0 = asm["single"]
        return mn.band_variance(
            x, s0["a"], s0["b"], asm["exposure_ratios"], asm["tau"], asm["band_ratio"]
        )
    if noise["kind"] == "dcg":
        return mn.dcg_band_variance(x, asm)
    return mn.model_variance(noise, x)


def metrics(x: np.ndarray, r: np.ndarray, x_lo: float) -> dict[str, float]:
    m = x >= x_lo
    xm, rm = x[m], r[m]
    over = rm > 1.0
    # R > 1인 구간 길이 (stop): 로그 램프 점 간격으로 적분
    lx = np.log2(xm)
    dl = np.gradient(lx)
    rmax = float(rm.max())
    return {
        "max_R": rmax,
        "x_at_max_R": float(xm[int(np.argmax(rm))]),
        "stops_R_gt_1": float(dl[over].sum()),
        "worst_snr_loss_db": float(10 * np.log10(1 + rmax**2 / 12)),
        "median_R": float(np.median(rm)),
    }


def plot_rx(sc: dict[str, Any], cfg: dict[str, Any], res: dict, x_lo: float, out: Path) -> None:
    bits = cfg["bits"]
    fig, axes = plt.subplots(1, len(bits), figsize=(5.2 * len(bits), 4.6), sharey=True)
    for ax, b in zip(axes, bits, strict=True):
        style(ax)
        for name in cfg["six"]:
            x, r = res[name][b]
            x, r = x[x > 0], r[x > 0]
            color, ls = STYLE[name]
            lw = 2.4 if name == sc["matched"] else 1.6
            ax.plot(
                np.log2(x),
                r,
                color=color,
                ls=ls,
                lw=lw,
                label=LABEL[name],
                zorder=3 if name == "hlog_single" else 2,
            )
        ax.axhline(1.0, color=INK, lw=1)
        ax.axvspan(np.log2(1e-5), np.log2(x_lo), color=GRID, alpha=0.6, lw=0)
        ax.set_yscale("log")
        ax.set_ylim(1e-3, 200)
        ax.set_xlim(np.log2(1e-4), 4.2)
        ax.set_title(f"{b} bit", color=INK)
        ax.set_xlabel("x [stop] (0 = long-exposure saturation)", color=INK)
    axes[0].set_ylabel("R(x) = quantization step / noise σ", color=INK)
    axes[-1].legend(fontsize=8, frameon=False, loc="upper right")
    fig.suptitle(
        f"Quantization metric R(x) on {sc['label']} (gray: SNR < 0 dB, not evaluated)", color=INK
    )
    fig.tight_layout()
    fig.savefig(out / f"rx_{sc['name']}.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------- H2: DR 대비 비트


def h2_rows(cfg: dict[str, Any], single: dict[str, Any]) -> list[dict[str, Any]]:
    a, b = single["segments"][0]["a"], single["segments"][0]["b"]
    rows = []
    for k in range(cfg["h2"]["k_max"] + 1):
        R = 2.0 ** (k / 2)
        cases = {"bracket_2frame": [1.0, R], "bracket_3frame": [1.0, math.sqrt(R), R]}
        for kind, ratios in cases.items():
            if R == 1.0:
                m = single
            elif len(set(ratios)) < len(ratios) or any(
                r2 / r1 < 1 / 0.9 for r1, r2 in itertools.pairwise(ratios)
            ):
                continue  # 노출비가 1/τ보다 작으면 다음 프레임 구간이 비어 의미가 없다
            else:
                m = mn.bracket_model(single, ratios)
            n = mn.noise_integral(m)
            rows.append(
                {
                    "kind": kind,
                    "R": R,
                    "dr_stops": mn.dr_stops(m),
                    "N": n,
                    "bits": math.ceil(math.log2(n)),
                }
            )
        ext = cp.single_extended(single, R)
        n = mn.noise_integral(ext)
        rows.append(
            {
                "kind": "single_model_extended",
                "R": R,
                "dr_stops": mn.dr_stops(ext),
                "N": n,
                "bits": math.ceil(math.log2(n)),
            }
        )
    p = {
        k: v
        for k, v in runner.load_config(_p(cfg["h2"]["dcg_config"])).items()
        if not k.startswith("_")
    }
    for g in cfg["h2"]["dcg_g"]:
        m = mn.dcg_model(
            dict(p, conversion_gain_ratio=g, hcg_full_well_e=None, hcg_read_noise_e=None)
        )
        n = mn.noise_integral(m)
        rows.append(
            {
                "kind": "dcg",
                "R": g,
                "dr_stops": mn.dr_stops(m),
                "N": n,
                "bits": math.ceil(math.log2(n)),
            }
        )
    _ = (a, b)
    return rows


def plot_h2(rows: list[dict[str, Any]], out: Path) -> None:
    fig, (ax, axb) = plt.subplots(
        2, 1, figsize=(8.5, 7.2), sharex=True, gridspec_kw={"height_ratios": [1.6, 1]}
    )
    style(ax)
    style(axb)
    spec = [
        ("bracket_2frame", "bracket, 2 frames (1:R)", SERIES[0], "o-"),
        ("bracket_3frame", "bracket, 3 frames (1:√R:R)", SERIES[1], "s-"),
        ("single_model_extended", "single-exposure model, x_max = R (merge ignored)", INK2, "^--"),
        ("dcg", "DCG g = 2, 4, 8", SERIES[6], "D"),
    ]
    for kind, label, color, mk in spec:
        rr = [r for r in rows if r["kind"] == kind]
        dr = [r["dr_stops"] for r in rr]
        ax.plot(dr, [r["N"] for r in rr], mk, color=color, ms=5, lw=1.8, label=label)
        axb.step(
            dr,
            [r["bits"] for r in rr],
            where="mid",
            color=color,
            ls="--" if "single" in kind else "-",
            lw=1.8,
        ) if kind != "dcg" else axb.plot(dr, [r["bits"] for r in rr], "D", color=color, ms=6)
    for n_lim, txt in ((512, "9 bit"), (1024, "10 bit"), (2048, "11 bit")):
        ax.axhline(n_lim, color=GRID, lw=1.2)
        ax.text(16.75, n_lim, txt, color=INK2, fontsize=8, va="center")
    ax.set_yscale("log", base=2)
    ax.set_ylabel("N = ∫dx/σ  (codes needed at R(x) = 1)", color=INK)
    ax.legend(fontsize=8.5, frameon=False, loc="upper left")
    ax.set_title("H2: required bits vs dynamic range (gain 1.1228 noise model)", color=INK)
    axb.set_ylabel("required bits ⌈log2 N⌉", color=INK)
    axb.set_xlabel("dynamic range [stop]", color=INK)
    axb.set_yticks([8, 9, 10, 11, 12])
    fig.tight_layout()
    fig.savefig(out / "dr_bits.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------- fixed_point.md


def update_fixed_point_md(rows: list[dict[str, Any]], results_dir: str) -> None:
    path = cli.REPO_ROOT / "docs" / "fixed_point.md"
    text = path.read_text(encoding="utf-8")
    table = [
        "| 곡선 | x_max | N | 인덱스 한 칸 @x=0 [LSB] | 검사 점 | 최대 오차 [LSB] | "
        "0 / 1 / 2+ LSB | 단조 | 역방향 최대 상대오차 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        h = r["hist"]
        table.append(
            f"| {r['curve']} | {r['x_max']:g} | {r['N']:.0f} | {r['index_step_lsb_at_0']:.3f} | "
            f"{r['samples']:,} | {r['max_abs_err_lsb']} | {h[0]:,} / {h[1]:,} / {h[2] + h[3]:,} | "
            f"{'예' if r['monotonic'] else '아니오'} | {r['inverse_max_rel_err']:.1e} |"
        )
    block = (
        "<!-- p3_curves:begin (experiments/p3_curves.py가 갱신) -->\n"
        + "\n".join(table)
        + "\n\n검사 점: 모든 정방향 인덱스 점 i와 i + {0.25, 0.4999, 0.5001, 0.75} "
        "(인덱스 반올림 경계 양쪽 포함).\n"
        f"오차 = |LUT 코드 − round_half_up(C(x)·1023)|. 결과: `{results_dir}`\n"
        "<!-- p3_curves:end -->"
    )
    if "<!-- p3_curves:begin" in text:
        text = re.sub(r"<!-- p3_curves:begin.*?<!-- p3_curves:end -->", block, text, flags=re.S)
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    exp = runner.start_experiment(argv[0])
    cfg, out = exp.config, exp.out_dir
    paths = {k: _p(v) for k, v in cfg["noise"].items()}
    noises = {k: noise_model.load(p) for k, p in paths.items()}
    noises["single_ext16"] = cp.single_extended(noises["single"], cfg["comparison_x_max"])
    curves = build_curves(cfg, noises, paths)

    # C++ 평가
    dirs = {n: eval_curve(n, cfg, out, lut=n in cfg["lut_curves"]) for n in cfg["curves"]}

    summary: dict[str, Any] = {"scenarios": {}, "curves": {}}
    metric_rows = []
    for sc in cfg["scenarios"]:
        data = noises[sc["data"]]
        x_lo = math.sqrt(data["segments"][0]["b"])
        res: dict[str, dict[int, tuple[np.ndarray, np.ndarray]]] = {}
        scm: dict[str, Any] = {}
        for name in cfg["six"]:
            res[name] = {}
            for b in cfg["bits"]:
                x, r = rx_for(dirs[name], b, data)
                res[name][b] = (x, r)
                m = metrics(x, r, x_lo)
                rb = rx_codebin(dirs[name], b, data)
                m["max_R_codebin"] = float(rb[x >= x_lo].max())
                rbe = rb * np.sqrt(mn.model_variance(data, x) / exact_variance(data, x))
                m["max_R_codebin_band"] = float(rbe[x >= x_lo].max())
                scm.setdefault(str(b), {})[name] = m
                metric_rows.append({"scenario": sc["name"], "bits": b, "curve": name, **m})
        verdict = {}
        for b in cfg["bits"]:
            mb = scm[str(b)]
            others = [
                n
                for n in cfg["six"]
                if n.startswith(("rec709", "hlg", "slog3")) or n == "hlog_single"
            ]
            best_other = min(others, key=lambda n: mb[n]["max_R"])
            verdict[str(b)] = {
                "matched": sc["matched"],
                "matched_max_R": mb[sc["matched"]]["max_R"],
                "best_comparison": best_other,
                "best_comparison_max_R": mb[best_other]["max_R"],
                "ratio": mb[sc["matched"]]["max_R"] / mb[best_other]["max_R"],
                "h1_holds": mb[sc["matched"]]["max_R"] < mb[best_other]["max_R"],
            }
        summary["scenarios"][sc["name"]] = {"x_lo": x_lo, "metrics": scm, "h1": verdict}
        plot_rx(sc, cfg, res, x_lo, out)
    write_csv(out / "rx_metrics.csv", metric_rows)

    # LUT 오차 표
    lut_rows = []
    for name in cfg["lut_curves"]:
        run = json.loads((dirs[name] / "run.json").read_text())
        c = curves[name]
        s0 = math.sqrt(c["segments"][0]["b"])
        lut_rows.append(
            {
                "curve": name,
                "x_max": c["x_max"],
                "N": c["N"],
                "index_step_lsb_at_0": c["x_max"] / 65535 * c["k"] / s0 * 1023,
                "samples": run["lut"]["samples"],
                "max_abs_err_lsb": run["lut"]["max_abs_err_lsb"],
                "worst_x": run["lut"]["worst_x"],
                "hist": run["lut"]["hist_err_0_1_2_3plus"],
                "monotonic": run["lut"]["monotonic"],
                "inverse_max_rel_err": run["lut"]["inverse_max_rel_err_vs_float"],
            }
        )
    write_csv(
        out / "lut_errors.csv",
        [
            {k: v for k, v in r.items() if k != "hist"}
            | {"err0": r["hist"][0], "err1": r["hist"][1], "err2plus": r["hist"][2] + r["hist"][3]}
            for r in lut_rows
        ],
    )
    update_fixed_point_md(lut_rows, str(out.relative_to(cli.REPO_ROOT)))
    lut_ok = all(r["max_abs_err_lsb"] <= 1 and r["monotonic"] for r in lut_rows)
    summary["lut"] = lut_rows
    summary["lut_pass"] = lut_ok

    # H2
    rows = h2_rows(cfg, noises["single"])
    write_csv(out / "dr_bits.csv", rows)
    plot_h2(rows, out)
    summary["h2"] = rows
    summary["curves"] = {
        n: {k: c.get(k) for k in ("type", "x_max", "N", "required_bits", "params_hash")}
        for n, c in curves.items()
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    md = conclusion_md(summary, cfg, out)
    (out / "conclusion.md").write_text(md, encoding="utf-8")
    copy = _p(cfg["conclusion_copy"])
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(md, encoding="utf-8")
    print(md)
    print(f"→ {out}")
    return 0 if lut_ok else 1


def h1_notes(s: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    """H1 해석과 주의점 (수치는 summary에서)."""
    br = s["scenarios"][cfg["scenarios"][0]["name"]]["metrics"]
    dc = s["scenarios"][cfg["scenarios"][1]["name"]]["metrics"]
    m8, m10 = br["8"], br["10"]
    return [
        "### 해석과 주의",
        "",
        f"- **8 bit에서는 어느 곡선도 R(x) ≤ 1이 아니다.** 맞춘 H-Log는 손실을 전 구간에 고르게 "
        f"나눠(최대 {m8['hlog_R16']['max_R']:.2f}, 전 구간 상수) 최대값이 가장 작지만, HLG"
        f"({m8['hlg']['max_R']:.2f})는 R > 1 구간이 {m8['hlg']['stops_R_gt_1']:.1f} stop으로 "
        "조금 짧다. 8 bit 대조군(Phase 5)에서 실제 영향을 본다.",
        f"- **DCG 데이터에서는 맞춘 H-Log와 단일 모델 H-Log가 사실상 같다** (10 bit "
        f"{dc['10']['hlog_dcg']['max_R']:.3f} vs {dc['10']['hlog_single']['max_R']:.3f}). DCG는 두 "
        "판독이 "
        "샷 노이즈를 공유해 노이즈가 거의 √x 모양이고, 단일 노출 모델 곡선도 √x 모양이기 때문이다. "
        "노이즈 매칭의 이득은 브래킷처럼 노이즈가 점프하는 합성에서 생긴다.",
        f"- **합성 노이즈를 무시하면 비트가 모자란다.** 단일 모델 H-Log는 브래킷 데이터 10 bit에서 "
        f"R ≈ {m10['hlog_single']['max_R']:.2f}가 {m10['hlog_single']['stops_R_gt_1']:.1f} stop에 "
        "걸친다 "
        "(긴 노출 구간에 코드가 모자라고 짧은 노출 구간에 남는다).",
        "- **보조 지표 (경계를 걸친 코드 하나 포함)**: 노이즈를 점프 모델로 보면 맞춘 H-Log가 10 "
        "bit "
        f"{m10['hlog_R16']['max_R_codebin']:.3f}로 HLG({m10['hlg']['max_R_codebin']:.3f})와 "
        "비슷하다. "
        "곡선 기울기가 꺾이는 점(x = 0.9, 3.6)의 코드 하나가 위쪽 넓은 간격까지 덮기 때문이다. "
        "실제 합성 데이터는 경계 바로 아래가 띠라 노이즈가 이미 커져 있어, 띠 포함 노이즈로 보면 "
        f"{m10['hlog_R16']['max_R_codebin_band']:.3f}다 (HLG "
        f"{m10['hlg']['max_R_codebin_band']:.3f}).",
        "- R(x)는 스펙 6.4의 미분 정의로 계산했다. 처음 계획(복호 레벨의 중앙 차분, decisions.md "
        "결정 3)은 "
        "경계 점에서 위아래 간격을 섞어 맞춘 곡선의 상수 R을 1.5배로 보이게 해서 바꿨다. 경계 "
        "효과는 "
        "위 보조 지표로 따로 남겼다.",
        "- Rec.709 곡선의 −1.8 stop 근처 뾰족한 점은 BT.709 상수 반올림 때문에 L = 0.018에서 식이 "
        "0.00025 끊기는 곳이다 (곡선의 성질).",
        "",
    ]


def conclusion_md(s: dict[str, Any], cfg: dict[str, Any], out: Path) -> str:
    L = [
        "# Phase 3 결론: HDR 대응 H-Log",
        "",
        f"결과 폴더: `{out.relative_to(cli.REPO_ROOT)}`. 규칙: docs/decisions.md 2026-11-22 "
        "(Phase 3 착수). R(x) = Δx/σ(x), Δx = 1/((2^bits−1)·C'(x)), C'는 C++ float 출력의 "
        "한쪽 미분, σ는 데이터 노이즈.",
        "",
    ]
    # H1 한 줄
    sc0 = cfg["scenarios"][0]["name"]
    v10 = s["scenarios"][sc0]["h1"]["10"]
    holds_all = all(v["h1_holds"] for sc in s["scenarios"].values() for v in sc["h1"].values())
    L += [
        "## H1",
        "",
        f"**{'성립' if holds_all else '부분 성립'}.** 브래킷 R=16 데이터, 10 bit에서 데이터에 맞춘 "
        f"H-Log의 최대 R(x)는 {v10['matched_max_R']:.3f}로, 가장 좋은 비교 곡선 "
        f"{LABEL[v10['best_comparison']]}({v10['best_comparison_max_R']:.3f})의 "
        f"{v10['ratio']:.2f}배다.",
        "",
    ]
    for scn, sc in s["scenarios"].items():
        L += [
            f"### {scn} 데이터 (평가 범위 x ≥ {sc['x_lo']:.2e})",
            "",
            "| bit | 곡선 | 최대 R(x) | R > 1 구간 [stop] | 최악 SNR 손실 [dB] | 중앙값 R | "
            "보조: 경계 걸친 코드 포함 최대 R (점프 노이즈 / 띠 포함 실제 노이즈) |",
            "|---|---|---|---|---|---|---|",
        ]
        for b, mb in sc["metrics"].items():
            for name in cfg["six"]:
                m = mb[name]
                mark = " **(맞춤)**" if name == sc["h1"][b]["matched"] else ""
                L.append(
                    f"| {b} | {LABEL[name]}{mark} | {m['max_R']:.3f} | "
                    f"{m['stops_R_gt_1']:.2f} | {m['worst_snr_loss_db']:.2f} | "
                    f"{m['median_R']:.3f} | {m['max_R_codebin']:.3f} / "
                    f"{m['max_R_codebin_band']:.3f} |"
                )
        L.append("")
        L += ["| bit | H1 (맞춤 H-Log < 비교 곡선 전부) | 맞춤 / 최선 비교 |", "|---|---|---|"]
        for b, v in sc["h1"].items():
            tie = " (차이 < 2%, 사실상 동점)" if abs(v["ratio"] - 1) < 0.02 else ""
            L.append(
                f"| {b} | {'성립' if v['h1_holds'] else '불성립'}{tie} | "
                f"{v['matched_max_R']:.3f} / {v['best_comparison_max_R']:.3f} "
                f"({LABEL[v['best_comparison']]}) |"
            )
        L.append("")
    L += h1_notes(s, cfg)
    # H2
    h2 = s["h2"]

    def pick(kind: str) -> list[dict[str, Any]]:
        return [r for r in h2 if r["kind"] == kind]

    two, three, ext = pick("bracket_2frame"), pick("bracket_3frame"), pick("single_model_extended")
    L += [
        "## H2",
        "",
        f"**필요 비트는 DR보다 합성에 쓴 프레임 수로 정해진다.** 2장 브래킷은 R = 1~64 "
        f"(DR {two[0]['dr_stops']:.1f}~{two[-1]['dr_stops']:.1f} stop)에서 N이 "
        f"{two[0]['N']:.0f}~{max(r['N'] for r in two):.0f}로, 필요 비트가 "
        f"{min(r['bits'] for r in two)}~{max(r['bits'] for r in two)} bit다. "
        f"3장은 최대 N {max(r['N'] for r in three):.0f} → {max(r['bits'] for r in three)} bit. "
        f"합성 노이즈를 무시한 단일 모델 곡선은 R = 64에서 N {ext[-1]['N']:.0f} → "
        f"{ext[-1]['bits']} bit가 필요하다. → 브래킷 R=16(3장)은 10 bit에서 R(x) ≤ "
        f"{s['scenarios'][sc0]['metrics']['10']['hlog_R16']['max_R']:.2f}로 충분하다.",
        "",
        "| 종류 | R | DR [stop] | N | 비트 |",
        "|---|---|---|---|---|",
    ]
    for r in h2:
        if r["kind"] == "dcg" or abs(math.log2(r["R"]) - round(math.log2(r["R"]))) < 1e-9:
            L.append(
                f"| {r['kind']} | {r['R']:.3g} | {r['dr_stops']:.2f} | {r['N']:.0f} | {r['bits']} |"
            )
    L += [
        "",
        "## 고정소수점 LUT (수락 기준 ≤ 1 LSB)",
        "",
        "| 곡선 | 최대 오차 [LSB] | 2 LSB 이상 | 단조 |",
        "|---|---|---|---|",
    ]
    for r in s["lut"]:
        L.append(
            f"| {r['curve']} | {r['max_abs_err_lsb']} | {r['hist'][2] + r['hist'][3]} | "
            f"{r['monotonic']} |"
        )
    L += [
        "",
        f"LUT 판정: **{'PASS' if s['lut_pass'] else 'FAIL'}**",
        "",
        "그림: rx_bracket_R16.png, rx_dcg.png, dr_bits.png",
        "",
    ]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
