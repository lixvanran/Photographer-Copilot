"""
Non-destructive color grading applied via Pillow + numpy.

This is intentionally a Lightroom-style "basic panel" implementation:
- White balance (temp/tint shift)
- Exposure (stops)
- Contrast (S-curve around mid-gray)
- Highlights / Shadows (luminance mask)
- Whites / Blacks (endpoint shifts)
- Vibrance / Saturation (HSL)
- HSL per-channel adjustments
- Tone curve (simple RGB curve via LUT)

Output: a NEW JPEG file. The original is never modified. An XMP sidecar with
the same parameters is written next to the output, so the user can drop it
into Lightroom and continue editing.

Reserved for future:
- Local adjustment masks (radial/gradient/brush)
- HSL panel split into 8 channels (currently using simplified model)
- Camera profile / calibration
- Split toning (highlights/shadows color)
- Grain
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter, ImageOps

logger = logging.getLogger(__name__)


def write_xmp_sidecar(jpeg_path: Path, params: dict[str, Any]) -> Path:
    """
    Write a Lightroom-compatible XMP sidecar with the given parameters.

    MVP scope: a minimal subset of the XMP standard. Lightroom will read
    the basic adjustments and apply them on import.

    Future: full XMP coverage including masks, HSL, calibration, etc.
    """
    xmp_path = jpeg_path.with_suffix(".xmp")
    # Minimal Lightroom-compatible XMP for basic adjustments
    crs = params
    xmp = f"""<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Photographer Copilot">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
    crs:Exposure="{crs.get('exposure', 0):+.3f}"
    crs:Contrast="{crs.get('contrast', 0):+d}"
    crs:Highlights="{crs.get('highlights', 0):+d}"
    crs:Shadows="{crs.get('shadows', 0):+d}"
    crs:Whites="{crs.get('whites', 0):+d}"
    crs:Blacks="{crs.get('blacks', 0):+d}"
    crs:Vibrance="{crs.get('vibrance', 0):+d}"
    crs:Saturation="{crs.get('saturation', 0):+d}"
    crs:Temp="{crs.get('white_balance', {}).get('temp_shift', 0):+d}"
    crs:Tint="{crs.get('white_balance', {}).get('tint_shift', 0):+d}">
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""
    xmp_path.write_text(xmp, encoding="utf-8")
    # Also write a side-by-side JSON for our own future use
    json_path = jpeg_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Wrote XMP sidecar: %s", xmp_path.name)
    return xmp_path


def apply_color_grade(
    input_path: Path, output_path: Path, params: dict[str, Any]
) -> Path:
    """
    Apply non-destructive color grading to an image.

    Reads input (JPEG/PNG), applies the grade, writes new JPEG + XMP sidecar.
    """
    logger.info("Grading %s → %s", input_path.name, output_path.name)
    with Image.open(input_path) as im:
        # Apply EXIF orientation so portrait phone shots don't come out sideways.
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        graded = _apply_params(im, params)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    graded.save(output_path, format="JPEG", quality=92, optimize=True)
    write_xmp_sidecar(output_path, params)
    return output_path


def _apply_params(img: Image.Image, p: dict[str, Any]) -> Image.Image:
    """Apply all grading params in sequence. Returns a new image."""
    out = img

    # 1. White balance (temp/tint shift via channel scaling)
    wb = p.get("white_balance", {})
    temp = wb.get("temp_shift", 0) / 100.0  # -1..1
    tint = wb.get("tint_shift", 0) / 100.0
    if temp != 0 or tint != 0:
        out = _apply_white_balance(out, temp, tint)

    # 2. Exposure (EV stops, linear scale)
    ev = p.get("exposure", 0)
    if ev != 0:
        out = _apply_exposure(out, ev)

    # 3. Contrast (S-curve around 0.5)
    contrast = p.get("contrast", 0)
    if contrast != 0:
        out = _apply_contrast(out, contrast)

    # 4. Highlights / Shadows (luminance-mask blend)
    hi = p.get("highlights", 0)
    sh = p.get("shadows", 0)
    if hi != 0 or sh != 0:
        out = _apply_highlights_shadows(out, hi, sh)

    # 5. Whites / Blacks (endpoint shifts)
    wt = p.get("whites", 0)
    bk = p.get("blacks", 0)
    if wt != 0 or bk != 0:
        out = _apply_whites_blacks(out, wt, bk)

    # 6. Vibrance / Saturation (HSL)
    vibrance = p.get("vibrance", 0)
    sat = p.get("saturation", 0)
    if vibrance != 0 or sat != 0:
        out = _apply_saturation(out, sat, vibrance)

    # 7. HSL per-channel adjustments
    hsl = p.get("hsl", {})
    if hsl:
        out = _apply_hsl(out, hsl)

    # 8. Tone curve (last)
    curve = p.get("curve", {})
    if curve and "rgb" in curve:
        out = _apply_curve(out, curve["rgb"])

    return out


def _apply_white_balance(img: Image.Image, temp: float, tint: float) -> Image.Image:
    """Temp: + = warmer (more red, less blue). Tint: + = more magenta."""
    arr = np.array(img, dtype=np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    if temp != 0:
        r = np.clip(r * (1 + temp * 0.3), 0, 1)
        b = np.clip(b * (1 - temp * 0.3), 0, 1)
    if tint != 0:
        g = np.clip(g * (1 - tint * 0.2), 0, 1)
    arr[..., 0] = r
    arr[..., 1] = g
    arr[..., 2] = b
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_exposure(img: Image.Image, ev: float) -> Image.Image:
    arr = np.array(img, dtype=np.float32) / 255.0
    arr = arr * (2.0**ev)
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_contrast(img: Image.Image, amount: int) -> Image.Image:
    """S-curve around 0.5 mid-gray. amount in -100..100."""
    if amount == 0:
        return img
    factor = (amount + 100) / 100.0  # 0..2
    factor = factor**2  # ease
    arr = np.array(img, dtype=np.float32) / 255.0
    # Sigmoid-like S-curve
    arr = 0.5 + (arr - 0.5) * factor
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_highlights_shadows(
    img: Image.Image, highlights: int, shadows: int
) -> Image.Image:
    """Luminance-mask blend: highlights affect bright pixels, shadows affect dark."""
    arr = np.array(img, dtype=np.float32) / 255.0
    luma = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    # Highlights mask: bright pixels (peaks at 1.0 for white)
    hi_mask = np.clip((luma - 0.5) * 2, 0, 1)[..., None]
    # Shadows mask: dark pixels
    sh_mask = np.clip((0.5 - luma) * 2, 0, 1)[..., None]
    if highlights != 0:
        factor = 1 + (highlights / 100.0) * 0.5
        arr = arr * (1 - hi_mask) + arr * factor * hi_mask
    if shadows != 0:
        factor = 1 + (shadows / 100.0) * 0.5
        arr = arr * (1 - sh_mask) + arr * factor * sh_mask
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_whites_blacks(img: Image.Image, whites: int, blacks: int) -> Image.Image:
    arr = np.array(img, dtype=np.float32) / 255.0
    if whites != 0:
        # Whites: shift upper endpoint
        w = whites / 100.0 * 0.2
        arr = arr + (1 - arr) * w
    if blacks != 0:
        b = blacks / 100.0 * 0.2
        arr = arr + arr * b  # blacks: shift lower endpoint (negative = crush)
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_saturation(
    img: Image.Image, saturation: int, vibrance: int
) -> Image.Image:
    arr = np.array(img, dtype=np.float32) / 255.0
    luma = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    luma_3 = luma[..., None]
    if saturation != 0:
        f = 1 + saturation / 100.0
        arr = luma_3 + (arr - luma_3) * f
    if vibrance != 0:
        # Vibrance: boost less-saturated pixels more
        current_sat = np.max(np.abs(arr - luma_3), axis=-1)
        weight = 1 - current_sat
        f = 1 + vibrance / 100.0 * weight[..., None] * 0.5
        arr = luma_3 + (arr - luma_3) * f
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_hsl(img: Image.Image, hsl: dict[str, dict[str, int]]) -> Image.Image:
    """Per-hue adjustments. Simplified: shift hue via channel rotation in HSL space."""
    arr = np.array(img, dtype=np.float32) / 255.0
    # Convert RGB to HSV for hue/sat/lum adjustments
    hsv = _rgb_to_hsv(arr)
    for color, adj in hsl.items():
        hue_target = _HUE_TARGETS.get(color)
        if hue_target is None:
            continue
        h_shift = adj.get("hue", 0) / 200.0  # -0.5..0.5
        s_shift = adj.get("sat", 0) / 100.0
        l_shift = adj.get("lum", 0) / 100.0
        # Hue distance (circular)
        dist = np.abs(hsv[..., 0] - hue_target)
        dist = np.minimum(dist, 1 - dist)
        # Smooth falloff: 1 at center, 0 at ±0.08 distance
        mask = np.clip(1 - dist / 0.08, 0, 1)[..., None]
        hsv[..., 0] = (hsv[..., 0] + h_shift * mask[..., 0]) % 1.0
        hsv[..., 1] = np.clip(hsv[..., 1] + s_shift * mask[..., 0], 0, 1)
        hsv[..., 2] = np.clip(hsv[..., 2] + l_shift * mask[..., 0], 0, 1)
    arr = _hsv_to_rgb(hsv)
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


_HUE_TARGETS = {
    "red": 0.0,
    "orange": 0.05,
    "yellow": 0.12,
    "green": 0.30,
    "aqua": 0.45,
    "blue": 0.60,
    "purple": 0.75,
    "magenta": 0.88,
}


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """rgb in [0,1] → hsv in [0,1]."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    max_c = np.max(rgb, axis=-1)
    min_c = np.min(rgb, axis=-1)
    v = max_c
    diff = max_c - min_c
    s = np.where(max_c > 0, diff / np.maximum(max_c, 1e-10), 0)
    h = np.zeros_like(v)
    mask = diff > 1e-10
    rc = np.where(mask, (max_c - r) / np.maximum(diff, 1e-10), 0)
    gc = np.where(mask, (max_c - g) / np.maximum(diff, 1e-10), 0)
    bc = np.where(mask, (max_c - b) / np.maximum(diff, 1e-10), 0)
    h = np.where(r == max_c, bc - gc, h)
    h = np.where(g == max_c, 2.0 + rc - bc, h)
    h = np.where(b == max_c, 4.0 + gc - rc, h)
    h = (h / 6.0) % 1.0
    return np.stack([h, s, v], axis=-1)


def _hsv_to_rgb(hsv: np.ndarray) -> np.ndarray:
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    i = np.floor(h * 6).astype(int)
    f = h * 6 - i
    p = v * (1 - s)
    q = v * (1 - f * s)
    t = v * (1 - (1 - f) * s)
    i = i % 6
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], axis=-1)


def _apply_curve(img: Image.Image, points: list[list[int]]) -> Image.Image:
    """Apply an RGB tone curve from a list of (in, out) points in 0..255."""
    if not points or len(points) < 2:
        return img
    xs = np.array([p[0] for p in points], dtype=np.float32)
    ys = np.array([p[1] for p in points], dtype=np.float32)
    lut = np.interp(np.arange(256), xs, ys).astype(np.uint8)
    arr = np.array(img)
    return Image.fromarray(lut[arr])
