"""
Image analysis helpers — extract客观读数 from a photo for the grade prompt.

输出能直接塞进 build_grade_prompt 的字符串:
- format_exif()  → EXIF 字符串(相机/ISO/光圈/快门/原 WB)
- analyze_image() → 亮度/饱和度/色温 分布
- estimate_color_temp() → 主色温估计(粗略)

这些数据让 M3 不再"凭感觉"给参数,而是基于客观读数。
"""
from __future__ import annotations

import io
import logging
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

logger = logging.getLogger(__name__)


# ---- EXIF 解析 ----

def parse_exif(path: Path) -> dict[str, Any] | None:
    """Read EXIF from JPEG/TIFF/HEIC. Return normalized dict with friendly keys.
    RAW formats(.arw/.cr2/.nef/.dng) — 不读 EXIF(rawpy 转换后的 JPEG 是
    preview,EXIF 信息已丢大部分);返回 None。

    返回的 key 是 camera_make / camera_model / lens_model / iso / aperture /
    shutter / focal_length / white_balance / datetime。
    """
    if path.suffix.lower() in {".arw", ".cr2", ".cr3", ".nef", ".dng", ".raf", ".orf", ".rw2", ".pef"}:
        # 这些是 RAW,EXIF 在 rawpy 阶段读才有意义(后续要扩展)
        return None

    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return None
            # IFD 0 + Exif IFD
            try:
                exif_ifd = exif.get_ifd(0x8769) or {}
            except Exception:
                exif_ifd = {}

            # 标准 EXIF tags 翻译成友好 key
            from PIL.ExifTags import Base as BaseTags  # type: ignore

            base_tags = {v: k for k, v in BaseTags.items()}

            make_raw = exif.get(base_tags.get("Make", 0x010F)) or ""
            model_raw = exif.get(base_tags.get("Model", 0x0110)) or ""
            lens_raw = exif_ifd.get(0xA434) or ""  # LensModel

            # 数值字段
            iso = exif_ifd.get(0x8827)  # ISOSpeedRatings
            fnum = exif_ifd.get(0x829D)  # FNumber
            expo = exif_ifd.get(0x829A)  # ExposureTime
            focal = exif_ifd.get(0x920A)  # FocalLength

            # 原 WB
            wb_mode = exif_ifd.get(0xA401)  # LightSource / WhiteBalance
            wb_label = _wb_mode_label(wb_mode)

            # 拍摄时间
            dt_raw = exif.get(base_tags.get("DateTime", 0x0132)) or exif_ifd.get(0x9003)

            return {
                "camera_make": _clean(make_raw),
                "camera_model": _clean(model_raw),
                "lens_model": _clean(lens_raw) if isinstance(lens_raw, str) else "",
                "iso": int(iso) if isinstance(iso, (int, float)) else None,
                "aperture": f"f/{fnum:.1f}" if fnum else None,
                "shutter": _format_shutter(expo) if expo else None,
                "focal_length": f"{int(focal)}mm" if focal else None,
                "white_balance": wb_label,
                "datetime": str(dt_raw) if dt_raw else None,
            }
    except Exception as e:
        logger.debug("parse_exif failed for %s: %s", path, e)
        return None


def _clean(s: Any) -> str:
    if s is None:
        return ""
    s = str(s).strip().strip("\x00")
    return s


def _format_shutter(expo: float) -> str:
    """把秒数格式化成 '1/200s' 或 '2s' 风格。"""
    if not isinstance(expo, (int, float)) or expo <= 0:
        return ""
    if expo >= 1:
        return f"{expo:.1f}s".rstrip("0").rstrip(".")
    # 1/x
    denom = 1 / expo
    if denom >= 2:
        return f"1/{int(round(denom))}s"
    return f"{expo:.2f}s".rstrip("0").rstrip(".")


def format_exif(exif: dict[str, Any] | None) -> str | None:
    """把 EXIF dict 拍平成 'Key: Value' 列表,跳过 None / 未知值。

    返回:
        - None:无数据
        - str:多行字符串,每行一个字段(摄影师友好)
    """
    if not exif:
        return None
    lines: list[str] = []
    key_order = [
        ("camera_make", "品牌"),
        ("camera_model", "型号"),
        ("lens_model", "镜头"),
        ("iso", "ISO"),
        ("aperture", "光圈"),
        ("shutter", "快门"),
        ("focal_length", "焦距"),
        ("white_balance", "原 WB"),
        ("datetime", "拍摄时间"),
    ]
    for k, label in key_order:
        v = exif.get(k)
        if v is None or v == "" or v == "Unknown":
            continue
        lines.append(f"- {label}: {v}")
    return "\n".join(lines) if lines else None


def _wb_mode_label(mode: Any) -> str | None:
    if mode is None:
        return None
    mapping = {
        0: "Auto",
        1: "Manual",
        2: "Tungsten",
        3: "Fluorescent",
        4: "Flash",
        5: "Daylight",
        6: "Cloudy",
        7: "Shadow",
        8: "Custom",
        9: "Color Temp",
        10: "Kelvin",
    }
    if isinstance(mode, int) and mode in mapping:
        return mapping[mode]
    return f"Mode {mode}"


# ---- 图像客观分析 ----

def analyze_image(path: Path, max_dim: int = 512) -> dict[str, Any]:
    """Read photo, downsample, extract:
    - luma histogram (8 段)
    - average saturation (HSV)
    - estimated color temp (K)
    - clipping (高光 / 阴影 比例)
    - skin tone ratio (粗略)
    """
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            # 缩到 512 加速分析;不影响结论
            w, h = im.size
            longest = max(w, h)
            if longest > max_dim:
                scale = max_dim / longest
                im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            # 算 luma 直方图(RGB 用 Rec.709)
            stat = ImageStat.Stat(im)
            mean_r, mean_g, mean_b = stat.mean  # 0..255
            luma = 0.2126 * mean_r + 0.7152 * mean_g + 0.0722 * mean_b
            # luma histogram(8 bins: 0-31, 32-63, ..., 224-255)
            gray = im.convert("L")
            hist = gray.histogram()  # 256 bins
            bins8 = [sum(hist[i*32:(i+1)*32]) for i in range(8)]
            total = sum(bins8)
            bins8_pct = [round(b * 100.0 / total, 1) for b in bins8] if total else [0]*8
            # 饱和度(HSV S channel mean)
            try:
                hsv = im.convert("HSV")
                s_mean = ImageStat.Stat(hsv.split()[1]).mean[0]  # 0..255
            except Exception:
                s_mean = 0
            # 阴影 / 高光 clip(取 R/G/B 任意 channel <5 或 >250 视为 clip)
            r_chan, g_chan, b_chan = im.split()
            r_hist = r_chan.histogram()
            g_hist = g_chan.histogram()
            b_hist = b_chan.histogram()
            shadow_clip = sum(r_hist[:3] + g_hist[:3] + b_hist[:3]) / (3 * total)
            highlight_clip = sum(r_hist[-3:] + g_hist[-3:] + b_hist[-3:]) / (3 * total)
            # 色温估计
            color_temp = estimate_color_temp(mean_r, mean_g, mean_b)
            # 肤色比例(粗略:橙色像素 — R 高 G 中 B 低)
            skin_ratio = _estimate_skin_ratio(im)
            return {
                "mean_rgb": (round(mean_r, 1), round(mean_g, 1), round(mean_b, 1)),
                "luma": round(luma, 1),
                "luma_histogram_8bins_pct": bins8_pct,  # 阴影→高光
                "saturation_mean": round(s_mean, 1),  # 0..255
                "shadow_clip_pct": round(shadow_clip * 100, 2),
                "highlight_clip_pct": round(highlight_clip * 100, 2),
                "color_temp_kelvin": color_temp,
                "skin_ratio_pct": round(skin_ratio * 100, 1),
            }
    except Exception as e:
        logger.debug("analyze_image failed for %s: %s", path, e)
        return {}


def _estimate_color_temp(mean_r: float, mean_g: float, mean_b: float) -> int:
    """非常粗略的色温估计:基于 R/B 比值映射到 Kelvin。
    仅供 M3 参考(不是物理准确)。"""
    if mean_b <= 0:
        mean_b = 0.01
    rb_ratio = mean_r / mean_b
    # rb_ratio > 1.4 → 暖(< 4000K),< 0.9 → 冷(> 7000K)
    if rb_ratio > 1.5:
        return 3200
    if rb_ratio > 1.2:
        return 4500
    if rb_ratio > 1.0:
        return 5500
    if rb_ratio > 0.85:
        return 6500
    return 8000


def _estimate_skin_ratio(im: Image.Image) -> float:
    """粗略肤色像素比例。肤色经验:R > 95, G 在 40-180, B 在 20-150, R > G > B。"""
    r, g, b = im.split()
    r_data = list(r.getdata())
    g_data = list(g.getdata())
    b_data = list(b.getdata())
    total = len(r_data)
    if total == 0:
        return 0.0
    skin = 0
    for i in range(0, total, max(1, total // 5000)):  # 采样,不全扫
        rv, gv, bv = r_data[i], g_data[i], b_data[i]
        if rv > 95 and 40 <= gv <= 180 and 20 <= bv <= 150 and rv > gv > bv:
            skin += 1
    sampled = max(1, total // 5000)
    return skin / sampled


def format_image_stats(stats: dict[str, Any]) -> str | None:
    """把 analyze_image() 的 dict 拍成 prompt 友好的字符串。"""
    if not stats:
        return None
    lines: list[str] = []
    if "luma" in stats:
        luma = stats["luma"]
        if luma < 60:
            exposure_state = "明显欠曝"
        elif luma < 100:
            exposure_state = "略暗"
        elif luma < 170:
            exposure_state = "正常"
        else:
            exposure_state = "偏亮 / 可能过曝"
        lines.append(f"- 平均亮度(Luma): {luma}/255  → {exposure_state}")
    if "luma_histogram_8bins_pct" in stats:
        h = stats["luma_histogram_8bins_pct"]
        # h[0] 阴影端, h[7] 高光端
        lines.append(
            f"- 直方图分布(8 段, %): {h}  (左=阴影, 右=高光)"
        )
    if "saturation_mean" in stats:
        sat = stats["saturation_mean"]
        sat_state = "高饱和" if sat > 150 else ("中等" if sat > 80 else "低饱和/接近黑白")
        lines.append(f"- 平均饱和度: {sat}/255  → {sat_state}")
    if "shadow_clip_pct" in stats and stats["shadow_clip_pct"] > 0.5:
        lines.append(f"- 阴影 clip: {stats['shadow_clip_pct']}%  → 暗部细节可能丢失")
    if "highlight_clip_pct" in stats and stats["highlight_clip_pct"] > 0.5:
        lines.append(f"- 高光 clip: {stats['highlight_clip_pct']}%  → 亮部细节可能丢失")
    if "color_temp_kelvin" in stats:
        lines.append(f"- 估计色温: ~{stats['color_temp_kelvin']}K")
    if "skin_ratio_pct" in stats and stats["skin_ratio_pct"] > 5:
        lines.append(f"- 肤色像素占比: {stats['skin_ratio_pct']}%  → 保护肤色优先级最高")
    if "mean_rgb" in stats:
        r, g, b = stats["mean_rgb"]
        lines.append(f"- 平均 RGB: ({r}, {g}, {b})")
    return "\n".join(lines) if lines else None
