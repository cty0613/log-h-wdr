"""Phase 1 플랫필드 셋업 판정 (`capture/check_flatfield.py` 결과 분석).

PTC 본촬영 전에 광원·확산판 배치가 쓸 만한지 수치로 판정한다.

사용:
  python -m cctvlog.sensor.flatfield_check data/pi_runs/ffcheck_YYYYMMDD_HHMM \
      [--out results/p1/flatfield_check/NAME]

판정 항목:
  sweep      노출 대비 ROI 평균 선형성(R²), 게인별 포화 노출, PTC 밝기 단계 확보 가능성
  flicker    쌍 차분의 행 평균 분산 비(가로 줄무늬), 프레임 평균 변동(CV)
  stability  최소 게인 50% 신호에서 시간에 따른 밝기 변화
  uniformity 화면 격자 평균(중앙 대비), 중앙 ROI 안의 평탄도, 채널 비

임계값은 셋업 확인용 잠정 기준이다(본 측정의 수락 기준이 아님). 보고서에 값과 함께 적는다.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

# 잠정 판정 기준
SAT_LEVEL = 0.98  # 이 이상이면 포화로 보고 선형 피팅에서 제외
FIT_RANGE = (0.02, 0.8)
LINEARITY_R2_MIN = 0.999
BANDING_RATIO_WARN = 1.5  # 행 평균 분산 / (화소 분산 / 열 수). 무상관 노이즈면 ≈ 1
BANDING_RATIO_FAIL = 3.0
MEAN_CV_WARN = 0.002  # 프레임 평균 변동 0.2%
MEAN_CV_FAIL = 0.01
STABILITY_RANGE_WARN = 0.005  # 0.5%
STABILITY_RANGE_FAIL = 0.02
ROI_FLATNESS_WARN = 0.02  # 중앙 ROI 안 블록 평균의 최대 편차 2%
PTC_LOW_LEVEL = 0.01  # PTC 최저 단계 목표 신호(포화 대비)
GRID = (6, 8)  # 균일도 격자 (행, 열)
BAND_COLS = 512  # 줄무늬 분석에 쓰는 중앙 세로 띠 폭 (전체 높이 사용)
ROI = 512


@dataclass
class Frame:
    raw: np.ndarray
    meta: dict[str, Any]

    @property
    def black(self) -> float:
        b = self.meta.get("black_level")
        return float(b) if isinstance(b, (int, float)) else 64.0

    @property
    def white(self) -> float:
        w = self.meta.get("white_level")
        return float(w) if isinstance(w, (int, float)) else 1023.0

    def normalized(self) -> np.ndarray:
        return (self.raw.astype(np.float64) - self.black) / (self.white - self.black)


def load_frame(run_dir: Path, rel: str) -> Frame:
    p = run_dir / rel
    return Frame(np.load(p), json.loads(p.with_suffix(".json").read_text()))


def channel_labels(bayer_order: str) -> list[str]:
    """2x2 위치 [(0,0), (0,1), (1,0), (1,1)]의 색 이름. 초록 둘은 G1(행 0쪽), G2."""
    labels, g = [], 0
    for c in bayer_order.upper():
        if c == "G":
            g += 1
            labels.append(f"G{g}")
        else:
            labels.append(c)
    return labels


def planes(img: np.ndarray) -> list[np.ndarray]:
    return [img[dy::2, dx::2] for dy in (0, 1) for dx in (0, 1)]


def center_crop(img: np.ndarray, h: int, w: int) -> np.ndarray:
    """짝수 정렬 중앙 자르기 (Bayer 위상 유지)."""
    y0 = max(0, (img.shape[0] // 2 - h // 2) & ~1)
    x0 = max(0, (img.shape[1] // 2 - w // 2) & ~1)
    return img[y0 : y0 + h, x0 : x0 + w]


# ---------------------------------------------------------------- sweep


def linear_fit(t: np.ndarray, m: np.ndarray) -> dict[str, float]:
    """m = slope·t + intercept 최소제곱. R²와 최대 상대 잔차."""
    A = np.vstack([t, np.ones_like(t)]).T
    (slope, intercept), *_ = np.linalg.lstsq(A, m, rcond=None)
    pred = slope * t + intercept
    ss_res = float(((m - pred) ** 2).sum())
    ss_tot = float(((m - m.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return {
        "slope_per_us": float(slope),
        "intercept": float(intercept),
        "r2": r2,
        "max_rel_residual": float(np.max(np.abs(m - pred) / m)),
        "n_points": len(t),
    }


def analyze_sweep(rows: list[dict], labels: list[str]) -> dict[str, Any]:
    t = np.array([r["exposure_time_us"] for r in rows], float)
    means = np.array([r["roi_means"] for r in rows], float)  # (n, 4)
    out: dict[str, Any] = {"exposure_time_us": t.tolist(), "channels": {}}
    for c, name in enumerate(labels):
        m = means[:, c]
        sel = (m >= FIT_RANGE[0]) & (m <= FIT_RANGE[1])
        ch: dict[str, Any] = {"roi_mean": m.tolist(), "saturated_at_us": None}
        sat = np.nonzero(m >= SAT_LEVEL)[0]
        if sat.size:
            ch["saturated_at_us"] = float(t[sat[0]])
        if sel.sum() >= 3:
            fit = linear_fit(t[sel], m[sel])
            ch["fit"] = fit
            ch["t_sat_est_us"] = (
                (1.0 - fit["intercept"]) / fit["slope_per_us"] if fit["slope_per_us"] > 0 else None
            )
        out["channels"][name] = ch
    # 가장 밝은 채널 기준 요약
    brightest = max(labels, key=lambda n: sum(out["channels"][n]["roi_mean"]))  # 포화 시 max는 동률
    out["brightest_channel"] = brightest
    b = out["channels"][brightest]
    out["t_sat_est_us"] = b.get("t_sat_est_us")
    out["r2_min"] = min(
        (ch["fit"]["r2"] for ch in out["channels"].values() if "fit" in ch), default=None
    )
    out["min_exposure_us"] = float(t.min())
    if out["t_sat_est_us"]:
        out["lowest_level_at_min_exposure"] = out["min_exposure_us"] / out["t_sat_est_us"]
    return out


# ---------------------------------------------------------------- flicker


def row_banding_ratio(diff: np.ndarray) -> float:
    """var(행 평균) / (var(화소) / 열 수). 행 간 상관 없는 노이즈면 기댓값 1.

    롤링 셔터에서 광원이 깜빡이면 행마다 밝기가 달라지고, 두 프레임의 위상이 다르면
    차분에 가로 줄무늬가 남아 비가 커진다. 센서 행 노이즈도 같은 방향으로 키운다.
    """
    d = diff - diff.mean()
    n_cols = d.shape[1]
    pix_var = float(d.var(ddof=1))
    row_var = float(d.mean(axis=1).var(ddof=1))
    return row_var / (pix_var / n_cols)


def dominant_row_period(row_profile: np.ndarray) -> float | None:
    """행 평균 프로파일의 가장 센 주기(행 단위). DC 제외."""
    x = row_profile - row_profile.mean()
    if x.size < 16:
        return None
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    k = int(np.argmax(spec[1:]) + 1)
    return x.size / k


def analyze_flicker_set(frames: list[Frame], labels: list[str]) -> dict[str, Any]:
    imgs = [center_crop(f.normalized(), f.raw.shape[0], BAND_COLS) for f in frames]
    roi_means = [float(center_crop(f.normalized(), ROI, ROI).mean()) for f in frames]
    pairs: list[dict[str, Any]] = []
    for i in range(0, len(imgs) - 1, 2):
        pa, pb = planes(imgs[i]), planes(imgs[i + 1])
        ratios = {n: row_banding_ratio(a - b) for n, a, b in zip(labels, pa, pb, strict=True)}
        g = next(n for n in labels if n.startswith("G"))
        prof = (pa[labels.index(g)] - pb[labels.index(g)]).mean(axis=1)
        pairs.append(
            {
                "frames": [i, i + 1],
                "banding_ratio": ratios,
                "banding_ratio_max": max(ratios.values()),
                "row_profile_std": float(prof.std(ddof=1)),
                "dominant_period_rows": dominant_row_period(prof),
            }
        )
    rm = np.array(roi_means)
    return {
        "exposure_time_us": frames[0].meta.get("exposure_time_us"),
        "analogue_gain": frames[0].meta.get("analogue_gain"),
        "roi_mean": rm.tolist(),
        "roi_mean_cv": float(rm.std(ddof=1) / rm.mean()) if rm.size > 1 else None,
        "pairs": pairs,
        "banding_ratio_max": max((p["banding_ratio_max"] for p in pairs), default=None),
    }


# ---------------------------------------------------------------- stability


def analyze_stability(rows: list[dict]) -> dict[str, Any]:
    m = np.array([r["roi_max_channel_mean"] for r in rows], float)
    ts = np.array([r["timestamp_ns"] or 0 for r in rows], float)
    t_s = (ts - ts[0]) / 1e9
    out: dict[str, Any] = {
        "n": len(m),
        "duration_s": float(t_s[-1]) if t_s.size else 0.0,
        "mean": float(m.mean()),
        "range_rel": float((m.max() - m.min()) / m.mean()),
        "cv": float(m.std(ddof=1) / m.mean()) if m.size > 1 else None,
        "first_to_last_rel": float((m[-1] - m[0]) / m.mean()),
        "t_s": t_s.tolist(),
        "roi_mean": m.tolist(),
        "temperature": [r.get("sensor_temperature") for r in rows],
    }
    if t_s[-1] > 0 and m.size >= 3:
        slope = np.polyfit(t_s, m, 1)[0]
        out["drift_rel_per_min"] = float(slope * 60.0 / m.mean())
    return out


# ---------------------------------------------------------------- uniformity


def grid_means(img: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    gh, gw = grid
    h, w = img.shape
    bh, bw = h // gh, w // gw
    return img[: bh * gh, : bw * gw].reshape(gh, bh, gw, bw).mean(axis=(1, 3))


def analyze_uniformity(frame: Frame, labels: list[str]) -> dict[str, Any]:
    img = frame.normalized()
    ps = planes(img)
    g = [i for i, n in enumerate(labels) if n.startswith("G")]
    green = (ps[g[0]] + ps[g[1]]) / 2.0
    gm = grid_means(green, GRID)
    c = center_crop(green, ROI // 2, ROI // 2)
    center = float(c.mean())
    rel = gm / center
    roi_blocks = grid_means(c, (8, 8))
    roi_planes = [center_crop(p, ROI // 2, ROI // 2).mean() for p in ps]
    gref = (roi_planes[g[0]] + roi_planes[g[1]]) / 2.0
    return {
        "grid_shape": list(GRID),
        "grid_rel_to_center": rel.tolist(),
        "corner_min_rel": float(min(rel[0, 0], rel[0, -1], rel[-1, 0], rel[-1, -1])),
        "grid_min_rel": float(rel.min()),
        "grid_max_rel": float(rel.max()),
        "roi_flatness": float(np.abs(roi_blocks / roi_blocks.mean() - 1).max()),
        "channel_ratio_to_green": {
            n: float(v / gref) for n, v in zip(labels, roi_planes, strict=True)
        },
    }


# ---------------------------------------------------------------- verdict


def grade(value: float | None, warn: float, fail: float, higher_bad: bool = True) -> str:
    if value is None or not np.isfinite(value):
        return "N/A"
    v = value if higher_bad else -value
    w, f = (warn, fail) if higher_bad else (-warn, -fail)
    return "FAIL" if v > f else "WARN" if v > w else "OK"


def verdicts(rep: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(item: str, value: str, status: str, note: str) -> None:
        out.append({"item": item, "value": value, "status": status, "note": note})

    for key in ("sweep_g1", "sweep_g16"):
        s = rep.get(key)
        if not s:
            continue
        r2 = s["r2_min"]
        st = "N/A" if r2 is None else "OK" if r2 >= LINEARITY_R2_MIN else "WARN"
        add(
            f"{key} 선형성 R²(최소 채널)",
            "—" if r2 is None else f"{r2:.5f}",
            st,
            f"기준 ≥ {LINEARITY_R2_MIN}",
        )
        tsat = s.get("t_sat_est_us")
        low = s.get("lowest_level_at_min_exposure")
        if tsat:
            st = "OK" if low is not None and low <= PTC_LOW_LEVEL else "WARN"
            add(
                f"{key} 포화 노출 추정",
                f"{tsat:,.0f} µs",
                st,
                f"최소 노출 {s['min_exposure_us']:.0f} µs에서 신호 {low * 100:.2f}% "
                f"(PTC 최저 단계 목표 ≤ {PTC_LOW_LEVEL * 100:.0f}%)",
            )

    fl = rep.get("flicker", {})
    for name, f in fl.items():
        br = f.get("banding_ratio_max")
        add(
            f"flicker {name} 줄무늬 비",
            "—" if br is None else f"{br:.2f}",
            grade(br, BANDING_RATIO_WARN, BANDING_RATIO_FAIL),
            f"무상관이면 ≈ 1, 경고 > {BANDING_RATIO_WARN}",
        )
        cv = f.get("roi_mean_cv")
        add(
            f"flicker {name} 프레임 평균 CV",
            "—" if cv is None else f"{cv * 100:.3f}%",
            grade(cv, MEAN_CV_WARN, MEAN_CV_FAIL),
            f"경고 > {MEAN_CV_WARN * 100:.1f}%",
        )

    st = rep.get("stability")
    if st:
        add(
            "stability 밝기 범위",
            f"{st['range_rel'] * 100:.3f}%",
            grade(st["range_rel"], STABILITY_RANGE_WARN, STABILITY_RANGE_FAIL),
            f"{st['n']}장 / {st['duration_s']:.0f} s, 경고 > {STABILITY_RANGE_WARN * 100:.1f}%",
        )

    u = rep.get("uniformity")
    if u:
        add(
            "uniformity 중앙 ROI 평탄도",
            f"{u['roi_flatness'] * 100:.2f}%",
            "OK" if u["roi_flatness"] <= ROI_FLATNESS_WARN else "WARN",
            f"ROI {ROI // 2}x{ROI // 2}(채널 평면) 8x8 블록 최대 편차, 경고 > "
            f"{ROI_FLATNESS_WARN * 100:.0f}%",
        )
        add(
            "uniformity 모서리 / 중앙",
            f"{u['corner_min_rel']:.3f}",
            "INFO",
            "렌즈 비네팅 포함. 쌍 차분 PTC에는 영향 작음",
        )
    return out


# ---------------------------------------------------------------- report


def analyze(run_dir: Path) -> dict[str, Any]:
    check = json.loads((run_dir / "check.json").read_text())
    session = json.loads((run_dir / "session.json").read_text())
    labels = channel_labels(session.get("bayer_order") or "BGGR")
    rep: dict[str, Any] = {
        "run_dir": str(run_dir),
        "bayer_order": session.get("bayer_order"),
        "channel_labels": labels,
        "capture_exit_status": session.get("exit_status"),
        "t50_us": check.get("t50_us"),
    }
    for key in ("sweep_g1", "sweep_g16"):
        if check.get(key):
            rep[key] = analyze_sweep(check[key], labels)
    rep["flicker"] = {}
    for name, rows in check.get("flicker", {}).items():
        frames = [load_frame(run_dir, r["file"]) for r in rows]
        rep["flicker"][name] = analyze_flicker_set(frames, labels)
    if check.get("stability"):
        rep["stability"] = analyze_stability(check["stability"])
        rep["uniformity"] = analyze_uniformity(
            load_frame(run_dir, check["stability"][0]["file"]), labels
        )
    rep["verdicts"] = verdicts(rep)
    return rep


def write_outputs(rep: dict[str, Any], out: Path, run_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False))
    labels = rep["channel_labels"]
    colors = {"R": "tab:red", "B": "tab:blue", "G1": "tab:green", "G2": "tab:olive"}

    # sweep
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    with (out / "sweep.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["sweep", "exposure_time_us", *labels])
        for ax, key in zip(axes, ("sweep_g1", "sweep_g16"), strict=True):
            s = rep.get(key)
            if not s:
                continue
            t = np.array(s["exposure_time_us"])
            for n in labels:
                ax.loglog(
                    t,
                    np.maximum(s["channels"][n]["roi_mean"], 1e-4),
                    "o-",
                    color=colors.get(n),
                    label=n,
                )
            for i, tt in enumerate(t):
                w.writerow([key, tt, *(s["channels"][n]["roi_mean"][i] for n in labels)])
            ax.axhline(1.0, color="k", lw=0.8, ls="--")
            ax.set_title(key)
            ax.set_xlabel("exposure time [µs]")
            ax.set_ylabel("ROI mean [normalized, black=0, white=1]")
            ax.grid(True, which="both", alpha=0.3)
            ax.legend()
    fig.tight_layout()
    fig.savefig(out / "sweep.png", dpi=120)
    plt.close(fig)

    # flicker row profiles (first pair, green)
    fl = rep.get("flicker", {})
    if fl:
        check = json.loads((run_dir / "check.json").read_text())
        fig, ax = plt.subplots(figsize=(11, 4))
        g = labels.index(next(n for n in labels if n.startswith("G")))
        with (out / "flicker_row_profile.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            cols: dict[str, np.ndarray] = {}
            for name, rows in check["flicker"].items():
                if len(rows) < 2:
                    continue
                a, b = (load_frame(run_dir, r["file"]) for r in rows[:2])
                da = planes(center_crop(a.normalized(), a.raw.shape[0], BAND_COLS))[g]
                db = planes(center_crop(b.normalized(), b.raw.shape[0], BAND_COLS))[g]
                prof = (da - db).mean(axis=1)
                cols[name] = prof / max(da.mean(), 1e-9)
                ax.plot(
                    cols[name] * 100, lw=0.8, label=f"{name} ({fl[name]['exposure_time_us']} µs)"
                )
            w.writerow(["plane_row", *cols])
            n = min((len(v) for v in cols.values()), default=0)
            for i in range(n):
                w.writerow([i, *(v[i] for v in cols.values())])
        ax.set_xlabel("row (green plane, full height)")
        ax.set_ylabel("row mean of (A - B) / mean [%]")
        ax.set_title("pair-difference row profile (horizontal banding → flicker)")
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(out / "flicker_row_profile.png", dpi=120)
        plt.close(fig)

    st = rep.get("stability")
    if st:
        fig, ax = plt.subplots(figsize=(7, 3.5))
        rel = (np.array(st["roi_mean"]) / st["mean"] - 1) * 100
        ax.plot(st["t_s"], rel, "o-")
        ax.set_xlabel("time [s]")
        ax.set_ylabel("ROI mean deviation [%]")
        ax.set_title("stability (min gain, ~50% signal)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(out / "stability.png", dpi=120)
        plt.close(fig)
        with (out / "stability.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["t_s", "roi_mean", "deviation_pct", "sensor_temperature"])
            for row in zip(st["t_s"], st["roi_mean"], rel, st["temperature"], strict=True):
                w.writerow(row)

    u = rep.get("uniformity")
    if u:
        rel = np.array(u["grid_rel_to_center"])
        fig, ax = plt.subplots(figsize=(7, 4.5))
        im = ax.imshow(rel, cmap="viridis")
        for (i, j), v in np.ndenumerate(rel):
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", color="w", fontsize=8)
        fig.colorbar(im, ax=ax, label="green mean / center")
        ax.set_title("uniformity (grid mean relative to center)")
        fig.tight_layout()
        fig.savefig(out / "uniformity.png", dpi=120)
        plt.close(fig)
        with (out / "uniformity.csv").open("w", newline="") as fh:
            csv.writer(fh).writerows(rel.tolist())

    (out / "report.md").write_text(render_md(rep))


def _r1(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def render_md(rep: dict[str, Any]) -> str:
    L = [f"# 플랫필드 셋업 판정: `{Path(rep['run_dir']).name}`", ""]
    L += ["| 항목 | 값 | 판정 | 비고 |", "|---|---|---|---|"]
    L += [f"| {v['item']} | {v['value']} | {v['status']} | {v['note']} |" for v in rep["verdicts"]]
    L += ["", f"Bayer 순서 {rep['bayer_order']}, 채널 {', '.join(rep['channel_labels'])}.", ""]
    for key in ("sweep_g1", "sweep_g16"):
        s = rep.get(key)
        if not s:
            continue
        L += [
            f"## {key}",
            "",
            "| 노출 µs | " + " | ".join(rep["channel_labels"]) + " |",
            "|---" * (len(rep["channel_labels"]) + 1) + "|",
        ]
        for i, t in enumerate(s["exposure_time_us"]):
            vals = " | ".join(
                f"{s['channels'][n]['roi_mean'][i]:.4f}" for n in rep["channel_labels"]
            )
            L.append(f"| {t:.0f} | {vals} |")
        L.append("")
    for name, f in rep.get("flicker", {}).items():
        L.append(
            f"- flicker `{name}`: 노출 {f['exposure_time_us']} µs, 게인 {f['analogue_gain']:.3f}, "
            f"줄무늬 비 {[round(p['banding_ratio_max'], 2) for p in f['pairs']]}, "
            f"주기(행) {[_r1(p['dominant_period_rows']) for p in f['pairs']]}"
        )
    st = rep.get("stability")
    if st:
        L += [
            "",
            f"- stability: 처음→끝 {st['first_to_last_rel'] * 100:+.3f}%, "
            f"드리프트 {st.get('drift_rel_per_min', float('nan')) * 100:+.3f}%/min",
        ]
    u = rep.get("uniformity")
    if u:
        L.append(
            "- 채널 비(초록 대비): "
            + ", ".join(f"{k} {v:.3f}" for k, v in u["channel_ratio_to_green"].items())
        )
    L += ["", "그림: sweep.png, flicker_row_profile.png, stability.png, uniformity.png (+ CSV)", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    out = args.out or Path("results/p1/flatfield_check") / args.run_dir.name
    rep = analyze(args.run_dir)
    write_outputs(rep, out, args.run_dir)
    print(render_md(rep))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
