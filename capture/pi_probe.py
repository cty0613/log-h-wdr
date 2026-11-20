#!/usr/bin/env python3
"""Phase 0 Pi 환경 확인 스크립트 (Raspberry Pi 5 + Camera Module 3 / IMX708).

capture_raw.py·tuning_import.py를 만들기 전에, 추측 대신 실제 동작을 기록한다 (CLAUDE.md 2절, 8절).
각 단계는 독립적으로 실행되며 실패해도 다음 단계로 넘어가고, 오류는 보고서에 남긴다.

확인 항목
  versions        OS / libcamera / rpicam-apps / Picamera2 버전
  camera_info     연결된 카메라, camera_properties
  sensor_modes    Picamera2.sensor_modes (일반 모드)
  tuning_files    IMX708 튜닝 JSON 위치, CCM·ALSC 키 구조 (파일 사본 저장)
  raw_formats     unpacked(비압축 16bit) RAW 요청 방법, MSB 정렬(LSB 0 패딩) 확인
  raw_main        한 request에서 raw + main 동시 획득, 샘플 프레임 저장
  exposure_delay  노출 변경 후 메타데이터에 반영되기까지의 프레임 수
  controls        camera_controls (수동 노출·게인·초점 제어 이름과 범위)
  manual_gain     요청 AnalogueGain {1,2,4,8,16}이 메타데이터에 그대로 반영되는지 (제어 방식별)
  white_level     포화 화소의 RAW 값 (화면 일부에 밝은 광원이 들어오게 하고 실행)
  sensor_hdr      (--sensor-hdr) 센서 HDR 모드 전환과 그때의 sensor_modes

사용법 (Pi에서):
  python3 pi_probe.py --out ~/pi_probe --sensor-hdr
  python3 pi_probe.py --out ~/pi_probe2 --steps tuning_files,controls,manual_gain,white_level
출력: <out>/pi_probe_report.json, <out>/tuning/*.json,
      <out>/sample/000000.{npy,json}, <out>/sample/000000_isp.png
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import platform
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np

REF_SIZE = (2304, 1296)  # 기준 해상도 (CLAUDE.md 2절)
TUNING_GLOBS = [
    "/usr/share/libcamera/ipa/rpi/*/imx708*.json",
    "/usr/local/share/libcamera/ipa/rpi/*/imx708*.json",
]


# ---------------------------------------------------------------------------------------------
# 공통
# ---------------------------------------------------------------------------------------------
def jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    return repr(obj)


def sh(cmd: list[str]) -> dict[str, Any]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"cmd": cmd, "error": repr(e)}
    return {"cmd": cmd, "rc": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}


class Report:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.path = out / "pi_probe_report.json"
        self.data: dict[str, Any] = {
            "created": dt.datetime.now().astimezone().isoformat(),
            "argv": sys.argv,
            "steps": {},
        }

    def step(self, name: str, fn, *args) -> Any:
        t0 = time.monotonic()
        print(f"[....] {name}", flush=True)
        try:
            result = fn(*args)
            entry = {"ok": True, "result": jsonable(result)}
        except Exception as e:
            result = None
            entry = {"ok": False, "error": repr(e), "traceback": traceback.format_exc()}
        entry["seconds"] = round(time.monotonic() - t0, 3)
        self.data["steps"][name] = entry
        self.save()
        print(f"[{'OK  ' if entry['ok'] else 'FAIL'}] {name} ({entry['seconds']} s)", flush=True)
        if not entry["ok"]:
            print("       " + entry["error"], flush=True)
        return result

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n")


def bayer_order_from_format(fmt: str) -> str | None:
    # 'SRGGB10_CSI2P', 'SBGGR16', 'BGGR_PISP_COMP1' 등
    for order in ("RGGB", "GRBG", "GBRG", "BGGR"):
        if order in fmt:
            return order
    return None


def pick_mode(modes: list[dict[str, Any]]) -> dict[str, Any]:
    exact = [m for m in modes if tuple(m.get("size", ())) == REF_SIZE]
    if exact:
        return max(exact, key=lambda m: m.get("bit_depth", 0))
    return max(modes, key=lambda m: m["size"][0] * m["size"][1])


# ---------------------------------------------------------------------------------------------
# 단계
# ---------------------------------------------------------------------------------------------
def step_versions() -> dict[str, Any]:
    out: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "os_release": Path("/etc/os-release").read_text()
        if Path("/etc/os-release").exists()
        else None,
        "device_model": Path("/proc/device-tree/model").read_text().strip("\x00\n")
        if Path("/proc/device-tree/model").exists()
        else None,
        "rpicam_hello": sh(["rpicam-hello", "--version"]),
        "dpkg": sh(
            [
                "dpkg-query",
                "-W",
                "-f=${Package} ${Version}\\n",
                "libcamera*",
                "python3-picamera2",
                "rpicam-apps*",
            ]
        ),
        "numpy": np.__version__,
    }
    try:
        from importlib import metadata

        out["picamera2_pip_metadata"] = metadata.version("picamera2")
    except Exception as e:
        out["picamera2_pip_metadata"] = repr(e)
    return out


def step_camera_info(cam: int) -> dict[str, Any]:
    from picamera2 import Picamera2

    info = Picamera2.global_camera_info()
    with Picamera2(cam) as picam2:
        props = dict(picam2.camera_properties)
    return {"global_camera_info": info, "camera_properties": props}


def step_sensor_modes(cam: int) -> list[dict[str, Any]]:
    from picamera2 import Picamera2

    with Picamera2(cam) as picam2:
        return [dict(m) for m in picam2.sensor_modes]


def step_tuning_files(out: Path, model: str | None) -> dict[str, Any]:
    files = sorted({p for pat in TUNING_GLOBS for p in glob.glob(pat)})
    dst = out / "tuning"
    dst.mkdir(exist_ok=True)
    summary: dict[str, Any] = {"searched": TUNING_GLOBS, "files": {}}
    for f in files:
        p = Path(f)
        target = p.parent.name  # pisp(Pi 5) / vc4(Pi 4 이하)
        shutil.copy2(p, dst / f"{target}__{p.name}")
        info: dict[str, Any] = {"target_dir": target}
        try:
            tj = json.loads(p.read_text())
            info["version"] = tj.get("version")
            info["target"] = tj.get("target")
            algos = tj.get("algorithms", [])
            names = [next(iter(a)) for a in algos if isinstance(a, dict) and a]
            info["algorithms"] = names
            for a in algos:
                name, body = next(iter(a.items()))
                if name == "rpi.ccm":
                    ccms = body.get("ccms", [])
                    info["ccm"] = {
                        "keys": list(body),
                        "count": len(ccms),
                        "entry_keys": list(ccms[0]) if ccms else [],
                        "cts": [c.get("ct") for c in ccms],
                    }
                elif name == "rpi.alsc":
                    tables = {k: v for k, v in body.items() if isinstance(v, list)}
                    info["alsc"] = {
                        "keys": list(body),
                        "list_lengths": {k: len(v) for k, v in tables.items()},
                        "first_table_entry_keys": {
                            k: list(v[0]) for k, v in tables.items() if v and isinstance(v[0], dict)
                        },
                        "first_table_lengths": {
                            k: {kk: len(vv) for kk, vv in v[0].items() if isinstance(vv, list)}
                            for k, v in tables.items()
                            if v and isinstance(v[0], dict)
                        },
                    }
                elif name == "rpi.black_level":
                    info["black_level"] = body
        except Exception as e:
            info["parse_error"] = repr(e)
        summary["files"][f] = info
    # libcamera는 센서 모델 이름(camera_properties["Model"])으로 튜닝 파일을 고른다
    summary["camera_model"] = model
    summary["expected_tuning_file"] = f"{model}.json" if model else None
    pisp = [f for f in files if Path(f).parent.name == "pisp" and Path(f).stem == model]
    summary["expected_tuning_path_pisp"] = pisp[0] if pisp else None
    if model:
        try:
            from picamera2 import Picamera2

            summary["picamera2_load_tuning_file_ok"] = bool(
                Picamera2.load_tuning_file(f"{model}.json")
            )
        except Exception as e:
            summary["picamera2_load_tuning_file_ok"] = repr(e)
    return summary


def _raw_stats(arr: np.ndarray, width: int, bit_depth: int) -> dict[str, Any]:
    out: dict[str, Any] = {"array_dtype": str(arr.dtype), "array_shape": list(arr.shape)}
    if arr.dtype == np.uint8 and arr.shape[1] >= 2 * width:
        v = arr.view(np.uint16)[:, :width]
    elif arr.dtype == np.uint16:
        v = arr[:, :width]
    else:
        out["note"] = "16bit로 해석할 수 없는 배열 (packed/compressed로 보임)"
        return out
    shift = 16 - bit_depth
    out["as_uint16_shape"] = list(v.shape)
    out["min"] = int(v.min())
    out["max"] = int(v.max())
    out["lsb_zero_fraction"] = float(np.mean((v & ((1 << shift) - 1)) == 0)) if shift > 0 else None
    out["max_after_shift"] = int(v.max() >> shift)
    out["mean_after_shift"] = float((v >> shift).mean())
    return out


def _configure(picam2, mode: dict[str, Any], raw_fmt: str | None, controls: dict[str, Any]):
    kwargs: dict[str, Any] = {
        "main": {"size": tuple(mode["size"]), "format": "RGB888"},
        "raw": {"size": tuple(mode["size"])} | ({"format": raw_fmt} if raw_fmt else {}),
        "buffer_count": 2,
        "controls": controls,
    }
    try:
        cfg = picam2.create_still_configuration(
            sensor={"output_size": tuple(mode["size"]), "bit_depth": mode["bit_depth"]}, **kwargs
        )
        used_sensor_arg = True
    except TypeError:
        cfg = picam2.create_still_configuration(**kwargs)
        used_sensor_arg = False
    picam2.configure(cfg)
    return used_sensor_arg


MANUAL = {"AeEnable": False, "AwbEnable": False, "ExposureTime": 10000, "AnalogueGain": 1.0}


def step_raw_formats(cam: int, modes: list[dict[str, Any]]) -> dict[str, Any]:
    from picamera2 import Picamera2

    mode = pick_mode(modes)
    order = bayer_order_from_format(str(mode.get("unpacked", mode.get("format", ""))))
    candidates: list[str | None] = [None, mode.get("unpacked"), mode.get("format")]
    if order:
        candidates.append(f"S{order}16")
    seen: set[Any] = set()
    results = []
    for fmt in candidates:
        if fmt in seen:
            continue
        seen.add(fmt)
        r: dict[str, Any] = {"requested_format": fmt}
        try:
            with Picamera2(cam) as picam2:
                r["used_sensor_arg"] = _configure(picam2, mode, fmt, MANUAL)
                conf = picam2.camera_configuration()
                r["actual_raw"] = dict(conf["raw"])
                r["actual_sensor"] = dict(conf.get("sensor", {}) or {})
                picam2.start()
                for _ in range(4):  # 제어값 안정화
                    picam2.capture_metadata()
                req = picam2.capture_request()
                try:
                    arr = req.make_array("raw")
                    r["metadata"] = dict(req.get_metadata())
                finally:
                    req.release()
                picam2.stop()
            r["stats"] = _raw_stats(arr, mode["size"][0], mode["bit_depth"])
            r["ok"] = True
        except Exception as e:
            r["ok"] = False
            r["error"] = repr(e)
        results.append(r)
    return {"mode": mode, "bayer_order_from_mode": order, "attempts": results}


def step_raw_main(cam: int, modes: list[dict[str, Any]], raw_fmt: str | None, out: Path):
    from picamera2 import Picamera2

    mode = pick_mode(modes)
    sample = out / "sample"
    sample.mkdir(exist_ok=True)
    frames = []
    with Picamera2(cam) as picam2:
        _configure(picam2, mode, raw_fmt, MANUAL)
        conf = picam2.camera_configuration()
        actual_fmt = str(conf["raw"]["format"])
        picam2.start()
        for _ in range(4):
            picam2.capture_metadata()
        for i in range(5):
            req = picam2.capture_request()
            try:
                raw = req.make_array("raw")
                main = req.make_array("main")
                md = dict(req.get_metadata())
                if i == 0:
                    if raw.dtype == np.uint8:
                        raw16 = raw.view(np.uint16)[:, : mode["size"][0]]
                    else:
                        raw16 = raw[:, : mode["size"][0]]
                    shifted = (raw16 >> (16 - mode["bit_depth"])).astype(np.uint16)
                    np.save(sample / "000000.npy", np.ascontiguousarray(shifted))
                    req.save("main", str(sample / "000000_isp.png"))
                    frame_json = {
                        "frame_index": 0,
                        "timestamp_ns": md.get("SensorTimestamp"),
                        "sensor_mode": {
                            "size": list(mode["size"]),
                            "bit_depth": mode["bit_depth"],
                            "hdr": False,
                        },
                        "bayer_order": bayer_order_from_format(actual_fmt),
                        "exposure_time_us_requested": MANUAL["ExposureTime"],
                        "exposure_time_us": md.get("ExposureTime"),
                        "analogue_gain": md.get("AnalogueGain"),
                        "digital_gain": md.get("DigitalGain"),
                        "colour_gains": md.get("ColourGains"),
                        "black_level": None,  # SensorBlackLevels 스케일 확인 후 결정
                        "white_level": None,
                        "colour_temperature": md.get("ColourTemperature"),
                        "sensor_temperature": md.get("SensorTemperature"),
                        "notes": f"pi_probe sample; raw format {actual_fmt}; "
                        f"SensorBlackLevels={md.get('SensorBlackLevels')}",
                    }
                    (sample / "000000.json").write_text(json.dumps(jsonable(frame_json), indent=2))
                frames.append(
                    {
                        "raw_shape": list(raw.shape),
                        "raw_dtype": str(raw.dtype),
                        "main_shape": list(main.shape),
                        "main_dtype": str(main.dtype),
                        "SensorTimestamp": md.get("SensorTimestamp"),
                        "ExposureTime": md.get("ExposureTime"),
                        "FrameDuration": md.get("FrameDuration"),
                    }
                )
            finally:
                req.release()
        picam2.stop()
    return {
        "raw_format": actual_fmt,
        "camera_configuration": conf,
        "frames": frames,
        "first_frame_metadata_keys": sorted(md.keys()),
    }


def step_exposure_delay(cam: int, modes: list[dict[str, Any]], raw_fmt: str | None):
    from picamera2 import Picamera2

    mode = pick_mode(modes)
    schedule = [5000, 20000, 5000, 40000, 10000]
    trials = []
    with Picamera2(cam) as picam2:
        _configure(picam2, mode, raw_fmt, {**MANUAL, "ExposureTime": schedule[0]})
        picam2.start()
        for _ in range(8):
            picam2.capture_metadata()
        for target in schedule[1:]:
            picam2.set_controls({"ExposureTime": target})
            seq = []
            for _ in range(12):
                md = picam2.capture_metadata()
                seq.append(
                    {
                        "ExposureTime": md.get("ExposureTime"),
                        "SensorTimestamp": md.get("SensorTimestamp"),
                    }
                )
            applied = next(
                (
                    i
                    for i, s in enumerate(seq)
                    if s["ExposureTime"] and abs(s["ExposureTime"] - target) / target < 0.02
                ),
                None,
            )
            trials.append(
                {"target_us": target, "frames_until_within_2pct": applied, "sequence": seq}
            )
        picam2.stop()
    return {"trials": trials}


def step_controls(cam: int) -> dict[str, Any]:
    """camera_controls: 제어 이름 → (min, max, default). 수동 노출·게인·초점 제어 이름 확인용."""
    from picamera2 import Picamera2

    with Picamera2(cam) as picam2:
        return {
            k: list(v) if isinstance(v, tuple) else v for k, v in picam2.camera_controls.items()
        }


GAIN_KEYS = ("AnalogueGain", "DigitalGain", "ExposureTime", "ColourGains", "AeState", "Lux")


def _settle_and_read(picam2, n_settle: int = 8, n_read: int = 3) -> list[dict[str, Any]]:
    for _ in range(n_settle):
        picam2.capture_metadata()
    return [
        {k: md.get(k) for k in GAIN_KEYS}
        for md in (picam2.capture_metadata() for _ in range(n_read))
    ]


def step_manual_gain(cam: int, modes: list[dict[str, Any]], raw_fmt: str | None):
    """요청한 AnalogueGain이 메타데이터에 그대로 나오는지, 제어 방식별로 확인한다.

    1차 결과: AeEnable=False + AnalogueGain=1.0 요청 → 메타데이터 1.1228.
    """
    from picamera2 import Picamera2

    mode = pick_mode(modes)
    with Picamera2(cam) as picam2:
        available = set(picam2.camera_controls)
    variants: list[tuple[str, str, dict[str, Any]]] = [
        ("ae_enable_false", "config", {"AeEnable": False, "AwbEnable": False}),
        ("ae_enable_false", "set_controls", {"AeEnable": False, "AwbEnable": False}),
    ]
    if {"ExposureTimeMode", "AnalogueGainMode"} <= available:
        manual_modes = {"ExposureTimeMode": 1, "AnalogueGainMode": 1, "AwbEnable": False}
        variants.append(("mode_controls", "config", manual_modes))
        variants.append(("mode_controls", "set_controls", manual_modes))
    results = []
    for gain in (1.0, 2.0, 4.0, 8.0, 16.0):
        for vname, how, base in variants:
            ctrl = {**base, "ExposureTime": 10000, "AnalogueGain": gain}
            r: dict[str, Any] = {
                "requested_gain": gain,
                "variant": vname,
                "apply": how,
                "controls": ctrl,
            }
            try:
                with Picamera2(cam) as picam2:
                    _configure(picam2, mode, raw_fmt, ctrl if how == "config" else {})
                    picam2.start()
                    if how == "set_controls":
                        picam2.set_controls(ctrl)
                    r["readings"] = _settle_and_read(picam2)
                    picam2.stop()
                ag = r["readings"][-1]["AnalogueGain"]
                r["gain_ratio_actual_over_requested"] = ag / gain if ag else None
                r["ok"] = True
            except Exception as e:
                r["ok"] = False
                r["error"] = repr(e)
            results.append(r)
    return {
        "available_mode_controls": sorted(
            available & {"AeEnable", "ExposureTimeMode", "AnalogueGainMode", "AwbEnable"}
        ),
        "results": results,
    }


def step_white_level(cam: int, modes: list[dict[str, Any]], raw_fmt: str | None):
    """포화 화소의 RAW 값을 확인한다. 화면 일부에 밝은 광원이 들어오게 하고 실행한다."""
    from picamera2 import Picamera2

    mode = pick_mode(modes)
    bd = mode["bit_depth"]
    results = []
    for exp_us, gain in ((20000, 4.0), (100000, 8.0), (400000, 16.0)):
        ctrl = {**MANUAL, "ExposureTime": exp_us, "AnalogueGain": gain}
        with Picamera2(cam) as picam2:
            _configure(picam2, mode, raw_fmt, ctrl)
            picam2.start()
            for _ in range(6):
                picam2.capture_metadata()
            req = picam2.capture_request()
            try:
                arr = req.make_array("raw")
                md = dict(req.get_metadata())
            finally:
                req.release()
            picam2.stop()
        if arr.dtype == np.uint8:
            v = arr.view(np.uint16)[:, : mode["size"][0]]
        else:
            v = arr[:, : mode["size"][0]]
        v = v >> (16 - bd)
        vals, counts = np.unique(v, return_counts=True)
        top = sorted(zip(vals.tolist(), counts.tolist(), strict=True))[-5:]
        results.append(
            {
                "exposure_us_requested": exp_us,
                "gain_requested": gain,
                "ExposureTime": md.get("ExposureTime"),
                "AnalogueGain": md.get("AnalogueGain"),
                "max": int(v.max()),
                "count_at_max": int((v == v.max()).sum()),
                "fraction_at_max": float((v == v.max()).mean()),
                "top5_values_counts": top,
                "mean": float(v.mean()),
            }
        )
    return {"bit_depth": bd, "results": results}


def step_sensor_hdr(cam: int) -> dict[str, Any]:
    from picamera2 import Picamera2
    from picamera2.devices.imx708 import IMX708

    out: dict[str, Any] = {}
    try:
        with IMX708(cam) as dev:
            dev.set_sensor_hdr_mode(True)
        with Picamera2(cam) as picam2:
            out["sensor_modes_hdr_on"] = [dict(m) for m in picam2.sensor_modes]
            out["camera_properties_hdr_on"] = dict(picam2.camera_properties)
    finally:
        with IMX708(cam) as dev:
            dev.set_sensor_hdr_mode(False)
        out["reset_to_hdr_off"] = True
    return out


# ---------------------------------------------------------------------------------------------
OPTIONAL_STEPS = (
    "tuning_files",
    "controls",
    "raw_main",
    "exposure_delay",
    "manual_gain",
    "white_level",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path("pi_probe_out"))
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--sensor-hdr", action="store_true", help="센서 HDR 모드 확인도 수행")
    ap.add_argument(
        "--steps",
        default=",".join(OPTIONAL_STEPS),
        help="실행할 선택 단계 (쉼표 구분). 기본: 전체. "
        f"선택지: {','.join(OPTIONAL_STEPS)},sensor_hdr\n"
        "versions·camera_info·sensor_modes·raw_formats는 항상 실행한다.",
    )
    args = ap.parse_args()
    steps = {s.strip() for s in args.steps.split(",") if s.strip()}
    unknown = steps - set(OPTIONAL_STEPS) - {"sensor_hdr"}
    if unknown:
        ap.error(f"unknown steps: {sorted(unknown)}")
    if args.sensor_hdr:
        steps.add("sensor_hdr")
    args.out.mkdir(parents=True, exist_ok=True)

    rep = Report(args.out)
    rep.step("versions", step_versions)
    info = rep.step("camera_info", step_camera_info, args.camera)
    model = (info or {}).get("camera_properties", {}).get("Model")
    modes = rep.step("sensor_modes", step_sensor_modes, args.camera)
    if "tuning_files" in steps:
        rep.step("tuning_files", step_tuning_files, args.out, model)
    if "controls" in steps:
        rep.step("controls", step_controls, args.camera)

    raw_fmt = None
    if modes:
        rf = rep.step("raw_formats", step_raw_formats, args.camera, modes)
        # 16bit로 해석 가능하고 LSB가 전부 0인(=비압축 MSB 정렬) 첫 포맷을 이후 단계에 사용
        for a in (rf or {}).get("attempts", []):
            s = a.get("stats", {})
            if a.get("ok") and s.get("lsb_zero_fraction") == 1.0:
                raw_fmt = a["requested_format"]
                break
        rep.data["selected_raw_format"] = raw_fmt
        if "raw_main" in steps:
            rep.step("raw_main", step_raw_main, args.camera, modes, raw_fmt, args.out)
        if "exposure_delay" in steps:
            rep.step("exposure_delay", step_exposure_delay, args.camera, modes, raw_fmt)
        if "manual_gain" in steps:
            rep.step("manual_gain", step_manual_gain, args.camera, modes, raw_fmt)
        if "white_level" in steps:
            rep.step("white_level", step_white_level, args.camera, modes, raw_fmt)
    if "sensor_hdr" in steps:
        rep.step("sensor_hdr", step_sensor_hdr, args.camera)

    failed = [k for k, v in rep.data["steps"].items() if not v["ok"]]
    rep.data["failed_steps"] = failed
    rep.save()
    print(f"\nreport: {rep.path}")
    print("failed steps: " + (", ".join(failed) if failed else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
