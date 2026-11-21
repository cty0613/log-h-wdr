"""플랫필드·다크 촬영 공용 세션 (check_flatfield.py, capture_ptc.py).

capture_raw.Capturer 위에 게인·노출을 함께 바꾸고, 둘 다 반영된 프레임만 저장하는 얇은 층.
근거: docs/pi_notes.md (최소 게인 1.1228, 노출·게인 반영 지연 5프레임, 라인 단위 노출 양자화).
"""

from __future__ import annotations

import argparse
import faulthandler
import json
from pathlib import Path
from typing import Any

import numpy as np

import capture_raw as cr
import rawutil as ru

GAIN_MIN = 1.1228  # code 112, 1024/(1024-112)
GAIN_MAX = 16.0
EXPOSURE_MIN_US = 13  # 2304x1296 모드 exposure_limits 하한 (실측)
WATCHDOG_S = 30  # 프레임 하나를 이 시간 안에 못 받으면 traceback을 남기고 종료
EXPOSURE_ABS_TOL_US = 25.0


def arm_watchdog(seconds: float = WATCHDOG_S) -> None:
    """센서가 스트리밍을 못 하면 capture_request가 영원히 막힌다(SIGINT도 안 먹음).
    faulthandler가 별도 스레드에서 모든 스레드의 traceback을 stderr에 쓰고 프로세스를 끝낸다."""
    faulthandler.dump_traceback_later(seconds, exit=True)


def disarm_watchdog() -> None:
    faulthandler.cancel_dump_traceback_later()


def exposure_close(
    actual_us: float | None,
    requested_us: float,
    tol: float,
    abs_tol_us: float = EXPOSURE_ABS_TOL_US,
) -> bool:
    """새 노출이 반영됐는지 판정. 짧은 노출은 라인 단위 양자화로 수십 µs 어긋나므로
    (1000 → 988 µs, 100 → 80 µs) 허용치를 max(tol·요청, 25 µs)로 둔다.
    연속 설정 사이 간격은 촬영 계획에서 이 허용치의 2배 이상으로 보장한다."""
    return actual_us is not None and abs(actual_us - requested_us) <= max(
        tol * requested_us, abs_tol_us
    )


def roi_channel_means(raw: np.ndarray, black: float, white: float, roi: int) -> list[float]:
    """중앙 roi x roi(센서 화소, 짝수 정렬)의 2x2 Bayer 위치별 정규화 평균
    [(0,0), (0,1), (1,0), (1,1)]."""
    h, w = raw.shape
    y0, x0 = (h // 2 - roi // 2) & ~1, (w // 2 - roi // 2) & ~1
    r = raw[y0 : y0 + roi, x0 : x0 + roi].astype(np.float64)
    scale = white - black
    return [float((r[dy::2, dx::2].mean() - black) / scale) for dy in (0, 1) for dx in (0, 1)]


class Session:
    """게인·노출 수동 고정 RAW 세션. AWB 수렴 대기 없음(RAW에 영향 없음), ISP PNG 저장 없음."""

    def __init__(
        self,
        out: Path,
        *,
        camera: int = 0,
        lens_position: float | None = None,
        roi: int = 512,
        note_prefix: str = "",
        notes: str = "",
        initial_exposure_us: float = 1000,
        abs_tol_us: float = EXPOSURE_ABS_TOL_US,
    ) -> None:
        lens = [] if lens_position is None else ["--lens-position", str(lens_position)]
        cap_args = cr.parse_args(
            [
                "--mode",
                "single",
                "--exposures-us",
                str(round(initial_exposure_us)),
                "--gain",
                str(GAIN_MIN),
                "--out",
                str(out),
                "--colour-gains",
                "1.990,1.928",  # RAW에는 영향 없음, AWB 수렴 대기 생략
                "--no-isp",
                "--camera",
                str(camera),
                *lens,
            ]
        )
        self.cap = cr.Capturer(cap_args)
        self.roi = roi
        self.note_prefix = note_prefix
        self.notes = notes
        self.gain = GAIN_MIN
        self.exposure = float(initial_exposure_us)  # grab의 일치 기준
        self.requested = float(initial_exposure_us)
        self.abs_tol_us = abs_tol_us  # 노출 단위가 거친 모드(센서 HDR, 약 71.5 µs)에서 키운다

    def start(self) -> dict[str, Any]:
        md0 = self.cap.start()
        self.cap.flip = cr.sensor_flip(dict(self.cap.picam2.camera_properties).get("Model"))
        return md0

    def describe(self) -> dict[str, Any]:
        cap = self.cap
        return {
            "camera_properties": dict(cap.picam2.camera_properties),
            "sensor_mode": cap.mode,
            "raw_format": cap.raw_format,
            "bayer_order": cap.bayer_order,
            "camera_configuration": cap.config,
            "roi": {"size": self.roi, "position": "center", "unit": "sensor pixels"},
        }

    def set(self, gain: float, exposure_us: float) -> None:
        """게인·노출 요청. 이후 grab은 실제 노출이 요청과 허용치 안인 프레임을 기다린다."""
        self.gain, self.exposure = gain, float(exposure_us)
        self.requested = float(exposure_us)
        ctl = {"AnalogueGain": gain, "ExposureTime": round(exposure_us)}
        self.cap.controls.update(ctl)
        self.cap.picam2.set_controls(ctl)

    def settle(self, min_frames: int = 7, stable: int = 3) -> float:
        """요청→실제 노출 대응을 모르는 모드(센서 HDR)용. set() 뒤 메타데이터를 읽어, 게인이 맞고
        노출이 stable 프레임 연속 같은 값이 될 때까지 기다린 뒤 그 실제 노출을 grab의 일치
        기준으로 삼는다(요청값은 JSON에 그대로 기록). 최소 min_frames(> 반영 지연 5)를 본다."""
        arm_watchdog()
        hist: list[Any] = []
        for i in range(self.cap.args.max_wait_frames + min_frames):
            md = self.cap.picam2.capture_metadata()
            if not ru.gain_matches(md.get("AnalogueGain"), self.gain):
                hist.clear()
                continue
            hist.append(md.get("ExposureTime"))
            if i + 1 >= min_frames and len(hist) >= stable and len(set(hist[-stable:])) == 1:
                self.exposure = float(hist[-1])
                return self.exposure
        raise TimeoutError(f"exposure did not settle after request {self.requested}: {hist}")

    def grab(self, out_dir: Path, index: int, step: str) -> dict[str, Any]:
        """게인·노출이 모두 설정값과 맞는 프레임까지 버리고 저장. 5.2 JSON + ROI 요약 반환."""
        cap = self.cap
        arm_watchdog()
        out_dir.mkdir(parents=True, exist_ok=True)
        dropped = 0
        seen: list[tuple[Any, Any]] = []
        for _ in range(cap.args.max_wait_frames):
            req = cap.picam2.capture_request()
            try:
                md = dict(req.get_metadata())
                seen.append((md.get("ExposureTime"), md.get("AnalogueGain")))
                if not (
                    exposure_close(
                        md.get("ExposureTime"), self.exposure, cap.args.tolerance, self.abs_tol_us
                    )
                    and ru.gain_matches(md.get("AnalogueGain"), self.gain)
                ):
                    dropped += 1
                    continue
                raw = ru.raw_to_uint16(req.make_array("raw"), cap.size[0], cap.bit_depth)
            finally:
                req.release()
            stem = f"{index:06d}"
            np.save(out_dir / f"{stem}.npy", raw)
            fj = ru.frame_json(
                frame_index=index,
                md=md,
                size=cap.size,
                bit_depth=cap.bit_depth,
                hdr=False,
                bayer_order=cap.bayer_order,
                exposure_requested_us=self.requested,
                raw_format=cap.raw_format,
                notes=f"{self.note_prefix}{step} {self.notes}".strip(),
            )
            fj["dropped_frames_before"] = dropped
            fj["sensor_flip"] = cap.flip
            (out_dir / f"{stem}.json").write_text(json.dumps(fj, indent=2, ensure_ascii=False))
            black = fj["black_level"] if isinstance(fj["black_level"], int) else 64
            means = roi_channel_means(raw, black, fj["white_level"], self.roi)
            return {
                "file": str((out_dir / f"{stem}.npy").relative_to(cap.args.out)),
                "gain": md.get("AnalogueGain"),
                "exposure_time_us": md.get("ExposureTime"),
                "exposure_time_us_requested": self.requested,
                "timestamp_ns": md.get("SensorTimestamp"),
                "sensor_temperature": md.get("SensorTemperature"),
                "roi_means": means,
                "roi_max_channel_mean": max(means),
            }
        raise TimeoutError(
            f"no frame with gain {self.gain} / exposure {self.exposure} us "
            f"within {cap.args.max_wait_frames} frames; seen (exposure, gain) {seen}"
        )

    def close(self) -> None:
        self.cap.close()


def add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--lens-position", type=float, default=None)
    ap.add_argument("--notes", default="")


def ensure_empty(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise SystemExit(f"output directory is not empty: {out}")
