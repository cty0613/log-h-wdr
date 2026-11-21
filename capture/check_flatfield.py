#!/usr/bin/env python3
"""Phase 1 플랫필드 셋업 확인 촬영 (PTC 본촬영 전).

광원·확산판 배치가 PTC에 쓸 만한지 확인할 프레임을 찍는다. 판정은 PC에서
`python -m cctvlog.sensor.flatfield_check DIR`로 한다 (여기서는 빠른 요약만 출력).

단계:
  1. sweep    게인 {최소, 16}별 노출 스윕 1장씩 → 포화 노출 추정, 선형성
  2. flicker  게인 16, 노출 100 µs(짧음)와 기준 노출(약 50% 신호) 각 N장
              → 롤링 셔터 가로 줄무늬, 프레임 간 평균 변동
  3. stability 최소 게인, 약 50% 신호, 일정 간격 N장 → 밝기 드리프트

사용 예 (PC에서):
  scripts/pi.sh job ffcheck_YYYYMMDD_HHMM check_flatfield.py
출력:
  OUT/session.json, OUT/check.json (단계별 파일 목록·ROI 평균)
  OUT/{sweep_g1,sweep_g16,flicker,stability}/NNNNNN.npy + .json (CLAUDE.md 5.2)
근거: docs/phase1_equipment.md, docs/pi_notes.md (최소 게인 1.1228, 노출 지연 5프레임).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

import capture_raw as cr
import flatsession as fs
import rawutil as ru

GAIN_MIN = fs.GAIN_MIN
GAIN_MAX = fs.GAIN_MAX
SWEEP_G1_US = (500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000)
SWEEP_G16_US = (50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
FLICKER_SHORT_US = 100
ROI = 512  # 중앙 ROI 한 변 (화소, 짝수)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    fs.add_common_args(ap)
    ap.add_argument("--flicker-frames", type=int, default=4)
    ap.add_argument("--stability-frames", type=int, default=15)
    ap.add_argument("--stability-interval-s", type=float, default=2.0)
    return ap.parse_args(argv)


def sweep(
    s: fs.Session, out: Path, gain: float, exposures: tuple[int, ...], name: str
) -> list[dict]:
    rows = []
    for i, t in enumerate(exposures):
        s.set(gain, t)
        r = s.grab(out / name, i, name)
        rows.append(r)
        print(
            f"[{name}] gain {r['gain']:.4f} exp {r['exposure_time_us']:>7} us  "
            f"ROI mean max ch {r['roi_max_channel_mean']:.3f}",
            flush=True,
        )
        if r["roi_max_channel_mean"] > 0.98:  # 포화 이후는 더 찍지 않음
            break
    return rows


def exposure_for_level(rows: list[dict], level: float) -> float | None:
    """비포화 점(0.02~0.8)의 원점 통과 선형 피팅으로 최대 채널이 level이 되는 노출."""
    pts = [(r["exposure_time_us"], r["roi_max_channel_mean"]) for r in rows]
    pts = [(t, m) for t, m in pts if 0.02 <= m <= 0.8]
    if not pts:
        return None
    t = np.array([p[0] for p in pts], float)
    m = np.array([p[1] for p in pts], float)
    slope = float((t * m).sum() / (t * t).sum())
    return level / slope if slope > 0 else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    fs.ensure_empty(args.out)
    session: dict[str, Any] = {
        "script": "check_flatfield.py",
        "argv": sys.argv,
        "started": dt.datetime.now().astimezone().isoformat(),
        "versions": cr.versions(),
    }
    check: dict[str, Any] = {}
    t0 = time.monotonic()
    s = None
    status = 1
    try:
        fs.arm_watchdog()
        s = fs.Session(
            args.out,
            camera=args.camera,
            lens_position=args.lens_position,
            roi=ROI,
            note_prefix="check_flatfield:",
            notes=args.notes,
            initial_exposure_us=SWEEP_G1_US[0],
        )
        session.update(s.describe())
        fs.arm_watchdog()
        md0 = s.start()
        session["sensor_flip"] = s.cap.flip
        session["lens_position_metadata"] = md0.get("LensPosition")

        # 1. 노출 스윕
        check["sweep_g1"] = sweep(s, args.out, GAIN_MIN, SWEEP_G1_US, "sweep_g1")
        check["sweep_g16"] = sweep(s, args.out, GAIN_MAX, SWEEP_G16_US, "sweep_g16")
        t50_g1 = exposure_for_level(check["sweep_g1"], 0.5)
        t50_g16 = exposure_for_level(check["sweep_g16"], 0.5)
        check["t50_us"] = {"g1": t50_g1, "g16": t50_g16}
        print(f"[sweep] 50% level exposure: g1 {t50_g1} us, g16 {t50_g16} us", flush=True)

        # 2. 깜빡임: 게인 16, 100 µs vs 기준 노출, 최소 게인 기준 노출
        flicker_sets = [("g16_short", GAIN_MAX, FLICKER_SHORT_US)]
        if t50_g16:
            flicker_sets.append(("g16_ref", GAIN_MAX, max(t50_g16, 200.0)))
        if t50_g1:
            flicker_sets.append(("g1_ref", GAIN_MIN, t50_g1))
        check["flicker"] = {}
        idx = 0
        for name, g, t in flicker_sets:
            s.set(g, t)
            rows = []
            for _ in range(args.flicker_frames):
                rows.append(s.grab(args.out / "flicker", idx, f"flicker:{name}"))
                idx += 1
            check["flicker"][name] = rows
            ms = [r["roi_max_channel_mean"] for r in rows]
            print(
                f"[flicker {name}] exp {rows[0]['exposure_time_us']} us  ROI means "
                + " ".join(f"{m:.4f}" for m in ms),
                flush=True,
            )

        # 3. 안정성: 최소 게인, 약 50% 신호
        if t50_g1:
            s.set(GAIN_MIN, t50_g1)
            rows = []
            for i in range(args.stability_frames):
                if i:
                    time.sleep(args.stability_interval_s)
                rows.append(s.grab(args.out / "stability", i, "stability"))
            check["stability"] = rows
            ms = np.array([r["roi_max_channel_mean"] for r in rows])
            print(
                f"[stability] {len(rows)} frames, mean {ms.mean():.4f}, "
                f"range {(ms.max() - ms.min()) / ms.mean() * 100:.3f}%",
                flush=True,
            )
        status = 0
        fs.disarm_watchdog()
    except Exception as e:
        session["error"] = repr(e)
        raise
    finally:
        if s is not None:
            s.close()
        session["finished"] = dt.datetime.now().astimezone().isoformat()
        session["seconds"] = round(time.monotonic() - t0, 3)
        session["exit_status"] = status
        (args.out / "session.json").write_text(
            json.dumps(ru.jsonable(session), indent=2, ensure_ascii=False)
        )
        (args.out / "check.json").write_text(json.dumps(ru.jsonable(check), indent=2))
    print(f"done: {args.out} ({session['seconds']} s)", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
