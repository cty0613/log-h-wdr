"""Phase 0 수락 기준: C++ npy 로더와 Python(NumPy) 로더 결과가 비트 단위로 같다.

양방향으로 확인한다.
  1) NumPy가 쓴 파일 → C++ read_npy가 읽은 화소 버퍼 == arr.tobytes()
  2) C++ write_npy가 다시 쓴 파일 → np.load 결과 == 원본 (바이트 비교)
"""

import json

import numpy as np
import pytest

from cctvlog import cli

SEED = 20261120
RAW_W, RAW_H = 2304, 1296  # 기준 해상도 (CLAUDE.md 2절)


def _special_float32() -> np.ndarray:
    f32 = np.finfo(np.float32)
    vals = [
        0.0,
        -0.0,
        1.0,
        16.0,
        f32.max,
        -f32.max,
        f32.tiny,  # 최소 정규수
        f32.smallest_subnormal,
        np.inf,
        -np.inf,
        np.nan,
    ]
    out = np.array(vals, dtype=np.float32)
    # NaN payload까지 보존되는지 확인 (quiet NaN, payload 0x12345)
    payload_nan = np.array([0x7FC12345], dtype=np.uint32).view(np.float32)
    return np.concatenate([out, payload_nan])


def _cases() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(SEED)
    u16_full = rng.integers(0, 2**16, size=(RAW_H, RAW_W), dtype=np.uint16)
    u16_full[0, :4] = [0, 1, 0xFFFE, 0xFFFF]
    raw10 = rng.integers(0, 1024, size=(RAW_H, RAW_W), dtype=np.uint16)  # right-shift 완료 RAW
    f32_rgb = rng.normal(0.0, 4.0, size=(37, 53, 3)).astype(np.float32)
    special = _special_float32()
    f32_special = np.tile(special, 4).reshape(4, special.size)
    u8_rgb = rng.integers(0, 256, size=(31, 17, 3), dtype=np.uint8)
    return {
        "u16_full_range": u16_full,
        "u16_raw10": raw10,
        "u16_single_pixel": np.array([[0xABCD]], dtype=np.uint16),
        "f32_rgb": f32_rgb,
        "f32_special": f32_special,
        "f32_hdr_bayer": (rng.random((64, 96), dtype=np.float32) * 16.0),
        "u8_rgb": u8_rgb,
    }


CASES = _cases()


def _roundtrip(tmp_path, name: str, src_path) -> tuple[dict, bytes, np.ndarray]:
    raw = tmp_path / f"{name}.bin"
    out = tmp_path / f"{name}_cpp.npy"
    proc = cli.run_cpp(
        "npy_roundtrip", directory=cli.tools_bin_dir(), in_=src_path, raw_out=raw, npy_out=out
    )
    meta = json.loads(proc.stdout)
    return meta, raw.read_bytes(), np.load(out)


@pytest.mark.parametrize("name", list(CASES))
def test_cpp_loader_matches_numpy(tmp_path, name):
    arr = CASES[name]
    src = tmp_path / f"{name}.npy"
    np.save(src, arr)

    meta, raw_bytes, back = _roundtrip(tmp_path, name, src)

    # 1) C++가 읽은 버퍼가 NumPy 메모리와 비트 동일
    assert meta["descr"] == arr.dtype.str
    assert meta["shape"] == list(arr.shape)
    assert meta["height"] == arr.shape[0]
    assert meta["width"] == arr.shape[1]
    assert meta["channels"] == (arr.shape[2] if arr.ndim == 3 else 1)
    assert raw_bytes == arr.tobytes(order="C")

    # 2) C++가 쓴 파일을 NumPy가 읽으면 원본과 비트 동일
    assert back.dtype == arr.dtype
    assert back.shape == arr.shape
    assert back.tobytes() == arr.tobytes()


@pytest.mark.parametrize("version", [(1, 0), (2, 0), (3, 0)])
def test_cpp_reads_all_npy_format_versions(tmp_path, version):
    arr = CASES["u16_raw10"][:8, :16]
    src = tmp_path / f"v{version[0]}.npy"
    with src.open("wb") as f:
        np.lib.format.write_array(f, arr, version=version)

    _, raw_bytes, back = _roundtrip(tmp_path, f"v{version[0]}", src)
    assert raw_bytes == arr.tobytes()
    assert back.tobytes() == arr.tobytes()


def test_cpp_reads_noncontiguous_source_saved_by_numpy(tmp_path):
    # 슬라이스·전치처럼 비연속인 배열도 np.save는 C-order로 저장한다
    arr = CASES["u16_full_range"][::3, 5:200:2].T
    assert not arr.flags.c_contiguous
    src = tmp_path / "strided.npy"
    np.save(src, arr)
    _, raw_bytes, _ = _roundtrip(tmp_path, "strided", src)
    assert raw_bytes == np.ascontiguousarray(arr).tobytes()


@pytest.mark.parametrize(
    "arr",
    [
        np.zeros((4, 4), dtype=">u2"),  # big-endian
        np.zeros((4, 4), dtype=np.int16),  # 미지원 dtype
        np.zeros(16, dtype=np.uint16),  # 1차원
        np.asfortranarray(np.zeros((4, 5), dtype=np.uint16)),  # Fortran order
    ],
    ids=["big_endian", "int16", "1d", "fortran"],
)
def test_cpp_rejects_unsupported(tmp_path, arr):
    src = tmp_path / "bad.npy"
    np.save(src, arr)
    with pytest.raises(cli.CppRunError):
        _roundtrip(tmp_path, "bad", src)
