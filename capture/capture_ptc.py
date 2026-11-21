#!/usr/bin/env python3
"""Phase 1 PTC 촬영: 게인 {1, 4, 16} x 노출 단계(포화 포함) x 플랫필드 쌍 + 다크 프레임.

두 번에 나눠 실행한다 (다크는 렌즈를 완전히 가린 뒤).

  1) flat  게인마다 포화 노출 t_sat을 자동 측정(probe) → 밝기 단계 levels x t_sat로 노출을
           정해 쌍(pairs개)을 찍는다. 게인 사이마다 고정 조건 기준 프레임(monitor)으로 광원
           드리프트를 기록한다. 계획과 실제 값은 plan.json.
  2) dark  flat의 plan.json을 읽어 게인마다 {가장 짧은, 가장 긴} 노출로 다크 N장.
           빛 누출이 보이면 즉시 중단.

사용 예 (PC에서):
  scripts/pi.sh job ptc_flat_YYYYMMDD_HHMM capture_ptc.py --part flat --notes "광원: ..."
  # 렌즈 캡/차광 후
  scripts/pi.sh job ptc_dark_YYYYMMDD_HHMM capture_ptc.py --part dark \
      --plan ../runs/ptc_flat_YYYYMMDD_HHMM/plan.json

출력 (CLAUDE.md 5.1, 5.2):
  OUT/session.json, OUT/ptc.json (프레임 색인: 게인, 단계, 쌍, 파일, 실제 노출, ROI 평균)
  flat: OUT/plan.json, OUT/probe/{g1,g4,g16}/, OUT/flat/{g1,g4,g16}/, OUT/monitor/
  dark: OUT/dark/{g1,g4,g16}/
  프레임마다 NNNNNN.npy (uint16, right-shift 완료) + NNNNNN.json (5.2 + dropped_frames_before)

근거: docs/phase1_equipment.md, results/p1/flatfield_check (깜빡임은 노출 1.7 ms 이상에서 무시
가능, 중앙 256x256 ROI 편차 약 7%), docs/pi_notes.md (최소 게인 1.1228, 최소 노출 13 µs).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

import capture_raw as cr
import flatsession as fs
import rawutil as ru

GAINS: dict[str, float] = {"g1": fs.GAIN_MIN, "g4": 4.0, "g16": fs.GAIN_MAX}  # 4.0 = code 768
# 가장 밝은 채널(초록)의 포화 노출 대비 밝기. 1 초과 단계는 R·B(초록의 약 0.5배)까지 포화시킨다.
DEFAULT_LEVELS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.45, 0.6, 0.75, 0.9, 1.1, 1.5, 2.2)
ROI = 256  # 센서 화소. 플랫필드 판정에서 이 크기의 블록 편차가 약 7%
EXPOSURE_MAX_US = 1_000_000
PROBE_LOW, PROBE_HIGH = 0.1, 0.9  # 이 범위의 신호로 t_sat 추정
PROBE_MAX = 12
QUANT_MARGIN_US = 27.0  # 짧은 노출의 라인 양자화 오차(실측 최대 약 20 µs) 여유
LEAK_WARN, LEAK_ABORT = 0.002, 0.005  # 다크 ROI 평균(정규화) — 0.005 ≈ 4.8 DN


# ---------------------------------------------------------------- 순수 로직 (PC 테스트 대상)


def parse_levels(text: str) -> tuple[float, ...]:
    levels = tuple(float(v) for v in text.split(",") if v.strip())
    if not levels or any(v <= 0 for v in levels) or list(levels) != sorted(set(levels)):
        raise ValueError(f"levels must be positive and strictly increasing: {text}")
    return levels


def distinguishable(prev_us: float, new_us: float, tol: float) -> bool:
    """이전 설정으로 찍힌 프레임이 새 설정의 허용치(flatsession.exposure_close)에 들어가지 않을 만큼
    떨어져 있는가. 라인 양자화로 실제 노출이 요청과 최대 약 20 µs 어긋나는 것까지 고려한다."""
    window = max(tol * new_us, fs.EXPOSURE_ABS_TOL_US)
    return abs(new_us - prev_us) > window + QUANT_MARGIN_US


def plan_exposures(
    t_sat_us: float,
    levels: tuple[float, ...],
    tol: float,
    exp_min: float = fs.EXPOSURE_MIN_US,
    exp_max: float = EXPOSURE_MAX_US,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """밝기 단계 → 요청 노출. 범위 밖은 잘라내고, 앞 단계와 구분되지 않는 단계는 뺀다.
    반환: (단계 목록, 빠진 단계와 이유)."""
    steps: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for lv in levels:
        t = float(round(min(max(lv * t_sat_us, exp_min), exp_max)))
        if steps and not distinguishable(steps[-1]["exposure_us_requested"], t, tol):
            skipped.append({"level": lv, "exposure_us": t, "reason": "too close to previous step"})
            continue
        steps.append({"level": lv, "exposure_us_requested": t})
    return steps, skipped


def next_probe_exposure(t_us: float, m: float) -> float | None:
    """probe 한 장의 결과로 다음 노출. None이면 t_sat 추정에 쓸 수 있는 신호."""
    if m >= PROBE_HIGH:
        return t_us / 4.0
    if m < PROBE_LOW:
        return t_us * min(0.5 / max(m, 1e-3), 50.0)
    return None


def dark_exposures(plan: dict[str, Any], label: str) -> list[float]:
    """다크 노출: 해당 게인 flat 단계의 가장 짧은·가장 긴 실제 요청 노출."""
    ts = [st["exposure_us_requested"] for st in plan["gains"][label]["steps"]]
    return sorted({min(ts), max(ts)})


# ---------------------------------------------------------------- 촬영


class Run:
    def __init__(self, s: fs.Session, out: Path) -> None:
        self.s = s
        self.out = out
        self.frames: list[dict[str, Any]] = []
        self.notes: list[dict[str, Any]] = []
        self.idx: dict[str, int] = {}

    def grab(self, sub: str, note: str, **tags: Any) -> dict[str, Any]:
        i = self.idx.get(sub, 0)
        self.idx[sub] = i + 1
        r = self.s.grab(self.out / sub, i, note)
        r.update(tags)
        self.frames.append(r)
        return r


def calibrate(run: Run, label: str, gain: float, t_guess: float, tol: float) -> dict[str, Any]:
    """t_sat(가장 밝은 채널이 1.0이 되는 노출) 추정. 선형성 R² = 1.00000(셋업 판정)이므로
    원점 통과 비례로 충분하다. 첫 추정 뒤 0.6·t_sat에서 한 번 더 재서 그 값을 쓴다."""
    t = t_guess
    first: float | None = None
    for _ in range(PROBE_MAX):
        t = float(round(min(max(t, fs.EXPOSURE_MIN_US), EXPOSURE_MAX_US)))
        run.s.set(gain, t)
        r = run.grab(f"probe/{label}", f"probe:{label}", part="probe", gain_label=label)
        m, ta = r["roi_max_channel_mean"], float(r["exposure_time_us"])
        print(f"[probe {label}] exp {ta:>9.0f} us  ROI max ch {m:.3f}", flush=True)
        nxt = next_probe_exposure(t, m)
        if nxt is not None:
            if (nxt < t and t <= fs.EXPOSURE_MIN_US) or (nxt > t and t >= EXPOSURE_MAX_US):
                raise SystemExit(
                    f"[{label}] light level out of range: ROI {m:.3f} at {ta:.0f} us. "
                    "확산판 장수·거리로 밝기를 조절하세요 (docs/phase1_equipment.md)"
                )
            t = nxt
            continue
        est = ta / m
        if first is not None:
            return {
                "t_sat_us": est,
                "t_sat_first_us": first,
                "probe_frames": run.idx[f"probe/{label}"],
            }
        first = est
        t_next = 0.6 * est
        if not distinguishable(t, t_next, tol):
            return {
                "t_sat_us": est,
                "t_sat_first_us": est,
                "probe_frames": run.idx[f"probe/{label}"],
            }
        t = t_next
    raise SystemExit(f"[{label}] t_sat calibration did not converge in {PROBE_MAX} frames")


def monitor(run: Run, t_ref: float, tag: str, n: int = 2) -> None:
    run.s.set(GAINS["g1"], t_ref)
    rs = [run.grab("monitor", f"monitor:{tag}", part="monitor", tag=tag) for _ in range(n)]
    print(
        f"[monitor {tag}] exp {rs[0]['exposure_time_us']} us  ROI max ch "
        + " ".join(f"{r['roi_max_channel_mean']:.4f}" for r in rs),
        flush=True,
    )


def run_flat(run: Run, args: argparse.Namespace, plan: dict[str, Any]) -> None:
    tol = run.s.cap.args.tolerance
    t_ref: float | None = None  # monitor 노출: 최소 게인 50% 신호
    prev: tuple[float, float] | None = None  # (게인, t_sat) — 다음 게인 probe 시작점
    for label in args.gains:
        gain = GAINS[label]
        t_guess = 20000.0 if prev is None else prev[1] * prev[0] / gain
        cal = calibrate(run, label, gain, t_guess, tol)
        steps, skipped = plan_exposures(cal["t_sat_us"], args.levels, tol)
        plan["gains"][label] = {"analogue_gain_requested": gain, **cal, "steps": steps}
        if skipped:
            plan["gains"][label]["skipped_steps"] = skipped
        write_json(args.out / "plan.json", plan)
        print(
            f"[plan {label}] t_sat {cal['t_sat_us']:.0f} us → {len(steps)} steps"
            + (f", skipped levels {[s['level'] for s in skipped]}" if skipped else ""),
            flush=True,
        )
        if label == "g1":
            t_ref = 0.5 * cal["t_sat_us"]
            plan["monitor_exposure_us"] = round(t_ref)
            monitor(run, t_ref, "start")
        for si, st in enumerate(steps):
            run.s.set(gain, st["exposure_us_requested"])
            rows = []
            for p in range(args.pairs):
                for f in (0, 1):
                    rows.append(
                        run.grab(
                            f"flat/{label}",
                            f"flat:{label}:step{si:02d}:pair{p}:{f}",
                            part="flat",
                            gain_label=label,
                            step=si,
                            level=st["level"],
                            pair=p,
                            frame=f,
                        )
                    )
            st["exposure_us_actual"] = rows[0]["exposure_time_us"]
            print(
                f"[flat {label} {si:02d}] level {st['level']:<5} "
                f"exp {rows[0]['exposure_time_us']:>8} us  ROI max ch "
                + " ".join(f"{r['roi_max_channel_mean']:.4f}" for r in rows),
                flush=True,
            )
        write_json(args.out / "plan.json", plan)
        if t_ref is not None:
            monitor(run, t_ref, f"after_{label}")
        prev = (gain, cal["t_sat_us"])


def run_dark(run: Run, args: argparse.Namespace, plan: dict[str, Any]) -> dict[str, Any]:
    leak: dict[str, Any] = {}
    for label in args.gains:
        gain = GAINS[label]
        for t in dark_exposures(plan, label):
            run.s.set(gain, t)
            rows = [
                run.grab(
                    f"dark/{label}",
                    f"dark:{label}:{t:.0f}us",
                    part="dark",
                    gain_label=label,
                    exposure_us_requested=t,
                )
                for _ in range(args.dark_frames)
            ]
            m = max(rows[0]["roi_means"])
            leak[f"{label}_{t:.0f}us"] = m
            print(
                f"[dark {label}] exp {rows[0]['exposure_time_us']:>8} us  {len(rows)} frames  "
                f"ROI max ch {m:+.5f} ({m * 959:+.2f} DN)",
                flush=True,
            )
            if m > LEAK_ABORT:
                raise SystemExit(
                    f"light leak: dark ROI mean {m:.4f} (> {LEAK_ABORT}) at {label} {t:.0f} us. "
                    "렌즈 캡·차광을 확인하고 다시 실행하세요."
                )
            if m > LEAK_WARN:
                print(f"  WARNING: dark level {m:.4f} > {LEAK_WARN} (빛 누출 의심)", flush=True)
    return leak


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(ru.jsonable(obj), indent=2, ensure_ascii=False))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    fs.add_common_args(ap)
    ap.add_argument("--part", choices=("flat", "dark"), required=True)
    ap.add_argument("--gains", default="g1,g4,g16", help=f"쉼표 구분, {list(GAINS)} 중")
    ap.add_argument("--levels", default=",".join(map(str, DEFAULT_LEVELS)))
    ap.add_argument("--pairs", type=int, default=2, help="단계당 플랫필드 쌍 수")
    ap.add_argument("--dark-frames", type=int, default=10, help="게인·노출 조합당 다크 장수")
    ap.add_argument("--plan", type=Path, default=None, help="dark: flat 실행의 plan.json")
    ap.add_argument("--roi", type=int, default=ROI)
    args = ap.parse_args(argv)
    args.gains = [g.strip() for g in args.gains.split(",") if g.strip()]
    if unknown := [g for g in args.gains if g not in GAINS]:
        ap.error(f"unknown gains {unknown}")
    args.levels = parse_levels(args.levels)
    if args.part == "dark" and args.plan is None:
        ap.error("--part dark requires --plan PATH (flat 실행의 plan.json)")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan: dict[str, Any]
    if args.part == "dark":
        plan = json.loads(args.plan.read_text())
        if missing := [g for g in args.gains if g not in plan.get("gains", {})]:
            raise SystemExit(f"plan has no gains {missing}: {args.plan}")
    else:
        plan = {"levels": list(args.levels), "pairs": args.pairs, "roi": args.roi, "gains": {}}
    fs.ensure_empty(args.out)
    session: dict[str, Any] = {
        "script": "capture_ptc.py",
        "part": args.part,
        "argv": sys.argv,
        "started": dt.datetime.now().astimezone().isoformat(),
        "versions": cr.versions(),
        "plan_source": None if args.plan is None else str(args.plan),
    }
    t0 = time.monotonic()
    s: fs.Session | None = None
    run: Run | None = None
    status = 1
    result: dict[str, Any] = {}
    try:
        fs.arm_watchdog()
        s = fs.Session(
            args.out,
            camera=args.camera,
            lens_position=args.lens_position,
            roi=args.roi,
            note_prefix="capture_ptc:",
            notes=args.notes,
        )
        session.update(s.describe())
        fs.arm_watchdog()
        md0 = s.start()
        session["sensor_flip"] = s.cap.flip
        session["lens_position_metadata"] = md0.get("LensPosition")
        run = Run(s, args.out)
        if args.part == "flat":
            run_flat(run, args, plan)
        else:
            result["dark_level"] = run_dark(run, args, plan)
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
        if args.part == "flat":
            write_json(args.out / "plan.json", plan)
        write_json(
            args.out / "ptc.json",
            {"part": args.part, "frames": [] if run is None else run.frames, **result},
        )
    print(f"done: {args.out} ({session['seconds']} s, {len(run.frames)} frames)", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
