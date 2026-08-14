"""
RAW image processing.

MVP scope: convert RAW (CR2/NEF/ARW/DNG) to a high-quality JPEG preview that
M3 can analyze. The original RAW file is never modified.

Future: round-trip back to DNG/TIFF with edits baked in (lossless workflow).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)


# Supported RAW extensions (lowercase, no dot)
SUPPORTED_RAW_EXTENSIONS: set[str] = {
    "cr2",  # Canon
    "cr3",  # Canon (newer; may need libraw >= 0.21)
    "nef",  # Nikon
    "arw",  # Sony
    "dng",  # Adobe Digital Negative (universal)
    # Future: "raf" (Fujifilm), "orf" (Olympus), "rw2" (Panasonic), "pef" (Pentax)
}


SUPPORTED_INPUT_EXTENSIONS: set[str] = {
    # RAW
    *SUPPORTED_RAW_EXTENSIONS,
    # Standard
    "jpg", "jpeg", "png", "heic", "webp", "tiff", "tif",
}


def is_raw(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in SUPPORTED_RAW_EXTENSIONS


def is_supported(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in SUPPORTED_INPUT_EXTENSIONS


def raw_to_jpeg(raw_path: Path, output_path: Path, quality: int = 92) -> Path:
    """
    Convert a RAW file to JPEG via rawpy (LibRaw).

    Args:
        raw_path: source RAW file
        output_path: destination JPEG path
        quality: JPEG quality (1-100, default 92)

    Returns:
        Path to the created JPEG.

    Raises:
        RuntimeError: if rawpy is unavailable or conversion fails.
    """
    try:
        import rawpy  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "rawpy not installed. Run: pip install rawpy"
        ) from e

    logger.info("Converting RAW %s → %s", raw_path.name, output_path.name)
    with rawpy.imread(str(raw_path)) as raw:
        # Use camera's default white balance for the preview; user can override
        # in the final color grade step.
        rgb = raw.postprocess(
            use_camera_wb=True,
            half_size=False,
            no_auto_bright=False,
            output_bps=8,
        )
    img = Image.fromarray(rgb)
    if img.mode != "RGB":
        img = img.convert("RGB")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="JPEG", quality=quality, optimize=True)
    return output_path


def to_jpeg_preview(input_path: Path, output_path: Path, max_edge: int = 2048) -> Path:
    """
    Convert any supported input (RAW or standard) to a JPEG preview suitable
    for M3 vision analysis.

    - RAW → rawpy → JPEG
    - Standard image → re-encoded JPEG (downsampled to max_edge)
    """
    if is_raw(input_path):
        # Always do an initial conversion; we downsample at JPEG-write time
        return raw_to_jpeg(input_path, output_path)

    with Image.open(input_path) as im:
        # Apply EXIF orientation BEFORE doing anything else. iPhone + many
        # mirrorless cameras store sensor-orientation pixels and tag them with
        # an Orientation EXIF value; without this, the preview is sideways
        # vs how the user actually composed the shot.
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGB")
        w, h = im.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / longest
            im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        im.save(output_path, format="JPEG", quality=88, optimize=True)
    return output_path
