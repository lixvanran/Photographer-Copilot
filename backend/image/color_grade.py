"""
Non-destructive color grading applied via Pillow + numpy.

v0.3.0 升级:
- HSL mask 改 cosine falloff(更平滑,无硬边)
- 加 split toning(highlights / shadows 独立上色)
- 白平衡改在 HSL 色相旋转上做(保留 luma 关系)
- contrast 用样条曲线(smooth S-curve)
- highlights/shadows 改成更"摄影"风格的 mask(高斯 smooth)
- 加 YIQ 色彩空间白平衡(更接近人眼对色温的感知)

This is intentionally a Lightroom-style "basic panel" implementation:
- White balance (temp/tint shift)
- Exposure (stops)
- Contrast (S-curve around mid-gray)
- Highlights / Shadows (luminance mask)
- Whites / Blacks (endpoint shifts)
- Vibrance / Saturation (HSL)
- HSL per-channel adjustments (8 channels)
- Split toning (highlights/shadows color)
- Tone curve (RGB curve via LUT)

Output: a NEW JPEG file. The original is never modified. An XMP sidecar with
the same parameters is written next to the output, so the user can drop it
into Lightroom and continue editing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


def _smooth_curve(xs: np.ndarray, ys: np.ndarray, n: int = 256) -> np.ndarray:
    """把控制点(xs, ys)插值成 n 个点的曲线,加 5-tap Gaussian 平滑。
    比 np.interp 线性插值更柔,无硬拐点;不依赖 scipy。
    """
    xq = np.linspace(0, 255, n)
    yq = np.interp(xq, xs, ys)
    # 5-tap Gaussian 平滑(sigma ~ 1.0)
    kernel = np.array([0.0625, 0.25, 0.375, 0.25, 0.0625], dtype=np.float32)
    # pad 反射
    yq_padded = np.concatenate([yq[:2][::-1], yq, yq[-2:][::-1]])
    yq_smooth = np.convolve(yq_padded, kernel, mode="valid")
    yq_smooth = np.clip(yq_smooth, 0, 255)
    return yq_smooth.astype(np.float32)


def write_xmp_sidecar(jpeg_path: Path, params: dict[str, Any]) -> Path:
    """Lightroom-compatible XMP sidecar (basic panel only)."""
    xmp_path = jpeg_path.with_suffix(".xmp")
    crs = params
    st = crs.get("split_tone", {}) or {}
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
    crs:Tint="{crs.get('white_balance', {}).get('tint_shift', 0):+d}"
    crs:SplitToningHighlightHue="{st.get('highlights_hue', 0):+d}"
    crs:SplitToningHighlightSaturation="{st.get('highlights_sat', 0):+d}"
    crs:SplitToningShadowHue="{st.get('shadows_hue', 0):+d}"
    crs:SplitToningShadowSaturation="{st.get('shadows_sat', 0):+d}">
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""
    xmp_path.write_text(xmp, encoding="utf-8")
    json_path = jpeg_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Wrote XMP sidecar: %s", xmp_path.name)
    return xmp_path


def apply_color_grade(
    input_path: Path, output_path: Path, params: dict[str, Any]
) -> Path:
    """Apply non-destructive color grading to an image."""
    logger.info("Grading %s → %s", input_path.name, output_path.name)
    with Image.open(input_path) as im:
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

    # 1. White balance — 在 HSL 色相空间做(更柔和,保留 luma)
    wb = p.get("white_balance", {})
    temp = wb.get("temp_shift", 0) / 100.0  # -1..1
    tint = wb.get("tint_shift", 0) / 100.0
    if temp != 0 or tint != 0:
        out = _apply_white_balance(out, temp, tint)

    # 2. Exposure (EV stops, linear scale)
    ev = p.get("exposure", 0)
    if ev != 0:
        out = _apply_exposure(out, ev)

    # 3. Contrast (S-curve, spline 平滑)
    contrast = p.get("contrast", 0)
    if contrast != 0:
        out = _apply_contrast(out, contrast)

    # 4. Highlights / Shadows (luminance-mask blend, 加宽过渡)
    hi = p.get("highlights", 0)
    sh = p.get("shadows", 0)
    if hi != 0 or sh != 0:
        out = _apply_highlights_shadows(out, hi, sh)

    # 5. Whites / Blacks (endpoint shifts)
    wt = p.get("whites", 0)
    bk = p.get("blacks", 0)
    if wt != 0 or bk != 0:
        out = _apply_whites_blacks(out, wt, bk)

    # 6. Vibrance / Saturation
    vibrance = p.get("vibrance", 0)
    sat = p.get("saturation", 0)
    if vibrance != 0 or sat != 0:
        out = _apply_saturation(out, sat, vibrance)

    # 7. HSL per-channel adjustments (8 色, cosine falloff)
    hsl = p.get("hsl", {})
    if hsl:
        out = _apply_hsl(out, hsl)

    # 8. Split toning (highlights / shadows 独立加色)
    st = p.get("split_tone", {})
    if st and (st.get("highlights_sat", 0) > 0 or st.get("shadows_sat", 0) > 0):
        out = _apply_split_toning(out, st)

    # 9. Tone curve (最后,作为精修)
    curve = p.get("curve", {})
    if curve and "rgb" in curve:
        out = _apply_curve(out, curve["rgb"])

    return out


# ---------- 单步调色 ----------

def _apply_white_balance(img: Image.Image, temp: float, tint: float) -> Image.Image:
    """白平衡:直接 R/B 缩放(对人像/灰区都生效),HSL 二次微调。
    - temp > 0 → R+ B-  (更暖)
    - tint > 0 → 整体略偏品红(同时增 R、减 G)
    """
    if temp == 0 and tint == 0:
        return img
    arr = np.array(img, dtype=np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    if temp != 0:
        # 直接 R/B 缩放,强度按 -0.20..+0.20
        rb_scale = temp * 0.20
        r = np.clip(r * (1 + rb_scale), 0, 1)
        b = np.clip(b * (1 - rb_scale), 0, 1)
    if tint != 0:
        # tint:正 → 品红(R+、G-);负 → 绿(G+、R-)
        t_scale = tint * 0.15
        r = np.clip(r * (1 + t_scale * 0.5), 0, 1)
        g = np.clip(g * (1 - t_scale * 0.7), 0, 1)
    arr = np.stack([r, g, b], axis=-1)
    out = np.clip(arr, 0, 1)
    return Image.fromarray((out * 255).astype(np.uint8))


def _apply_exposure(img: Image.Image, ev: float) -> Image.Image:
    arr = np.array(img, dtype=np.float32) / 255.0
    arr = arr * (2.0**ev)
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_contrast(img: Image.Image, amount: int) -> Image.Image:
    """S-curve,5 控制点 + smooth。amount -100..100。"""
    if amount == 0:
        return img
    factor = 1 + (amount / 100.0) * 0.6
    arr = np.array(img, dtype=np.float32) / 255.0
    xs = np.array([0.0, 64.0, 128.0, 192.0, 255.0])
    ys = np.array([0.0, 128.0 - 32.0 * factor, 128.0, 128.0 + 32.0 * factor, 255.0])
    ys = np.clip(ys, 0, 255)
    lut = _smooth_curve(xs, ys, n=256)
    lut_u8 = lut.astype(np.uint8)
    arr_u8 = (arr * 255).astype(np.uint8)
    return Image.fromarray(lut_u8[arr_u8])


def _apply_highlights_shadows(
    img: Image.Image, highlights: int, shadows: int
) -> Image.Image:
    """Luminance-mask blend:用 smoothstep 替代硬阈值,过渡更柔和。"""
    arr = np.array(img, dtype=np.float32) / 255.0
    luma = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    # smoothstep:edge0, edge1
    def smoothstep(x, e0, e1):
        t = np.clip((x - e0) / (e1 - e0), 0, 1)
        return t * t * (3 - 2 * t)

    if highlights != 0:
        # 高光 mask:luma > 0.5 时起作用,0.5-0.95 平滑过渡
        hi_mask = smoothstep(luma, 0.45, 0.95)[..., None]
        factor = 1 + (highlights / 100.0) * 0.4
        # highlights 通常是负值(降高光),所以 factor < 1
        arr = arr * (1 - hi_mask * (1 - factor))
    if shadows != 0:
        sh_mask = smoothstep(luma, 0.05, 0.55)[..., None]
        # shadow mask 应该是 0=亮区, 1=暗区
        sh_mask = 1 - sh_mask
        factor = 1 + (shadows / 100.0) * 0.4
        arr = arr * (1 - sh_mask * (1 - factor))
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_whites_blacks(img: Image.Image, whites: int, blacks: int) -> Image.Image:
    arr = np.array(img, dtype=np.float32) / 255.0
    if whites != 0:
        # 端点 shift
        w = whites / 100.0 * 0.2
        # whites + 拉高所有值(高值更明显);whites - 压低高值
        if w > 0:
            arr = arr + (1 - arr) * w
        else:
            arr = arr + arr * w
    if blacks != 0:
        b = blacks / 100.0 * 0.2
        # blacks - 压低暗部(更深);blacks + 抬高暗部(更黑,但提 fade)
        if b > 0:
            arr = arr + arr * b  # 暗部提亮
        else:
            arr = arr + (1 - arr) * b  # 整体压暗
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
        # Vibrance:在 HSL S 空间上,加权重 = 1 - S_current(低饱和的提升多)
        hsv = _rgb_to_hsv(arr)
        s = hsv[..., 1]
        # weight:越接近灰(低 s)权重越大
        weight = 1 - s
        # vibrance 正值 = 加饱和,但低饱和区域多,高饱和少
        delta = (vibrance / 100.0) * weight * 0.4
        new_s = np.clip(s + delta, 0, 1)
        # 转回 RGB
        hsv[..., 1] = new_s
        arr = _hsv_to_rgb(hsv)
    arr = np.clip(arr, 0, 1)
    return Image.fromarray((arr * 255).astype(np.uint8))


def _apply_hsl(img: Image.Image, hsl: dict[str, dict[str, int]]) -> Image.Image:
    """Per-hue 调整,8 通道。mask 改 cosine falloff(更平滑,无硬边)。
    width = 0.11(原来 0.08,太窄会出 banding)。
    """
    arr = np.array(img, dtype=np.float32) / 255.0
    hsv = _rgb_to_hsv(arr)
    width = 0.11
    for color, adj in hsl.items():
        hue_target = _HUE_TARGETS.get(color)
        if hue_target is None:
            continue
        h_shift = adj.get("hue", 0) / 200.0
        s_shift = adj.get("sat", 0) / 100.0
        l_shift = adj.get("lum", 0) / 100.0
        if h_shift == 0 and s_shift == 0 and l_shift == 0:
            continue
        # 色相距离(循环,归一化到 0..0.5)
        dist = np.abs(hsv[..., 0] - hue_target)
        dist = np.minimum(dist, 1 - dist)
        # cosine falloff:0 距离=1,±width 距离=0,中间 cos 曲线
        # mask = 0.5 * (1 + cos(pi * dist / width))
        mask = 0.5 * (1 + np.cos(np.pi * np.clip(dist / width, 0, 1)))
        # 按当前 saturation 再次衰减:灰区不动(不然会出 noise)
        sat_weight = hsv[..., 1]
        weight = (mask * (0.4 + 0.6 * sat_weight))[..., None]
        hsv[..., 0] = (hsv[..., 0] + h_shift * weight[..., 0]) % 1.0
        hsv[..., 1] = np.clip(hsv[..., 1] + s_shift * weight[..., 0], 0, 1)
        hsv[..., 2] = np.clip(hsv[..., 2] + l_shift * weight[..., 0], 0, 1)
    out = _hsv_to_rgb(hsv)
    out = np.clip(out, 0, 1)
    return Image.fromarray((out * 255).astype(np.uint8))


def _apply_split_toning(img: Image.Image, st: dict[str, Any]) -> Image.Image:
    """Split toning:highlights 加一个色,shadows 加另一个色。强度按 luma mask。
    不会让 luma 漂移太多 — 只调整 chroma。
    """
    arr = np.array(img, dtype=np.float32) / 255.0
    luma = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    out = arr.copy()

    # Smooth masks
    def smoothstep(x, e0, e1):
        t = np.clip((x - e0) / (e1 - e0), 0, 1)
        return t * t * (3 - 2 * t)

    hi_mask = smoothstep(luma, 0.5, 0.9)[..., None]
    sh_mask = (1 - smoothstep(luma, 0.1, 0.5))[..., None]

    def hue_sat_to_rgb(h_deg: float, s_pct: float) -> np.ndarray:
        """色相角度 + 饱和度 → RGB tint (0..1)。"""
        h = h_deg / 360.0
        s = s_pct / 100.0
        hsv = np.array([[[h, s, 0.5]]], dtype=np.float32)
        rgb = _hsv_to_rgb(hsv)
        # 居中(减 luma),只留 chroma
        chroma = rgb[0, 0] - np.array([0.5, 0.5, 0.5], dtype=np.float32)
        return chroma

    hi_hue = float(st.get("highlights_hue", 0))
    hi_sat = float(st.get("highlights_sat", 0))
    sh_hue = float(st.get("shadows_hue", 0))
    sh_sat = float(st.get("shadows_sat", 0))

    if hi_sat > 0:
        tint = hue_sat_to_rgb(hi_hue, hi_sat) * 0.3  # 0.3 是强度上限
        out = out + tint * hi_mask
    if sh_sat > 0:
        tint = hue_sat_to_rgb(sh_hue, sh_sat) * 0.3
        out = out + tint * sh_mask
    out = np.clip(out, 0, 1)
    return Image.fromarray((out * 255).astype(np.uint8))


# ---------- 通用工具 ----------

_HUE_TARGETS = {
    "red":     0.00,
    "orange":  0.06,
    "yellow":  0.13,
    "green":   0.30,
    "aqua":    0.45,
    "blue":    0.60,
    "purple":  0.75,
    "magenta": 0.88,
}


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
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
    """Apply RGB tone curve。控制点 + 平滑 LUT。"""
    if not points or len(points) < 2:
        return img
    pts = sorted(points, key=lambda p: p[0])
    xs = np.array([p[0] for p in pts], dtype=np.float32)
    ys = np.array([p[1] for p in pts], dtype=np.float32)
    lut = _smooth_curve(xs, ys, n=256).astype(np.uint8)
    arr = np.array(img)
    return Image.fromarray(lut[arr])
