"""Phase 2: 실제 장면 브래킷 합성 + DCG 시뮬레이션 데모, C++ vs Python 기준 모델 확인.

사용: python experiments/p2_scene.py configs/p2_scene.yaml
출력: results/p2_scene/<timestamp>/{merged_<variant>/, dcg/, scene_stops.png, source_maps.png,
      summary.json, summary.md}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap

from cctvlog import cli, runner
from cctvlog.io import bracket as bio
from cctvlog.ref import bracket_merge as ref

INK = "#0b0b0b"
SRC_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#e34948"]  # 프레임 0, 1, 2, 포화


def _p(path: str) -> Path:
    q = Path(path)
    return q if q.is_absolute() else cli.REPO_ROOT / q


def luminance_stops(bayer: np.ndarray) -> np.ndarray:
    """그림용: 2x2 Bayer 블록 평균 → log2 (표시 전용, 처리 경로 아님)."""
    h, w = bayer.shape
    q = bayer[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))
    return np.log2(np.maximum(q, 2.0**-12))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    exp = runner.start_experiment(argv[0])
    cfg, out = exp.config, exp.out_dir
    bdir, mcfg = _p(cfg["bracket_dir"]), _p(cfg["merge_config"])
    params = json.loads(mcfg.read_text())
    summ: dict[str, Any] = {"bracket_dir": cfg["bracket_dir"], "variants": {}}
    images: dict[str, np.ndarray] = {}
    sources: dict[str, np.ndarray] = {}
    for v in cfg["variants"]:
        od = out / f"merged_{v['name']}"
        cli.run_cpp("hdr_merge", config=mcfg, in_=bdir, out=od, frames=v["frames"])
        m, s = np.load(od / "merged.npy"), np.load(od / "source.npy")
        fr = bio.read_bracket(bdir, v["frames"])
        rm, rs = ref.bracket_merge(
            bio.load_raws(bdir, fr),
            [f.exposure_us for f in fr],
            [f.black for f in fr],
            [f.white for f in fr],
            params["tau"],
            params["band_ratio"],
        )
        nz = rm != 0
        rel = float(np.max(np.abs(m[nz] - rm[nz]) / np.abs(rm[nz])))
        meta = json.loads((od / "merged.json").read_text())
        run = json.loads((od / "run.json").read_text())
        # 출처 맵 번호 = 고른 프레임 목록 안의 위치 → bracket.json 파일 번호로 바꾼다
        idx = np.asarray(v["frames"])[s & ref.SOURCE_INDEX_MASK]
        summ["variants"][v["name"]] = {
            "frames": v["frames"],
            "ratios_actual": [f["ratio"] for f in meta["frames"]],
            "x_max": meta["x_max"],
            "max_rel_err_vs_python": rel,
            "source_map_identical": bool(np.array_equal(s, rs)),
            "fraction_by_frame": {str(i): float((idx == i).mean()) for i in v["frames"]},
            "fraction_blend": float((s & ref.SOURCE_BLEND > 0).mean()),
            "fraction_saturated": float((s & ref.SOURCE_SATURATED > 0).mean()),
            "p99_9_x": float(np.quantile(m, 0.999)),
            "merge_ms": run["timings_ms"].get("bracket_merge"),
            "omp_threads": run["omp_max_threads"],
        }
        images[v["name"]] = m
        sources[v["name"]] = np.where(s & ref.SOURCE_SATURATED, 3, idx)
        if v["name"] != cfg["dcg"]["input_variant"]:
            (od / "merged.npy").unlink()

    dv = cfg["dcg"]["input_variant"]
    dcg_out = out / "dcg"
    cli.run_cpp(
        "dcg_sim",
        config=_p(cfg["dcg"]["config"]),
        in_=out / f"merged_{dv}" / "merged.npy",
        out=dcg_out,
    )
    d, ds = np.load(dcg_out / "dcg.npy"), np.load(dcg_out / "dcg_source.npy")
    drun = json.loads((dcg_out / "run.json").read_text())
    summ["dcg"] = {
        "input": f"merged_{dv}",
        "fraction_lcg": float(((ds & 0x0F) == 1).mean()),
        "fraction_blend": float((ds & 0x40 > 0).mean()),
        "fraction_saturated": float((ds & 0x80 > 0).mean()),
        "simulate_ms": drun["timings_ms"].get("dcg_simulate"),
    }
    images["DCG (sim)"] = d
    for f in ("dcg.npy", "dcg_source.npy"):
        (dcg_out / f).unlink()
    (out / f"merged_{dv}" / "merged.npy").unlink()

    # 그림: 장면 밝기(stop) — 긴 노출 단독 vs 합성
    long = bio.read_bracket(bdir, [0])
    raw0 = bio.load_raws(bdir, long)[0].astype(np.float64)
    images = {
        "long only (16 ms)": (raw0 - long[0].black) / (long[0].white - long[0].black),
        **images,
    }
    fig, axes = plt.subplots(len(images), 1, figsize=(8, 4.6 * len(images)))
    for ax, (name, img) in zip(axes, images.items(), strict=True):
        im = ax.imshow(luminance_stops(img), cmap="gray", vmin=-10, vmax=4.2)
        ax.set_title(f"{name}: log2(linear), 0 = long-exposure saturation", color=INK)
        ax.axis("off")
        fig.colorbar(im, ax=ax, fraction=0.03, label="stop")
    fig.tight_layout()
    fig.savefig(out / "scene_stops.png", dpi=90)
    plt.close(fig)
    fig, axes = plt.subplots(len(sources), 1, figsize=(8, 4.6 * len(sources)))
    for ax, (name, s) in zip(axes, sources.items(), strict=True):
        code = s[::2, ::2]
        ax.imshow(
            code, cmap=ListedColormap(SRC_COLORS), vmin=-0.5, vmax=3.5, interpolation="nearest"
        )
        ax.set_title(f"{name} source: blue=16 ms, orange=4 ms, aqua=1 ms, red=saturated", color=INK)
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(out / "source_maps.png", dpi=90)
    plt.close(fig)

    (out / "summary.json").write_text(json.dumps(summ, indent=2, ensure_ascii=False))
    L = [
        "# Phase 2 실제 장면 합성 (Phase 0 책상 브래킷)",
        "",
        "| 구성 | 실제 노출비 | 최대 상대오차 vs Python | 출처 맵 동일 | 프레임별 비율 | 띠 | "
        "포화 | 99.9% x | 합성 시간 [ms] |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for n, v in summ["variants"].items():
        fr = ", ".join(f"{k}: {x * 100:.1f}%" for k, x in v["fraction_by_frame"].items())
        L.append(
            f"| {n} | {', '.join(f'{r:.4f}' for r in v['ratios_actual'])} | "
            f"{v['max_rel_err_vs_python']:.2e} | {v['source_map_identical']} | {fr} | "
            f"{v['fraction_blend'] * 100:.1f}% | {v['fraction_saturated'] * 100:.2f}% | "
            f"{v['p99_9_x']:.2f} | {v['merge_ms']:.1f} ({v['omp_threads']} thr) |"
        )
    dd = summ["dcg"]
    L += [
        "",
        f"DCG 시뮬레이션 (입력 {dd['input']}): LCG {dd['fraction_lcg'] * 100:.1f}%, "
        f"띠 {dd['fraction_blend'] * 100:.1f}%, 포화 {dd['fraction_saturated'] * 100:.2f}%, "
        f"{dd['simulate_ms']:.0f} ms",
        "",
        "그림: scene_stops.png, source_maps.png",
        "",
    ]
    md = "\n".join(L)
    (out / "summary.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"→ {out}")
    ok = all(
        v["max_rel_err_vs_python"] <= 1e-5 and v["source_map_identical"]
        for v in summ["variants"].values()
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
