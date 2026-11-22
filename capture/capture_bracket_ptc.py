#!/usr/bin/env python3
"""Phase 2 플랫필드 브래킷 스윕: 합성 후 노이즈 분포 실측용 (CLAUDE.md 6.3, Phase 2).

광원은 고정하고 긴 노출 t_long만 바꾼다. 단계마다 노출비(기본 1:4:16)를 유지한 브래킷 세트를
sets개 찍는다. 같은 단계의 세트 두 개가 프레임 쌍 차분의 쌍이 된다.

  1) 최소 게인에서 포화 노출 t_sat 측정 (capture_ptc.calibrate와 같은 방법)
  2) 단계 x (긴 노출 스케일, 가장 밝은 채널 기준)마다 t_long = x·t_sat,
     노출 = t_long / ratio. t_long이 최대 노출을 넘는 단계는 건너뛰고 기록
  3) 단계 사이 광원 모니터 프레임 (게인 1, 0.5·t_sat)
  4) 노출이 적용되지 않는 단계(TimeoutError)는 기록하고, 그 위 단계는 시도하지 않는다

사용 예 (PC에서):
  scripts/pi.sh job bracket_flat_YYYYMMDD_HHMM capture_bracket_ptc.py --notes "광원: ..."
  # 계획만 확인 (probe 후 종료)
  scripts/pi.sh job bracket_probe_YYYYMMDD_HHMM capture_bracket_ptc.py --probe-only

출력:
  OUT/session.json, OUT/plan.json (t_sat, 단계, 건너뛴 단계), OUT/sweep.json (프레임 색인)
  OUT/probe/g1/, OUT/monitor/
  OUT/flat/step_SS/bracket_KKKK/{NNNNNN.npy, NNNNNN.json, bracket.json}
    (capture_raw.py와 같은 형식)

근거: docs/decisions.md 2026-11-22 (밝기는 Phase 1 그대로 → 16:1 경계의 짧은 노출 ≥ 1.7 ms),
docs/pi_notes.md (still 구성 400 ms 노출 적용 확인, 최소 게인 1.1228).
"""

from __future__ import annotations

import argparse
import datetime as dt
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

import capture_ptc as cp
import capture_raw as cr
import flatsession as fs
import rawutil as ru

# 긴 노출 스케일 x. 경계 띠 [0.72, 0.9](긴→중간, 긴→짧은)와 [2.88, 3.6](중간→짧은)에 촘촘히.
DEFAULT_LEVELS = (
    0.04, 0.1, 0.25, 0.5, 0.65,
    0.72, 0.76, 0.8, 0.84, 0.88, 0.95,
    1.2, 1.8, 2.5,
    2.9, 3.05, 3.2, 3.35, 3.5, 3.8,
    5.0, 7.0, 10.0, 13.0,
)  # fmt: skip
DEFAULT_RATIOS = (1.0, 4.0, 16.0)
ROI = 512
EXPOSURE_MAX_US = 1_000_000
MONITOR_EVERY = 6


def parse_floats(text: str) -> tuple[float, ...]:
    v = tuple(float(s) for s in text.split(",") if s.strip())
    if not v or any(x <= 0 for x in v) or list(v) != sorted(set(v)):
        raise ValueError(f"values must be positive and strictly increasing: {text}")
    return v


def plan_steps(
    t_sat_us: float,
    levels: tuple[float, ...],
    ratios: tuple[float, ...],
    exp_min: float = fs.EXPOSURE_MIN_US,
    exp_max: float = EXPOSURE_MAX_US,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """단계 x → 요청 노출 [t_long, t_long/r1, ...]. 범위를 벗어나는 단계는 건너뛴다."""
    if ratios[0] != 1.0:
        raise ValueError("ratios must start with 1")
    steps, skipped = [], []
    for lv in levels:
        t_long = lv * t_sat_us
        exps = [float(round(t_long / r)) for r in ratios]
        if exps[0] > exp_max:
            skipped.append({"level": lv, "exposures_us": exps, "reason": "t_long > max exposure"})
            continue
        if exps[-1] < exp_min:
            skipped.append({"level": lv, "exposures_us": exps, "reason": "t_short < min exposure"})
            continue
        steps.append({"level": lv, "exposures_us_requested": exps})
    return steps, skipped


def within_set_distinguishable(exps: list[float], tol: float) -> bool:
    """세트 안에서 바로 앞 설정으로 찍힌 프레임이 다음 설정의 허용치에 들어가지 않는가.
    세트 사이(짧은 → 긴)도 확인한다."""
    seq = [*exps, exps[0]]
    return all(cp.distinguishable(a, b, tol) for a, b in itertools.pairwise(seq))


def bracket_json(k: int, rows: list[dict[str, Any]], requested: list[float], tol: float) -> dict:
    actual = [r["exposure_time_us"] for r in rows]
    summary = ru.bracket_ratio_summary(requested, actual, tol)
    return {
        "bracket_index": k,
        "exposure_time_us_requested": requested,
        "exposure_time_us_actual": actual,
        "analogue_gain": [r["gain"] for r in rows],
        "timestamps_ns": [r["timestamp_ns"] for r in rows],
        "files": [f"{i:06d}.npy" for i in range(len(rows))],
        **summary,
        "note": "flat sweep: ratio check is informational (no retry); merge uses actual exposures",
    }


def run_sweep(run: cp.Run, args: argparse.Namespace, plan: dict[str, Any]) -> None:
    s = run.s
    tol = s.cap.args.tolerance
    gain = cp.GAINS["g1"]
    cal = cp.calibrate(run, "g1", gain, 20000.0, tol)
    steps, skipped = plan_steps(cal["t_sat_us"], args.levels, args.ratios)
    plan.update({"t_sat_us": cal["t_sat_us"], "calibration": cal, "steps": steps})
    plan["skipped_steps"] = skipped
    t_ref = 0.5 * cal["t_sat_us"]
    plan["monitor_exposure_us"] = round(t_ref)
    write_json(args.out / "plan.json", plan)
    print(
        f"[plan] t_sat {cal['t_sat_us']:.0f} us → {len(steps)} steps"
        + (f", skipped {[x['level'] for x in skipped]}" if skipped else ""),
        flush=True,
    )
    for st in steps:
        print(f"  x {st['level']:<5} exposures {st['exposures_us_requested']}", flush=True)
    if args.probe_only:
        return
    cp.monitor(run, t_ref, "start")
    for si, st in enumerate(steps):
        exps = st["exposures_us_requested"]
        if not within_set_distinguishable(exps, tol):
            st["status"] = "skipped: exposures not distinguishable"
            continue
        step_dir = args.out / "flat" / f"step_{si:02d}"
        try:
            for k in range(args.sets):
                bdir = step_dir / f"bracket_{k:04d}"
                rows = []
                for i, t in enumerate(exps):
                    s.set(gain, t)
                    r = s.grab(bdir, i, f"bracket_flat:step{si:02d}:set{k}:frame{i}")
                    r.update({"part": "flat", "step": si, "level": st["level"], "set": k,
                              "frame": i})  # fmt: skip
                    run.frames.append(r)
                    rows.append(r)
                write_json(bdir / "bracket.json", bracket_json(k, rows, exps, tol))
                print(
                    f"[step {si:02d} x {st['level']:<5} set {k}] exp "
                    + " ".join(f"{r['exposure_time_us']}" for r in rows)
                    + "  ROI max ch "
                    + " ".join(f"{r['roi_max_channel_mean']:.3f}" for r in rows),
                    flush=True,
                )
        except TimeoutError as e:
            # 노출이 적용되지 않음(최대 노출 초과 등). 이 단계와 위 단계는 시도하지 않는다.
            st["status"] = f"failed: {e}"
            for later in steps[si + 1 :]:
                later["status"] = "not attempted (previous step failed)"
            print(f"[step {si:02d}] FAILED: {e}", flush=True)
            break
        st["status"] = "ok"
        st["dir"] = str(step_dir.relative_to(args.out))
        write_json(args.out / "plan.json", plan)
        if (si + 1) % MONITOR_EVERY == 0:
            cp.monitor(run, t_ref, f"after_step{si:02d}")
    cp.monitor(run, t_ref, "end")


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(ru.jsonable(obj), indent=2, ensure_ascii=False))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    fs.add_common_args(ap)
    ap.add_argument("--levels", default=",".join(map(str, DEFAULT_LEVELS)))
    ap.add_argument("--ratios", default=",".join(map(str, DEFAULT_RATIOS)))
    ap.add_argument("--sets", type=int, default=2, help="단계당 브래킷 세트 수 (쌍 차분에 2 이상)")
    ap.add_argument("--roi", type=int, default=ROI)
    ap.add_argument("--probe-only", action="store_true", help="t_sat 측정과 계획 출력만")
    args = ap.parse_args(argv)
    args.levels = parse_floats(args.levels)
    args.ratios = parse_floats(args.ratios)
    if args.sets < 2:
        ap.error("--sets >= 2")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fs.ensure_empty(args.out)
    plan: dict[str, Any] = {
        "levels": list(args.levels),
        "ratios": list(args.ratios),
        "sets": args.sets,
        "roi": args.roi,
        "gain": cp.GAINS["g1"],
    }
    session: dict[str, Any] = {
        "script": "capture_bracket_ptc.py",
        "argv": sys.argv,
        "started": dt.datetime.now().astimezone().isoformat(),
        "versions": cr.versions(),
    }
    t0 = time.monotonic()
    s: fs.Session | None = None
    run: cp.Run | None = None
    status = 1
    try:
        fs.arm_watchdog()
        s = fs.Session(
            args.out,
            camera=args.camera,
            lens_position=args.lens_position,
            roi=args.roi,
            note_prefix="capture_bracket_ptc:",
            notes=args.notes,
        )
        session.update(s.describe())
        fs.arm_watchdog()
        md0 = s.start()
        session["sensor_flip"] = s.cap.flip
        session["lens_position_metadata"] = md0.get("LensPosition")
        run = cp.Run(s, args.out)
        run_sweep(run, args, plan)
        status = 0
        fs.disarm_watchdog()
    except BaseException as e:
        session["error"] = repr(e)
        raise
    finally:
        if s is not None:
            s.close()
        session["finished"] = dt.datetime.now().astimezone().isoformat()
        session["seconds"] = round(time.monotonic() - t0, 3)
        session["exit_status"] = status
        write_json(args.out / "session.json", session)
        write_json(args.out / "plan.json", plan)
        write_json(args.out / "sweep.json", {"frames": [] if run is None else run.frames})
    print(f"done: {args.out} ({session['seconds']} s, {len(run.frames)} frames)", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
