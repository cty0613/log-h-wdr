"""Phase 1: 센서 HDR 모드의 응답 선형성·경계 위치·포화·노이즈 (비교군 특성).

사용: python experiments/p1_sensor_hdr.py configs/p1_sensor_hdr.yaml
출력: results/p1_sensor_hdr/<timestamp>/{report.json, steps.csv, response.png, snr.png,
      sensor_hdr_summary.md} + results/p1/sensor_hdr_summary.md
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
from cctvlog.sensor import sensor_hdr


def _p(path: str) -> Path:
    q = Path(path)
    return q if q.is_absolute() else cli.REPO_ROOT / q


def plot_response(rep: dict[str, Any], out: Path) -> None:
    st = rep["steps"]
    fig, (ax, axs) = plt.subplots(
        2, 1, figsize=(8, 7.5), sharex=True, gridspec_kw={"height_ratios": [3, 1.4]}
    )
    for g in sorted({s["analogue_gain"] for s in st}):
        q = [s for s in st if s["analogue_gain"] == g]
        ax.loglog(
            [s["E_us"] for s in q],
            [s["level"] for s in q],
            "o",
            ms=4,
            label=f"sensor HDR, gain {g:.4g}",
        )
    t = rep["normal_mode_t_sat_us"]
    ee = np.logspace(np.log10(min(s["E_us"] for s in st)), np.log10(t), 50)
    ax.loglog(ee, ee / t, "k--", lw=1, label=f"normal mode (linear, sat at {t / 1000:.1f} ms)")
    for seg in rep["segments"]["segments"]:
        x = np.linspace(*seg["E_us"], 30)
        ax.loglog(x, seg["slope_per_us"] * x + seg["intercept"], "-", color="tab:red", lw=1.2)
    for seg in rep["segments"]["segments"][1:]:
        ax.axvline(seg["E_us"][0], color="tab:red", ls=":", lw=0.8)
    ax.axhline(1.0, color="gray", lw=0.8)
    ax.set_ylabel("mean level (G1, normalized)")
    ax.set_title("IMX708 sensor HDR mode: response vs effective exposure")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=8)
    # 국소 기울기 / 선형 구간 기울기
    q = [s for s in st if s["level"] < rep["params"]["sat_level"]]
    e = np.array([s["E_us"] for s in q])
    m = np.array([s["level"] for s in q])
    o = np.argsort(e)
    e, m = e[o], m[o]
    keep = np.r_[True, np.diff(e) > 0]
    e, m = e[keep], m[keep]
    rel = np.diff(m) / np.diff(e) / rep["linear"]["slope_per_us"]
    axs.loglog(np.sqrt(e[1:] * e[:-1]), np.maximum(rel, 1e-4), "o-", ms=3)
    axs.set_ylabel("local slope / linear slope")
    axs.set_xlabel("effective exposure E = (gain / min gain) x exposure [µs]")
    axs.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def plot_snr(rep: dict[str, Any], noise_model: dict[str, Any] | None, out: Path) -> None:
    st = rep["steps"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)
    for ax, key, title in (
        (axes[0], "snr_in_db", "input-referred SNR (noise / local response slope)"),
        (axes[1], "snr_db", "output SNR (mean / σ of companded output)"),
    ):
        for g in sorted({s["analogue_gain"] for s in st}):
            q = [s for s in st if s["analogue_gain"] == g and s.get(key) is not None]
            ax.semilogx(
                [s["E_us"] for s in q],
                [s[key] for s in q],
                "o-",
                ms=3,
                label=f"sensor HDR, gain {g:.4g}",
            )
        if noise_model:
            seg = noise_model["segments"][0]
            t = rep["normal_mode_t_sat_us"]
            ee = np.logspace(np.log10(min(s["E_us"] for s in st)), np.log10(t), 60)
            x = ee / t
            ax.semilogx(
                ee,
                20 * np.log10(x / np.sqrt(seg["a"] * x + seg["b"])),
                "k--",
                lw=1,
                label="normal mode model (noise_single_g1)",
            )
        for sg in rep["segments"]["segments"][1:]:
            ax.axvline(sg["E_us"][0], color="tab:red", ls=":", lw=0.8)
        ax.set_xlabel("effective exposure [µs]")
        ax.set_title(title, fontsize=10)
        ax.grid(True, which="both", alpha=0.3)
    axes[0].set_ylabel("SNR [dB] (G1)")
    axes[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def summary_md(rep: dict[str, Any], cfg: dict[str, Any]) -> str:
    lin, sat = rep["linear"], rep.get("saturation")
    L = ["# Phase 1 센서 HDR 모드 특성 (IMX708 wide, 2304×1296, 30 fps)", ""]
    L += [
        f"- 스윕: {', '.join(f'`{r}`' for r in cfg['runs'])}",
        f"- 유효 노출 E = (게인 / {rep['reference_gain']:.4f}) × 노출, "
        f"채널 {rep['params']['channel']},"
        f" ROI {rep['params']['roi']} px, 블랙 {rep['black_dn']} DN",
        f"- 일반 모드 포화 노출(같은 광원, 최소 게인): {rep['normal_mode_t_sat_us'] / 1000:.2f} ms",
        "",
    ]
    fs = rep["full_scale_dn"]
    L += [
        "## 선형성과 첫 경계",
        "",
        f"- 기준: 일반 모드 감도 직선(출력 = E / {rep['normal_mode_t_sat_us'] / 1000:.2f} ms). "
        f"이 아래로 {lin['tol'] * 100:.0f}% 넘게 벗어난 뒤 돌아오지 않는 첫 점을 경계로 본다.",
        f"- 선형 구간: E ≤ {lin['last_linear_E_us'] / 1000:.2f} ms (출력 ≤ "
        f"{lin['last_linear_level']:.4f} = {lin['last_linear_level'] * fs:.0f} DN), "
        f"구간 안 최대 편차 {lin['max_abs_dev_in_linear'] * 100:.1f}%",
        f"- 선형 구간 직선 기울기 / 일반 모드: {lin['slope_vs_normal']:.4f}, "
        f"절편 {lin['intercept'] * fs:+.2f} DN",
    ]
    if lin["first_nonlinear_E_us"] is not None:
        L.append(
            f"- **첫 경계**: E {lin['last_linear_E_us'] / 1000:.2f} ~ "
            f"{lin['first_nonlinear_E_us'] / 1000:.2f} ms, 출력 "
            f"{lin['last_linear_level']:.4f} ~ {lin['first_nonlinear_level']:.4f} "
            f"(다음 점 편차 {lin['first_nonlinear_dev'] * 100:+.1f}%)"
        )
    sv = rep.get("snr_vs_normal_linear")
    if sv and sv["n"]:
        L.append(
            f"- 같은 출력 레벨에서 SNR − 일반 모드 모델 SNR: "
            f"중앙값 {sv['median_delta_db']:+.2f} dB "
            f"(분산 {sv['var_ratio_median']:.2f}배, 범위 {sv['min_delta_db']:+.2f} ~ "
            f"{sv['max_delta_db']:+.2f} dB, {sv['n']}점)"
        )
    L.append("")
    L += [
        "## 구간 분할 (구간별 직선, BIC로 구간 수 선택)",
        "",
        f"구간 수 {rep['segments']['k']}",
        "",
        "| 구간 | E 범위 [ms] | 출력 범위 | 기울기 / 선형 구간 기울기 |",
        "|---|---|---|---|",
    ]
    for i, s in enumerate(rep["segments"]["segments"]):
        L.append(
            f"| {i + 1} | {s['E_us'][0] / 1000:.2f} ~ {s['E_us'][1] / 1000:.2f} | "
            f"{s['level'][0]:.4f} ~ {s['level'][1]:.4f} | "
            f"{s['slope_per_us'] / lin['slope_per_us']:.4f} |"
        )
    if sat:
        L += [
            "",
            "## 포화와 DR 확장",
            "",
            f"- 출력이 {rep['params']['sat_level']} 이상이 되는 첫 E: {sat['E_us'] / 1000:.1f} ms "
            f"(직전 {sat['last_below_E_us'] / 1000:.1f} ms에서 {sat['last_below_level']:.4f})",
            f"- 일반 모드 대비 확장: **{sat['extension_ratio']:.1f}배 = "
            f"{sat['extension_stops']:.2f} stop**"
            " (밝은 쪽 확장만 센 값. 어두운 쪽은 선형 구간 노이즈가 일반 모드보다 크다"
            " — 위 선형성 절)",
            f"- 최대 RAW 코드: {rep['max_raw_code']}",
        ]
    if rep["gain_overlap"]:
        L += ["", "## 게인 겹침 (E = 게인 × 노출 가정 검증)", ""]
        for k, v in rep["gain_overlap"].items():
            L.append(
                f"- 게인 {k}: {v['n']}점, E {v['E_us'][0] / 1000:.1f}~{v['E_us'][1] / 1000:.1f} ms,"
                f" 최대 차이 {v['max_abs_rel_diff'] * 100:.2f}%, 중앙값 "
                f"{v['median_abs_rel_diff'] * 100:.2f}%"
            )
    g0 = rep["reference_gain"]
    q = [x for x in rep["steps"] if x["analogue_gain"] == g0 and x.get("snr_in_db") is not None]
    if q:
        L += [
            "",
            f"## 입력 기준 SNR (게인 {g0:.4f})",
            "",
            "출력 노이즈를 구간 직선 기울기로 나눈 근사값이다. 전이 구간은 국소 기울기가 흔들려 "
            "오차가 크다. 출력 SNR은 압축 때문에 장면 SNR보다 크게 보인다.",
            "",
            "| E [ms] | 출력 | 구간 기울기 / 선형 | 입력 기준 SNR [dB] | 출력 SNR [dB] |",
            "|---|---|---|---|---|",
        ]
        for x in q:
            L.append(
                f"| {x['E_us'] / 1000:.2f} | {x['level']:.4f} | {x['slope_rel']:.4f} | "
                f"{x['snr_in_db']:.2f} | {x['snr_db']:.2f} |"
            )
    b = rep.get("boundary_snr")
    if b:
        L += [
            "",
            "## 첫 경계 전후 SNR",
            "",
            f"- 전: E {b['before']['E_us']:.0f} µs, 출력 {b['before']['level']:.4f}, "
            f"SNR {b['before']['snr_db']:.2f} dB",
            f"- 후: E {b['after']['E_us']:.0f} µs, 출력 {b['after']['level']:.4f}, "
            f"SNR {b['after']['snr_db']:.2f} dB (Δ {b['delta_db']:+.2f} dB)",
        ]
    L += ["", "그림: response.png, snr.png", ""]
    return "\n".join(L)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    exp = runner.start_experiment(argv[0])
    cfg = exp.config
    plan = json.loads(_p(cfg["normal_plan"]).read_text())
    t_sat = plan["gains"]["g1"]["t_sat_us"]
    p = sensor_hdr.HdrParams(**cfg.get("hdr", {}))
    nm = json.loads(_p(cfg["normal_noise_model"]).read_text())
    rep = sensor_hdr.analyze(
        [_p(r) for r in cfg["runs"]], cfg["black_dn"], cfg["full_scale_dn"], t_sat, p, nm
    )
    rep["runs"] = cfg["runs"]
    out = exp.out_dir
    (out / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    keys = [
        "run",
        "step",
        "analogue_gain",
        "exposure_us_requested",
        "exposure_us",
        "E_us",
        "mean_dn",
        "level",
        "var_dn2",
        "snr_db",
        "sat_fraction",
        "max_raw",
    ]
    with (out / "steps.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rep["steps"])
    plot_response(rep, out / "response.png")
    plot_snr(rep, nm, out / "snr.png")
    md = summary_md(rep, cfg)
    (out / "sensor_hdr_summary.md").write_text(md, encoding="utf-8")
    copy = _p(cfg["summary_copy"])
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(out / "sensor_hdr_summary.md", copy)
    print(md)
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
