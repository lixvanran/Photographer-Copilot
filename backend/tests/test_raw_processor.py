"""Unit tests for the raw processor."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.image.raw_processor import (  # noqa: E402
    SUPPORTED_INPUT_EXTENSIONS,
    is_raw,
    is_supported,
    to_jpeg_preview,
)


def test_is_raw_false_for_jpeg(tmp_path):
    jpg = tmp_path / "x.jpg"
    Image.new("RGB", (10, 10), (128, 128, 128)).save(jpg, "JPEG")
    assert is_raw(jpg) is False
    assert is_supported(jpg) is True


def test_is_supported_various():
    for ext in ["jpg", "jpeg", "png", "heic", "webp", "tiff"]:
        p = Path(f"x.{ext}")
        assert is_supported(p), f"{ext} should be supported"
    for ext in ["txt", "zip", "exe"]:
        p = Path(f"x.{ext}")
        assert not is_supported(p), f"{ext} should not be supported"


def test_to_jpeg_preview_jpeg(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (100, 100), (128, 128, 128)).save(src, "JPEG")
    dst = tmp_path / "out.jpg"
    to_jpeg_preview(src, dst, max_edge=50)
    assert dst.exists()
    with Image.open(dst) as im:
        assert max(im.size) == 50  # downsampled


def test_to_jpeg_preview_already_small(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (50, 50), (128, 128, 128)).save(src, "JPEG")
    dst = tmp_path / "out.jpg"
    to_jpeg_preview(src, dst, max_edge=200)
    with Image.open(dst) as im:
        assert im.size == (50, 50)  # not upscaled


def test_to_jpeg_preview_rgba(tmp_path):
    src = tmp_path / "src.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 128)).save(src, "PNG")
    dst = tmp_path / "out.jpg"
    to_jpeg_preview(src, dst)
    assert dst.exists()
    with Image.open(dst) as im:
        assert im.mode == "RGB"
