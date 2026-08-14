"""Unit tests for color_grade module.

Run from project root: cd sidecar && python -m pytest ../tests/test_color_grade.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

# Make sidecar package importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.image.color_grade import (  # noqa: E402
    _apply_contrast,
    _apply_curve,
    _apply_exposure,
    _apply_hsl,
    _apply_saturation,
    _apply_white_balance,
    _apply_whites_blacks,
    _apply_highlights_shadows,
    apply_color_grade,
    write_xmp_sidecar,
)


@pytest.fixture
def sample_image() -> Image.Image:
    """Create a gradient test image."""
    img = Image.new("RGB", (100, 100))
    for y in range(100):
        for x in range(100):
            r = int(255 * x / 100)
            g = int(255 * y / 100)
            b = 128
            img.putpixel((x, y), (r, g, b))
    return img


def test_white_balance_neutral(sample_image):
    out = _apply_white_balance(sample_image, 0, 0)
    assert list(out.getdata())[5000] == list(sample_image.getdata())[5000]


def test_white_balance_warmer():
    img = Image.new("RGB", (10, 10), (128, 128, 128))
    out = _apply_white_balance(img, 0.5, 0)
    r, g, b = out.getpixel((5, 5))
    # Warmer = more red, less blue
    assert r > 128
    assert b < 128


def test_exposure_brighter():
    img = Image.new("RGB", (10, 10), (100, 100, 100))
    out = _apply_exposure(img, 1.0)  # +1 EV = 2x
    px = out.getpixel((5, 5))
    assert px[0] > 100
    assert px[0] == 200


def test_exposure_darker():
    img = Image.new("RGB", (10, 10), (200, 200, 200))
    out = _apply_exposure(img, -1.0)  # -1 EV = 0.5x
    px = out.getpixel((5, 5))
    assert px[0] < 200
    assert px[0] == 100


def test_contrast_increases_range():
    # Mid-gray should stay around the same; extremes should expand
    img = Image.new("RGB", (10, 10), (128, 128, 128))
    out = _apply_contrast(img, 50)
    assert out.getpixel((5, 5)) == (128, 128, 128)  # mid-gray unchanged


def test_saturation_increases_color():
    # Red image → more saturated = more red, less green/blue
    img = Image.new("RGB", (10, 10), (200, 50, 50))
    out = _apply_saturation(img, 50, 0)
    r, g, b = out.getpixel((5, 5))
    # After saturation boost, r - luma should be larger
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    assert (r - luma) > (200 - 0.299 * 200 - 0.587 * 50 - 0.114 * 50)


def test_curve_identity():
    img = Image.new("RGB", (10, 10), (100, 100, 100))
    out = _apply_curve(img, [[0, 0], [255, 255]])
    assert out.getpixel((5, 5)) == (100, 100, 100)


def test_apply_color_grade_writes_files(tmp_path):
    src = tmp_path / "src.jpg"
    Image.new("RGB", (50, 50), (128, 128, 128)).save(src, "JPEG")
    dst = tmp_path / "out.jpg"
    params = {
        "white_balance": {"temp_shift": 0, "tint_shift": 0},
        "exposure": 0.2,
        "contrast": 10,
        "highlights": 0,
        "shadows": 20,
        "whites": 0,
        "blacks": 0,
        "vibrance": 10,
        "saturation": 5,
        "hsl": {},
        "curve": {"rgb": [[0, 0], [64, 70], [128, 140], [192, 210], [255, 255]]},
        "notes": "test grading",
    }
    out = apply_color_grade(src, dst, params)
    assert out == dst
    assert dst.exists()
    xmp = dst.with_suffix(".xmp")
    assert xmp.exists()
    assert "crs:Exposure" in xmp.read_text()
    json_sidecar = dst.with_suffix(".json")
    assert json_sidecar.exists()
    loaded = json.loads(json_sidecar.read_text())
    assert loaded["exposure"] == 0.2


def test_highlights_shadows_basic():
    # Pure black image: shadows adjustment shouldn't go negative
    img = Image.new("RGB", (10, 10), (50, 50, 50))
    out = _apply_highlights_shadows(img, 0, 50)  # boost shadows
    px = out.getpixel((5, 5))
    assert px[0] > 50  # brighter than original


def test_whites_blacks_basic():
    img = Image.new("RGB", (10, 10), (128, 128, 128))
    out_w = _apply_whites_blacks(img, 50, 0)
    assert out_w.getpixel((5, 5)) != (128, 128, 128)


def test_hsl_no_change_with_empty_dict():
    img = Image.new("RGB", (10, 10), (128, 128, 128))
    out = _apply_hsl(img, {})
    assert out.getpixel((5, 5)) == (128, 128, 128)
