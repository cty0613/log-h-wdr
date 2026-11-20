#!/usr/bin/env python3
"""Pi RAW 촬영 (Phase 0): single | bracket | sensor_hdr.

비압축 RAW와 ISP main 출력을 같은 request에서 저장한다.

사용 예 (Pi에서, 또는 PC에서 scripts/pi.sh job 으로):
  python3 capture_raw.py --mode single  --exposures-us 10000 --gain 1.1228 --count 3 --out OUT
  python3 capture_raw.py --mode bracket --exposures-us 16000,4000,1000 --gain 1.1228 --count 2 \
      --out OUT --scene desk
  python3 capture_raw.py --mode sensor_hdr --exposures-us 10000 --gain 1.1228 --count 3 --out OUT

출력 (CLAUDE.md 5.1, 5.2):
  OUT/session.json
  single·sensor_hdr: OUT/NNNNNN.npy (uint16, right-shift 완료), NNNNNN.json, NNNNNN_isp.png
  bracket:           OUT/bracket_KKKK/{NNNNNN.npy,.json,_isp.png} (노출 지정 순서), bracket.json

동작 근거는 docs/pi_notes.md (비압축 포맷 요청, 실제 포맷에서 Bayer 순서, 5프레임 노출 지연,
최소 아날로그 게인 1.1228, white level 1023).
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

import rawutil as ru

REF_SIZE = (2304, 1296)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--mode", choices=("single", "bracket", "sensor_hdr"), required=True)
    ap.add_argument(
        "--exposures-us",
        required=True,
        help="쉼표 구분 노출 시간(µs). single/sensor_hdr는 1개, bracket은 2~3개 (저장 순서 그대로)",
    )
    ap.add_argument(
        "--gain", type=float, required=True, help="아날로그 게인 (IMX708 범위 1.1228~16)"
    )
    ap.add_argument("--count", type=int, default=1, help="프레임 수 (bracket: 세트 수)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--scene", default="", help="session.json에 기록할 장면 이름")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--size", default=f"{REF_SIZE[0]}x{REF_SIZE[1]}")
    ap.add_argument(
        "--lens-position", type=float, default=None, help="초점 고정 (dioptre). 생략 시 현재 위치"
    )
    ap.add_argument(
        "--colour-gains",
        default=None,
        help="r,b WB 게인 고정. 생략 시 AWB를 잠깐 돌려 수렴값으로 고정",
    )
    ap.add_argument("--awb-warmup-frames", type=int, default=30)
    ap.add_argument("--tolerance", type=float, default=0.02, help="노출 허용 상대오차 (기본 2%%)")
    ap.add_argument(
        "--max-wait-frames",
        type=int,
        default=15,
        help="노출 변경 후 일치하는 프레임을 기다리는 최대 프레임 수 (실측 지연 5)",
    )
    ap.add_argument(
        "--max-retries", type=int, default=3, help="브래킷 세트 재촬영 최대 횟수 (노출비 불일치 시)"
    )
    ap.add_argument("--no-isp", action="store_true", help="ISP main PNG 저장 생략")
    ap.add_argument("--notes", default="")
    args = ap.parse_args(argv)

    args.exposures = [float(x) for x in args.exposures_us.split(",") if x.strip()]
    n = len(args.exposures)
    if args.mode == "bracket" and not 2 <= n <= 3:
        ap.error("bracket 모드는 노출 2~3개가 필요합니다")
    if args.mode != "bracket" and n != 1:
        ap.error(f"{args.mode} 모드는 노출 1개만 받습니다")
    if args.mode == "bracket" and len(set(args.exposures)) != n:
        ap.error("bracket 노출 값이 중복됩니다")
    w, h = (int(v) for v in args.size.lower().split("x"))
    args.size_wh = (w, h)
    if args.colour_gains:
        r, b = (float(v) for v in args.colour_gains.split(","))
        args.colour_gains_rb = (r, b)
    else:
        args.colour_gains_rb = None
    if args.count < 1:
        ap.error("--count >= 1")
    return args


# ---------------------------------------------------------------------------------------------
class Capturer:
    def __init__(self, args: argparse.Namespace) -> None:
        from picamera2 import Picamera2

        self.args = args
        self.picam2 = Picamera2(args.camera)
        modes = [dict(m) for m in self.picam2.sensor_modes]
        cand = [m for m in modes if tuple(m["size"]) == args.size_wh]
        if not cand:
            raise SystemExit(f"sensor mode {args.size_wh} not found: {[m['size'] for m in modes]}")
        self.mode = max(cand, key=lambda m: m["bit_depth"])
        self.bit_depth = int(self.mode["bit_depth"])
        self.size = tuple(self.mode["size"])
        self.controls = self._base_controls(args.exposures[0])

        cfg = self.picam2.create_still_configuration(
            sensor={"output_size": self.size, "bit_depth": self.bit_depth},
            main={"size": self.size, "format": "RGB888"},
            raw={"size": self.size, "format": self.mode["unpacked"]},
            buffer_count=2,
            controls=self.controls,
        )
        self.picam2.configure(cfg)
        self.config = self.picam2.camera_configuration()
        self.raw_format = str(self.config["raw"]["format"])
        if not ru.is_unpacked16(self.raw_format):
            raise SystemExit(f"raw format is not unpacked 16bit: {self.raw_format}")
        self.bayer_order = ru.bayer_order_from_format(self.raw_format)
        self.dropped_total = 0
        self.flip: dict[str, Any] = {}

    def _base_controls(self, exposure_us: float) -> dict[str, Any]:
        c: dict[str, Any] = {
            "ExposureTimeMode": 1,  # Manual
            "AnalogueGainMode": 1,  # Manual
            "ExposureTime": round(exposure_us),
            "AnalogueGain": self.args.gain,
            "AfMode": 0,  # Manual
            "HdrMode": 0,  # ISP HDR 끔 (센서 HDR과 별개)
        }
        if self.args.lens_position is not None:
            c["LensPosition"] = self.args.lens_position
        if self.args.colour_gains_rb:
            c["AwbEnable"] = False
            c["ColourGains"] = self.args.colour_gains_rb
        return c

    def start(self) -> dict[str, Any]:
        self.picam2.start()
        md: dict[str, Any] = {}
        if self.args.colour_gains_rb is None:
            # AWB만 잠깐 수렴시킨 뒤 그 값으로 고정 (노출·게인은 처음부터 수동)
            self.picam2.set_controls({"AwbEnable": True})
            for _ in range(self.args.awb_warmup_frames):
                md = self.picam2.capture_metadata()
            gains = tuple(md["ColourGains"])
            self.controls.update({"AwbEnable": False, "ColourGains": gains})
            self.picam2.set_controls({"AwbEnable": False, "ColourGains": gains})
        for _ in range(self.args.max_wait_frames):
            md = self.picam2.capture_metadata()
        if not ru.gain_matches(md.get("AnalogueGain"), self.args.gain):
            raise SystemExit(
                f"AnalogueGain mismatch: requested {self.args.gain}, "
                f"actual {md.get('AnalogueGain')}"
                " (IMX708 range 1.1228~16, docs/pi_notes.md 7절)"
            )
        return md

    def set_exposure(self, exposure_us: float) -> None:
        self.controls["ExposureTime"] = round(exposure_us)
        self.picam2.set_controls({"ExposureTime": self.controls["ExposureTime"]})

    def grab(self, exposure_us: float, out_dir: Path, index: int, notes: str) -> dict[str, Any]:
        """노출이 exposure_us와 tol 안에 드는 프레임까지 request를 버리고, 맞는 프레임을 저장."""
        dropped = []
        for _ in range(self.args.max_wait_frames):
            req = self.picam2.capture_request()
            try:
                md = dict(req.get_metadata())
                if not ru.exposure_matches(
                    md.get("ExposureTime"), exposure_us, self.args.tolerance
                ):
                    dropped.append(md.get("ExposureTime"))
                    continue
                raw = ru.raw_to_uint16(req.make_array("raw"), self.size[0], self.bit_depth)
                stem = f"{index:06d}"
                np.save(out_dir / f"{stem}.npy", raw)
                if not self.args.no_isp:
                    req.save("main", str(out_dir / f"{stem}_isp.png"))
            finally:
                req.release()
            fj = ru.frame_json(
                frame_index=index,
                md=md,
                size=self.size,
                bit_depth=self.bit_depth,
                hdr=self.args.mode == "sensor_hdr",
                bayer_order=self.bayer_order,
                exposure_requested_us=exposure_us,
                raw_format=self.raw_format,
                notes=notes,
            )
            fj["dropped_frames_before"] = len(dropped)
            fj["sensor_flip"] = self.flip
            (out_dir / f"{stem}.json").write_text(json.dumps(fj, indent=2, ensure_ascii=False))
            self.dropped_total += len(dropped)
            return fj
        raise TimeoutError(
            f"no frame with exposure {exposure_us} us within {self.args.max_wait_frames} frames; "
            f"seen {dropped}"
        )

    def close(self) -> None:
        try:
            self.picam2.stop()
        finally:
            self.picam2.close()


# ---------------------------------------------------------------------------------------------
def run_single(cap: Capturer, out: Path) -> dict[str, Any]:
    t = cap.args.exposures[0]
    frames = [cap.grab(t, out, i, cap.args.notes) for i in range(cap.args.count)]
    exps = [f["exposure_time_us"] for f in frames]
    return {"frames": len(frames), "exposure_time_us_actual": exps}


def run_bracket(cap: Capturer, out: Path) -> dict[str, Any]:
    args = cap.args
    sets = []
    for k in range(args.count):
        bdir = out / f"bracket_{k:04d}"
        attempts = []
        for attempt in range(args.max_retries + 1):
            if bdir.exists():
                for f in bdir.iterdir():
                    f.unlink()
            bdir.mkdir(exist_ok=True)
            frames = []
            for i, t in enumerate(args.exposures):
                cap.set_exposure(t)
                frames.append(cap.grab(t, bdir, i, args.notes))
            actual = [f["exposure_time_us"] for f in frames]
            summary = ru.bracket_ratio_summary(args.exposures, actual, args.tolerance)
            attempts.append({"attempt": attempt, **summary})
            if summary["ratio_within_tolerance"]:
                break
            print(
                f"[bracket {k}] ratio error {summary['max_abs_ratio_rel_error']:.4f}, retry",
                flush=True,
            )
        bj = {
            "bracket_index": k,
            "exposure_time_us_requested": args.exposures,
            "exposure_time_us_actual": actual,
            "analogue_gain": [f["analogue_gain"] for f in frames],
            "timestamps_ns": [f["timestamp_ns"] for f in frames],
            "files": [f"{i:06d}.npy" for i in range(len(frames))],
            **summary,
            "attempts": attempts,
            "retries": len(attempts) - 1,
        }
        (bdir / "bracket.json").write_text(json.dumps(ru.jsonable(bj), indent=2))
        sets.append(bj)
        print(
            f"[bracket {k}] actual {actual} "
            f"ratios {[round(r, 4) for r in summary['ratios_actual']]}"
            f" max err {summary['max_abs_ratio_rel_error'] * 100:.3f}%"
            f" {'OK' if summary['ratio_within_tolerance'] else 'FAIL'}",
            flush=True,
        )
    return {
        "sets": len(sets),
        "all_ratios_within_tolerance": all(s["ratio_within_tolerance"] for s in sets),
        "max_abs_ratio_rel_error": max(s["max_abs_ratio_rel_error"] for s in sets),
        "total_retries": sum(s["retries"] for s in sets),
    }


def sensor_flip(model: str | None) -> dict[str, Any]:
    """센서 subdev의 실제 flip 상태를 v4l2-ctl로 읽는다 (LSC 테이블 방향 결정에 필요).

    Picamera2 transform이 identity여도 Rotation 180 카메라는 센서 flip이 켜져 있다 (pi_notes 3절).
    """
    import glob
    import subprocess

    for name_file in sorted(glob.glob("/sys/class/video4linux/v4l-subdev*/name")):
        name = Path(name_file).read_text().strip()
        if model and name == model:
            dev = "/dev/" + Path(name_file).parent.name
            p = subprocess.run(
                [
                    "v4l2-ctl",
                    "-d",
                    dev,
                    "--get-ctrl",
                    "horizontal_flip",
                    "--get-ctrl",
                    "vertical_flip",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            vals = dict(
                (k.strip(), v.strip() == "1")
                for k, v in (ln.split(":", 1) for ln in p.stdout.splitlines() if ":" in ln)
            )
            return {
                "horizontal": vals.get("horizontal_flip"),
                "vertical": vals.get("vertical_flip"),
                "source": f"v4l2-ctl {dev}",
            }
    return {"horizontal": None, "vertical": None, "source": f"subdev for {model!r} not found"}


def versions() -> dict[str, Any]:
    import subprocess

    out: dict[str, Any] = {"python": sys.version}
    try:
        p = subprocess.run(
            [
                "dpkg-query",
                "-W",
                "-f=${Package} ${Version}\\n",
                "python3-picamera2",
                "libcamera0.*",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        out["dpkg"] = p.stdout.strip().splitlines()
    except OSError as e:
        out["dpkg"] = repr(e)
    return out


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if any(args.out.iterdir()):
        raise SystemExit(f"output directory is not empty: {args.out}")

    hdr_dev = None
    if args.mode == "sensor_hdr":
        from picamera2.devices.imx708 import IMX708

        hdr_dev = IMX708
        with IMX708(args.camera) as dev:
            dev.set_sensor_hdr_mode(True)

    session: dict[str, Any] = {
        "mode": args.mode,
        "scene": args.scene,
        "argv": sys.argv,
        "started": dt.datetime.now().astimezone().isoformat(),
        "versions": versions(),
    }
    status = 1
    t0 = time.monotonic()
    cap = None
    try:
        cap = Capturer(args)
        session.update(
            {
                "camera_properties": dict(cap.picam2.camera_properties),
                "sensor_mode": cap.mode,
                "raw_format": cap.raw_format,
                "bayer_order": cap.bayer_order,
                "camera_configuration": cap.config,
            }
        )
        md0 = cap.start()
        cap.flip = sensor_flip(session["camera_properties"].get("Model"))
        session["sensor_flip"] = cap.flip
        session["controls"] = dict(cap.controls)
        session["locked_colour_gains"] = cap.controls.get("ColourGains")
        session["lens_position_metadata"] = md0.get("LensPosition")
        result = run_bracket(cap, args.out) if args.mode == "bracket" else run_single(cap, args.out)
        session["result"] = result
        session["dropped_frames_total"] = cap.dropped_total
        status = 0 if result.get("all_ratios_within_tolerance", True) else 3
    except Exception as e:
        session["error"] = repr(e)
        raise
    finally:
        if cap is not None:
            cap.close()
        if hdr_dev is not None:
            with hdr_dev(args.camera) as dev:
                dev.set_sensor_hdr_mode(False)
            session["sensor_hdr_reset"] = True
        session["finished"] = dt.datetime.now().astimezone().isoformat()
        session["seconds"] = round(time.monotonic() - t0, 3)
        session["exit_status"] = status
        (args.out / "session.json").write_text(
            json.dumps(ru.jsonable(session), indent=2, ensure_ascii=False)
        )
    print(f"session: {args.out / 'session.json'} (status {status})", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(main())
