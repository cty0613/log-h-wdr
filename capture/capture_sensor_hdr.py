#!/usr/bin/env python3
"""Phase 1 센서 HDR 모드 특성 측정: 노출 대비 평균의 선형성과 내부 합성 경계 위치.

IMX708 센서 HDR(wide_dynamic_range)은 센서 안에서 합성되는 블랙박스다(CLAUDE.md 2절). 여기서는
플랫필드에서 노출을 촘촘히 바꿔 가며 쌍을 찍고, 응답 곡선(평균 vs 노출)의 꺾임과 분산 점프로
합성 경계를 찾는다. 분석: python experiments/p1_sensor_hdr.py configs/p1_sensor_hdr.yaml

  flat  --exposures-us 또는 --sweep START,STOP,N (로그 간격). 가장 밝은 채널이 포화되면
        --stop-after-sat 단계 더 찍고 멈춘다.
  dark  렌즈를 가린 뒤 --exposures-us 노출마다 --dark-frames장.

센서 HDR은 촬영 전에 켜고, 성공·실패와 관계없이 끝날 때 끈다(pi_notes 2절).
출력: OUT/session.json, OUT/ptc.json (capture_ptc.py와 같은 색인 형식, gain_label "hdr"),
      OUT/{flat,dark}/hdr/NNNNNN.npy + .json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from typing import Any

import numpy as np

import capture_ptc as ptc
import capture_raw as cr
import flatsession as fs
import rawutil as ru

LABEL = "hdr"
SAT_LEVEL = 0.98
# 센서 HDR 모드의 노출 단위는 약 71.5 µs (1000 → 858, 10000 → 9873 µs 실측).
# 요청과 실제가 최대 한 단위 어긋나므로 허용치를 그만큼 키우고, 스윕 단계는 그 2배 이상 띄운다.
HDR_LINE_US = 71.5
HDR_ABS_TOL_US = 80.0


def sweep_exposures(start: float, stop: float, n: int, tol: float, abs_tol: float) -> list[float]:
    """로그 간격 노출. 앞 단계와 구분되지 않는 값(허용치 + 한 단위 이내)은 뺀다."""
    out: list[float] = []
    for t in np.geomspace(start, stop, n):
        t = float(round(t))
        if not out or abs(t - out[-1]) > max(tol * t, abs_tol) + HDR_LINE_US:
            out.append(t)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    fs.add_common_args(ap)
    ap.add_argument("--part", choices=("flat", "dark"), required=True)
    ap.add_argument("--exposures-us", default=None, help="쉼표 구분 노출(µs)")
    ap.add_argument("--sweep", default=None, help="START,STOP,N 로그 간격 노출 스윕")
    ap.add_argument("--gain", type=float, default=fs.GAIN_MIN)
    ap.add_argument("--pairs", type=int, default=2)
    ap.add_argument("--dark-frames", type=int, default=10)
    ap.add_argument("--stop-after-sat", type=int, default=2, help="포화 후 더 찍을 단계 수")
    ap.add_argument("--roi", type=int, default=ptc.ROI)
    ap.add_argument("--no-hdr", action="store_true", help="비교용: 센서 HDR을 켜지 않음")
    ap.add_argument("--abs-tol-us", type=float, default=None, help="노출 일치 절대 허용치")
    args = ap.parse_args(argv)
    if args.abs_tol_us is None:
        args.abs_tol_us = fs.EXPOSURE_ABS_TOL_US if args.no_hdr else HDR_ABS_TOL_US
    if (args.exposures_us is None) == (args.sweep is None):
        ap.error("give exactly one of --exposures-us or --sweep")
    if args.sweep:
        a, b, n = args.sweep.split(",")
        args.exposures = sweep_exposures(float(a), float(b), int(n), 0.02, HDR_ABS_TOL_US)
    else:
        args.exposures = [float(v) for v in args.exposures_us.split(",") if v.strip()]
    return args


def set_hdr(camera: int, on: bool) -> None:
    from picamera2.devices.imx708 import IMX708

    with IMX708(camera) as dev:
        dev.set_sensor_hdr_mode(on)


def run_flat(run: ptc.Run, args: argparse.Namespace) -> None:
    after_sat = None
    prev_actual: float | None = None
    for si, t in enumerate(args.exposures):
        run.s.set(args.gain, t)
        rows = []
        try:
            actual = run.s.settle()
            if actual == prev_actual:  # 이전 단계와 같은 실제 노출 → 건너뜀
                print(f"[flat {si:02d}] exp req {t:.0f} → {actual:.0f} us (same as previous)")
                continue
            prev_actual = actual
            for p in range(args.pairs):
                for f in (0, 1):
                    rows.append(
                        run.grab(
                            f"flat/{LABEL}",
                            f"sensor_hdr:step{si:02d}:pair{p}:{f}",
                            part="flat",
                            gain_label=LABEL,
                            step=si,
                            level=None,
                            pair=p,
                            frame=f,
                        )
                    )
        except TimeoutError as e:  # 모드가 받아들이지 않는 노출: 기록하고 스윕 종료
            print(f"[flat {si:02d}] exp {t:.0f} us not applied: {e}", flush=True)
            run.notes.append({"step": si, "exposure_us_requested": t, "error": str(e)})
            break
        m = max(r["roi_max_channel_mean"] for r in rows)
        print(
            f"[flat {si:02d}] exp req {t:>9.0f} act {rows[0]['exposure_time_us']:>9} us  "
            f"gain {rows[0]['gain']:.4f}  ROI max ch "
            + " ".join(f"{r['roi_max_channel_mean']:.4f}" for r in rows),
            flush=True,
        )
        if m >= SAT_LEVEL and after_sat is None:
            after_sat = 0
        elif after_sat is not None:
            after_sat += 1
        if after_sat is not None and after_sat >= args.stop_after_sat:
            break


def run_dark(run: ptc.Run, args: argparse.Namespace) -> None:
    for t in args.exposures:
        run.s.set(args.gain, t)
        run.s.settle()
        rows = [
            run.grab(
                f"dark/{LABEL}",
                f"sensor_hdr:dark:{t:.0f}us",
                part="dark",
                gain_label=LABEL,
                exposure_us_requested=t,
            )
            for _ in range(args.dark_frames)
        ]
        m = max(rows[0]["roi_means"])
        print(f"[dark] exp {rows[0]['exposure_time_us']:>9} us  ROI max ch {m:+.5f}", flush=True)
        if m > ptc.LEAK_ABORT:
            raise SystemExit(f"light leak: dark ROI mean {m:.4f} at {t:.0f} us")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fs.ensure_empty(args.out)
    session: dict[str, Any] = {
        "script": "capture_sensor_hdr.py",
        "part": args.part,
        "argv": sys.argv,
        "sensor_hdr": not args.no_hdr,
        "exposures_planned_us": args.exposures,
        "started": dt.datetime.now().astimezone().isoformat(),
        "versions": cr.versions(),
    }
    t0 = time.monotonic()
    s: fs.Session | None = None
    run: ptc.Run | None = None
    status = 1
    try:
        if not args.no_hdr:
            set_hdr(args.camera, True)
            session["sensor_hdr_set"] = True
        fs.arm_watchdog()
        s = fs.Session(
            args.out,
            camera=args.camera,
            lens_position=args.lens_position,
            roi=args.roi,
            note_prefix="capture_sensor_hdr:",
            notes=args.notes,
            initial_exposure_us=args.exposures[0],
            abs_tol_us=args.abs_tol_us,
        )
        session.update(s.describe())
        fs.arm_watchdog()
        md0 = s.start()
        session["sensor_flip"] = s.cap.flip
        session["start_metadata"] = md0
        run = ptc.Run(s, args.out)
        if args.part == "flat":
            run_flat(run, args)
        else:
            run_dark(run, args)
        status = 0
        fs.disarm_watchdog()
    except BaseException as e:
        session["error"] = repr(e)
        raise
    finally:
        if s is not None:
            s.close()
        if not args.no_hdr:
            try:
                set_hdr(args.camera, False)
                session["sensor_hdr_reset"] = True
            except Exception as e:  # 복구 실패는 반드시 기록 (다음 촬영 전에 재부팅 필요)
                session["sensor_hdr_reset"] = False
                session["sensor_hdr_reset_error"] = repr(e)
                print(f"WARNING: sensor HDR reset failed: {e!r} — reboot the Pi", flush=True)
        session["finished"] = dt.datetime.now().astimezone().isoformat()
        session["seconds"] = round(time.monotonic() - t0, 3)
        session["exit_status"] = status
        (args.out / "session.json").write_text(
            json.dumps(ru.jsonable(session), indent=2, ensure_ascii=False)
        )
        (args.out / "ptc.json").write_text(
            json.dumps(
                ru.jsonable(
                    {
                        "part": args.part,
                        "frames": [] if run is None else run.frames,
                        "notes": [] if run is None else run.notes,
                    }
                ),
                indent=2,
            )
        )
    print(f"done: {args.out} ({session['seconds']} s, {len(run.frames)} frames)", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
