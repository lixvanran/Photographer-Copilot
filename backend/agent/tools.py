"""
The tools the Agent can invoke.

These are the workhorse functions that mutate the workspace. The Agent
(M3 + function calling loop) decides which to call based on user intent.

Each tool returns a dict with:
- ok: bool
- data: structured result (JSON-serializable)
- error: optional human-readable error message

Tools are intentionally side-effectful but conservative: never delete
user data, never write outside the workspace, never make destructive
choices without explicit user confirmation.

Reserved for future:
- Group photo operations (event-level batch)
- Style-aware version (read style_profile.json before deciding params)
- Streaming progress per-photo (currently emit at end)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..db.catalog import Catalog
from ..image.analysis import (
    analyze_image,
    format_exif,
    format_image_stats,
    parse_exif,
)
from ..image.color_grade import apply_color_grade
from ..image.raw_processor import (
    is_raw,
    is_supported,
    to_jpeg_preview,
)
from .m3_client import (
    M3Client,
    M3AuthError,
    M3BadResponseError,
    M3NetworkError,
    M3QuotaError,
    M3ServerError,
)
from .prompts import build_cull_prompt, build_grade_prompt, format_user_feedback

logger = logging.getLogger(__name__)

# Module-level context for the currently active task.
# Set by cull/grade before processing so _emit() can route events.
_active_task_id: ContextVar[str | None] = ContextVar("_active_task_id", default=None)


# How many consecutive identical M3ServerError we tolerate before bailing.
# 5xx from upstream is often a transient blip; one retry is fair, but if
# every photo in a row hits the same 503, retrying is just burning time.
_MAX_CONSECUTIVE_M3_SERVER_ERRORS = 2


# -------- Tool registry & dispatcher --------

@dataclass
class ToolContext:
    workspace: Path
    catalog: Catalog
    m3: M3Client
    emit_event: Callable[[str, dict], Awaitable[None]]


# Tool implementations. Each takes (ctx, **kwargs) and returns dict.

async def list_input_folders(ctx: ToolContext) -> dict:
    """List all folders + loose files currently in workspace/input (excluding hidden).

    Returns:
        folders:      list of {name, path, mtime, photo_count}
        loose_files:  list of {name, path, size, is_raw, is_supported} (files at input/ root
                      that weren't wrapped in a subfolder)
    """
    input_dir = ctx.workspace / "input"
    if not input_dir.exists():
        return {"ok": True, "data": {"folders": [], "loose_files": []}}

    folders = []
    loose_files = []
    for p in sorted(input_dir.iterdir()):
        if p.name.startswith("."):
            continue
        if p.is_dir():
            photo_count = sum(1 for x in _iter_photos(p) if x is not None)
            folders.append({
                "name": p.name,
                "path": str(p),
                "mtime": p.stat().st_mtime,
                "photo_count": photo_count,
            })
        elif p.is_file() and is_supported(p):
            loose_files.append({
                "name": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "is_raw": is_raw(p),
            })
    return {
        "ok": True,
        "data": {"folders": folders, "loose_files": loose_files},
    }


# ---- Upload ----

# File extensions we accept for upload. Mirrors is_supported() in image/raw_processor.py.
# Tighter on purpose: webp/heic/tiff excluded per product decision
# (user wants only the formats their cull/grade pipeline actually handles well).
_UPLOAD_ALLOWED_EXTS: set[str] = {
    # RAW
    "arw", "cr2", "cr3", "nef", "dng",
    # Standard
    "jpg", "jpeg", "png",
}


def _sanitize_rel_path(raw_path: str) -> str | None:
    """
    Take a relative path like 'subdir/IMG_001.jpg' and return a clean version
    safe to join with the upload target dir. Returns None if it tries to
    escape (contains '..', absolute path, or is empty after normalization).
    """
    if not raw_path:
        return None
    # Reject absolute paths & Windows drive letters
    if raw_path.startswith("/") or raw_path.startswith("\\") or (len(raw_path) >= 2 and raw_path[1] == ":"):
        return None
    # Normalize separators
    cleaned = raw_path.replace("\\", "/")
    # Reject path traversal (check both: bare '..' segments and any '..' anywhere
    # in the path components before we drop them, so "../escape" doesn't sneak through
    # by being filterable to a clean relative path).
    if any(seg in ("..",) for seg in cleaned.split("/")):
        return None
    parts = [p for p in cleaned.split("/") if p not in ("", ".")]
    if not parts:
        return None
    # Reject hidden segments
    if any(p.startswith(".") for p in parts):
        return None
    # Reject control chars / NUL
    if any(ord(c) < 32 for c in cleaned):
        return None
    # Cap each segment length
    parts = [p[:200] for p in parts]
    return "/".join(parts)


async def save_uploads(
    ctx: ToolContext,
    files: list[tuple[str, Any]],
    folder_label: str | None = None,
    chunk_size: int = 1024 * 1024,
) -> dict:
    """
    Persist uploaded files to workspace/input/<时间>-uploaded[-<label>]/.
    `files` is a list of (rel_path, upload_file) tuples where upload_file is a
    FastAPI UploadFile (or any object exposing awaitable .read(n)). The
    rel_path is sanitized. Files with disallowed extensions are rejected and
    counted, not saved.

    Returns: {folder_name, accepted, rejected, total_bytes, files: [{rel_path, size, ext}]}
    """
    input_dir = ctx.workspace / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    label_suffix = ""
    if folder_label:
        safe_label = _sanitize_rel_path(folder_label)
        if safe_label:
            label_suffix = "-" + safe_label.replace("/", "_")[:60]
    target_dir = input_dir / f"{timestamp}-uploaded{label_suffix}"
    target_dir.mkdir(parents=True, exist_ok=True)

    accepted = []
    rejected = []
    total_bytes = 0

    for rel_path, upload_file in files:
        safe = _sanitize_rel_path(rel_path)
        if safe is None:
            rejected.append({"rel_path": rel_path, "reason": "invalid path"})
            continue
        ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
        if ext not in _UPLOAD_ALLOWED_EXTS:
            rejected.append({"rel_path": safe, "reason": f"unsupported type: .{ext}"})
            continue
        dest = target_dir / safe
        try:
            dest.resolve().relative_to(target_dir.resolve())
        except ValueError:
            rejected.append({"rel_path": safe, "reason": "path escape"})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with dest.open("wb") as f:
                while True:
                    chunk = await upload_file.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    size += len(chunk)
        except Exception as e:
            rejected.append({"rel_path": safe, "reason": f"write error: {e}"})
            continue
        if size == 0:
            dest.unlink(missing_ok=True)
            rejected.append({"rel_path": safe, "reason": "empty file"})
            continue
        total_bytes += size
        accepted.append({"rel_path": safe, "size": size, "ext": ext})

    return {
        "ok": True,
        "data": {
            "folder_name": target_dir.name,
            "folder_path": str(target_dir),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "total_bytes": total_bytes,
            "files": accepted,
            "rejected_files": rejected,
        },
    }


def _iter_photos(folder: Path, max_depth: int = 5):
    """Recursively yield (relative_path, full_path) for every supported photo under folder.

    Stops at max_depth to avoid runaway traversal on weird FS layouts. Skips
    hidden dirs and macOS / Windows system clutter.
    """
    skip_dirs = {".ds_store", "thumbs.db", "@eadir", ".thumbnails", "system volume information"}
    base = folder.resolve()

    def _walk(p: Path, depth: int):
        if depth > max_depth:
            return
        try:
            for child in sorted(p.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.is_dir():
                    if child.name.lower() in skip_dirs:
                        continue
                    yield from _walk(child, depth + 1)
                elif child.is_file() and is_supported(child):
                    yield (child.relative_to(base), child)
        except (PermissionError, OSError):
            return

    yield from _walk(folder, 0)


async def rename_to_in(ctx: ToolContext, folder_name: str) -> dict:
    """
    Rename a folder in input/ to `<YYYYMMDD-HHMMSS>-in` to mark it as a
    task in progress. Idempotent: if already renamed, returns success.
    """
    input_dir = ctx.workspace / "input"
    src = input_dir / folder_name
    if not src.exists() or not src.is_dir():
        return {"ok": False, "error": f"Folder not found: {folder_name}"}

    # Already renamed? Check pattern
    if re.match(r"^\d{8}-\d{6}-in$", src.name):
        return {"ok": True, "data": {"new_name": src.name, "already_renamed": True}}

    # Check for collision
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    dst = input_dir / f"{timestamp}-in"
    suffix = 1
    while dst.exists():
        dst = input_dir / f"{timestamp}_v{suffix}-in"
        suffix += 1

    src.rename(dst)
    logger.info("Renamed input folder: %s → %s", src.name, dst.name)
    return {"ok": True, "data": {"new_name": dst.name, "path": str(dst)}}


async def list_photos(ctx: ToolContext, folder_name: str) -> dict:
    """List all supported photos in a folder (recursively, up to 5 levels)."""
    folder = ctx.workspace / "input" / folder_name
    if not folder.exists():
        return {"ok": False, "error": f"Folder not found: {folder_name}"}

    photos = []
    for rel, p in _iter_photos(folder):
        photos.append({
            "name": p.name,
            "rel_path": str(rel),
            "path": str(p),
            "size": p.stat().st_size,
            "is_raw": is_raw(p),
            "ext": p.suffix.lower().lstrip("."),
        })
    return {"ok": True, "data": {"folder": folder_name, "photos": photos, "count": len(photos)}}


async def cull_photos(
    ctx: ToolContext,
    folder_name: str,
    task_id: str,
    scene_hint: str | None = None,
) -> dict:
    """
    Process all photos in a folder, asking M3 to decide keep/cull.
    Copies keepers to output/<时间>-out/. Never deletes from input.
    Special folder_name "__loose__" means "scan loose files in input/ root".
    """
    if folder_name == "__loose__":
        input_folder = ctx.workspace / "input"
    else:
        input_folder = ctx.workspace / "input" / folder_name
    if not input_folder.exists():
        return {"ok": False, "error": f"Folder not found: {folder_name}"}

    # Set task id on context so _emit can route to right task
    _active_task_id.set(task_id)

    # Determine output folder
    timestamp = _timestamp_from_in_name(folder_name) if folder_name != "__loose__" else None
    timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    output_folder = ctx.workspace / "output" / f"{timestamp}-out"
    output_folder.mkdir(parents=True, exist_ok=True)
    await _emit(ctx, "task_started", {"task_id": task_id, "type": "cull", "output": str(output_folder)})

    # List photos (recursive, dedup by absolute path)
    if folder_name == "__loose__":
        photos = sorted(
            p for p in input_folder.iterdir()
            if p.is_file() and is_supported(p) and not p.name.startswith(".")
        )
    else:
        photos = [p for _, p in _iter_photos(input_folder)]
    total = len(photos)
    if total == 0:
        msg = f"未在 {input_folder} 找到任何支持的照片文件"
        logger.warning(msg)
        await _emit(ctx, "task_done", {"task_id": task_id, "summary": msg})
        ctx.catalog.update_task(task_id, status="done", summary=msg)
        return {"ok": False, "error": msg, "data": {"task_id": task_id, "total": 0, "kept": 0, "culled": 0}}
    kept = 0
    culled = 0
    failed = 0

    # Set up previews dir
    previews_dir = ctx.workspace / ".tasks" / task_id / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    # Hoist fatal_err outside the loop so the final summary can detect
    # whether we exited via break (a fatal M3 error) or naturally.
    fatal_err: Exception | None = None
    for i, photo in enumerate(photos, 1):
        await _emit(ctx, "photo_progress", {
            "task_id": task_id,
            "current": i,
            "total": total,
            "photo": photo.name,
            "stage": "converting",
        })

        try:
            # Convert to preview JPEG for M3
            preview_path = previews_dir / f"{photo.stem}.jpg"
            await asyncio.to_thread(to_jpeg_preview, photo, preview_path)

            await _emit(ctx, "photo_progress", {
                "task_id": task_id,
                "current": i,
                "total": total,
                "photo": photo.name,
                "stage": "analyzing",
            })

            # Ask M3 — wrapped in retry once for transient M3ServerError.
            # Auth/quota/network errors skip the retry and bubble up
            # immediately (retrying a 401 won't help).
            prompt = build_cull_prompt()
            if scene_hint:
                prompt += f"\n摄影师提示:{scene_hint}"
            try:
                response = await ctx.m3.chat(
                    system="你是摄影师的筛片助手,严格按 schema 输出 JSON。",
                    user_text=prompt,
                    images=[preview_path],
                    response_format="json",
                )
            except M3ServerError as e:
                logger.warning("M3 5xx on cull %s, retrying once: %s", photo.name, e)
                response = await ctx.m3.chat(
                    system="你是摄影师的筛片助手,严格按 schema 输出 JSON。",
                    user_text=prompt,
                    images=[preview_path],
                    response_format="json",
                )
            decision = _parse_json_response(response)

            if not decision:
                raise ValueError(f"M3 returned invalid JSON: {response[:200]}")

            keep = bool(decision.get("keep", True))
            quality = int(decision.get("quality", 3))
            reasons = decision.get("reasons", [])
            tags = decision.get("tags", [])
            comment = decision.get("comment", "")

            # Copy keepers to output FIRST so we have a real dest path to
            # record in the catalog. (修"cull 完找不到 output 文件"——upsert_photo
            # 现在也收 output_path。)
            dest: Path | None = None
            if keep:
                dest = output_folder / photo.name
                if is_raw(photo):
                    dest = output_folder / f"{photo.stem}.jpg"
                await asyncio.to_thread(shutil.copy2, photo, dest)
                kept += 1
            else:
                culled += 1

            # Update catalog(写 output_path,前端就能给"打开文件"链接)
            photo_id = ctx.catalog.upsert_photo(
                task_id=task_id,
                source_path=str(photo),
                status="kept" if keep else "culled",
                quality_score=quality,
                keep=1 if keep else 0,
                reasons=reasons,
                tags=tags,
                comment=comment,
                output_path=str(dest) if dest else None,
            )

            await _emit(ctx, "photo_done", {
                "task_id": task_id,
                "photo_id": photo_id,
                "photo": photo.name,
                "keep": keep,
                "quality": quality,
                "comment": comment,
                # For kept photos, include the destination path so the UI
                # can link to / preview the result. None for culled ones.
                "output": str(dest) if keep else None,
            })

        except (M3AuthError, M3QuotaError, M3NetworkError) as e:
            # Fatal: retrying any further photo is pointless because the
            # next one will hit exactly the same wall. Bail out of the
            # loop, mark whatever we've already processed, and emit
            # task_done with a clear "the whole task was stopped" summary
            # so the UI shows why instead of a 0/N success rate.
            logger.error("Aborting cull task %s due to fatal M3 error: %s", task_id, e)
            fatal_err = e
            failed_photo_id = ctx.catalog.upsert_photo(
                task_id=task_id,
                source_path=str(photo),
                status="failed",
                error=f"任务中止: {e}",
            )
            failed += 1
            await _emit(ctx, "photo_failed", {
                "task_id": task_id,
                "photo_id": failed_photo_id,
                "photo": photo.name,
                "error": f"任务中止: {e}",
            })
            break
        except Exception as e:
            # Non-fatal: this specific photo is bad (JSON parse, M3
            # bad response, anything else). Skip and continue.
            logger.exception("Failed to cull photo %s", photo)
            failed_photo_id = ctx.catalog.upsert_photo(
                task_id=task_id,
                source_path=str(photo),
                status="failed",
                error=str(e),
            )
            failed += 1
            await _emit(ctx, "photo_failed", {
                "task_id": task_id,
                "photo_id": failed_photo_id,
                "photo": photo.name,
                "error": str(e),
            })

    if fatal_err is not None:
        summary = (
            f"⚠️ 筛片中途中止:已完成 {kept + culled}/{total} 张,失败 {failed} 张。"
            f"\n原因:{fatal_err}"
            f"\n建议:检查 .env 里的 M3_API_KEY / M3_BASE_URL / M3_MODEL,"
            f"或网络连接,修好后重新启动任务。"
        )
    else:
        summary = (
            f"筛片完成:共 {total} 张,保留 {kept} 张,剔除 {culled} 张,"
            f"失败 {failed} 张。输出目录:{output_folder.name}"
        )
    # 修 cull bug:把 output_folder 也写进 task catalog,前端 /tasks/{id} 返回
    # 时就能看到 output_path,UI 可以"打开 output 目录"按钮。
    ctx.catalog.update_task(
        task_id, status="done", summary=summary, output_folder=str(output_folder)
    )
    await _emit(ctx, "task_done", {"task_id": task_id, "summary": summary})

    return {
        "ok": True,
        "data": {
            "task_id": task_id,
            "output_folder": str(output_folder),
            "total": total,
            "kept": kept,
            "culled": culled,
            "failed": failed,
            "summary": summary,
        },
    }


async def grade_photos(
    ctx: ToolContext,
    folder_name: str,
    task_id: str,
    scene_hint: str | None = None,
) -> dict:
    """
    Process all photos in a folder, asking M3 to produce color grade params.
    Outputs graded JPEG + XMP sidecar to output/<时间>-out/.

    v0.2.1 升级:
    - scene_hint 注入 prompt(用户用自然语言告诉 AI 这张图想怎么用)
    - 每张照片先解析 EXIF + 客观图像分析,写进 prompt — M3 自己看图说话,
      不再给"风格 preset"让它套
    - 用户最近 👍/👎 调色记录作为风格倾向参考
    """
    if folder_name == "__loose__":
        input_folder = ctx.workspace / "input"
    else:
        input_folder = ctx.workspace / "input" / folder_name
    if not input_folder.exists():
        return {"ok": False, "error": f"Folder not found: {folder_name}"}

    _active_task_id.set(task_id)

    timestamp = _timestamp_from_in_name(folder_name) if folder_name != "__loose__" else None
    timestamp = timestamp or time.strftime("%Y%m%d-%H%M%S")
    output_folder = ctx.workspace / "output" / f"{timestamp}-out"
    output_folder.mkdir(parents=True, exist_ok=True)
    await _emit(ctx, "task_started", {"task_id": task_id, "type": "grade", "output": str(output_folder)})

    if folder_name == "__loose__":
        photos = sorted(
            p for p in input_folder.iterdir()
            if p.is_file() and is_supported(p) and not p.name.startswith(".")
        )
    else:
        photos = [p for _, p in _iter_photos(input_folder)]
    total = len(photos)
    if total == 0:
        msg = f"未在 {input_folder} 找到任何支持的照片文件"
        logger.warning(msg)
        await _emit(ctx, "task_done", {"task_id": task_id, "summary": msg})
        ctx.catalog.update_task(task_id, status="done", summary=msg)
        return {"ok": False, "error": msg, "data": {"task_id": task_id, "total": 0, "graded": 0, "failed": 0}}
    graded = 0
    failed = 0

    previews_dir = ctx.workspace / ".tasks" / task_id / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    # v0.3.0:提前把"用户历史反馈"取出来,所有照片共用同一段(省一次 DB 查)。
    # 只在 loop 外查一次,避免每张照片都重读 catalog。
    user_feedback_str: str | None = None
    try:
        recent_feedback = ctx.catalog.recent_feedback(limit=10)
        # recent_feedback 里的 grade_params 是 JSON 字符串,format_user_feedback
        # 期望 dict。normalize 一下。
        normalized: list[dict[str, Any]] = []
        for f in recent_feedback:
            gp = f.get("grade_params")
            if isinstance(gp, str):
                try:
                    gp = json.loads(gp)
                except Exception:
                    gp = None
            if not isinstance(gp, dict):
                continue
            normalized.append({
                "grade_params": gp,
                "feedback": f.get("feedback"),
            })
        user_feedback_str = format_user_feedback(normalized)
    except Exception as e:
        logger.debug("recent_feedback failed: %s", e)

    # Hoist fatal_err outside the loop so the final summary can detect
    # whether we exited via break (a fatal M3 error) or naturally.
    fatal_err: Exception | None = None
    for i, photo in enumerate(photos, 1):
        await _emit(ctx, "photo_progress", {
            "task_id": task_id,
            "current": i,
            "total": total,
            "photo": photo.name,
            "stage": "converting",
        })

        try:
            preview_path = previews_dir / f"{photo.stem}.jpg"
            await asyncio.to_thread(to_jpeg_preview, photo, preview_path)

            await _emit(ctx, "photo_progress", {
                "task_id": task_id,
                "current": i,
                "total": total,
                "photo": photo.name,
                "stage": "analyzing",
            })

            # v0.3.0:每张照片的 prompt 注入 EXIF + 客观图像分析
            # (同步跑 CPU 工作,几 ms 级别;但因为是同步阻塞,包到 to_thread 里)
            exif_str: str | None = None
            image_stats_str: str | None = None
            try:
                exif_dict = await asyncio.to_thread(parse_exif, photo)
                exif_str = format_exif(exif_dict)
                stats = await asyncio.to_thread(analyze_image, preview_path)
                image_stats_str = format_image_stats(stats)
            except Exception as e:
                logger.debug("exif/analyze failed for %s: %s", photo, e)

            prompt = build_grade_prompt(
                scene_hint=scene_hint,
                exif_summary=exif_str,
                image_stats=image_stats_str,
                user_feedback=user_feedback_str,
            )
            # Same retry policy as cull: one retry on 5xx, immediate
            # abort on 401/402/429/network. See the cull_photos loop for
            # the rationale.
            try:
                response = await ctx.m3.chat(
                    system="你是摄影后期的色彩师,严格按 schema 输出 JSON。",
                    user_text=prompt,
                    images=[preview_path],
                    response_format="json",
                )
            except M3ServerError as e:
                logger.warning("M3 5xx on grade %s, retrying once: %s", photo.name, e)
                response = await ctx.m3.chat(
                    system="你是摄影后期的色彩师,严格按 schema 输出 JSON。",
                    user_text=prompt,
                    images=[preview_path],
                    response_format="json",
                )
            params = _parse_json_response(response)
            if not params:
                raise ValueError(f"M3 returned invalid JSON: {response[:200]}")

            await _emit(ctx, "photo_progress", {
                "task_id": task_id,
                "current": i,
                "total": total,
                "photo": photo.name,
                "stage": "grading",
            })

            # Apply grade to the original image
            # (For RAW: apply to the preview JPEG we just made. This is MVP;
            #  future: bake into DNG or write a sidecar-only result.)
            if is_raw(photo):
                source_for_grade = preview_path
            else:
                source_for_grade = photo

            out_name = photo.stem + ".jpg" if is_raw(photo) else photo.name
            output_path = output_folder / out_name
            await asyncio.to_thread(apply_color_grade, source_for_grade, output_path, params)

            photo_id = ctx.catalog.upsert_photo(
                task_id=task_id,
                source_path=str(photo),
                output_path=str(output_path),
                status="graded",
                grade_params=params,
                comment=params.get("notes", ""),
            )
            graded += 1

            await _emit(ctx, "photo_done", {
                "task_id": task_id,
                "photo_id": photo_id,
                "photo": photo.name,
                "output": str(output_path),
                "params_summary": {k: v for k, v in params.items() if k != "hsl"},
            })

        except (M3AuthError, M3QuotaError, M3NetworkError) as e:
            # Fatal: same reasoning as cull_photos. Bail and surface the
            # error in the final summary so the user knows the task was
            # stopped, not "0/N success".
            logger.error("Aborting grade task %s due to fatal M3 error: %s", task_id, e)
            fatal_err = e
            failed_photo_id = ctx.catalog.upsert_photo(
                task_id=task_id,
                source_path=str(photo),
                status="failed",
                error=f"任务中止: {e}",
            )
            failed += 1
            await _emit(ctx, "photo_failed", {
                "task_id": task_id,
                "photo_id": failed_photo_id,
                "photo": photo.name,
                "error": f"任务中止: {e}",
            })
            break
        except Exception as e:
            logger.exception("Failed to grade photo %s", photo)
            failed_photo_id = ctx.catalog.upsert_photo(
                task_id=task_id,
                source_path=str(photo),
                status="failed",
                error=str(e),
            )
            failed += 1
            await _emit(ctx, "photo_failed", {
                "task_id": task_id,
                "photo_id": failed_photo_id,
                "photo": photo.name,
                "error": str(e),
            })

    if fatal_err is not None:
        summary = (
            f"⚠️ 修图中途中止:已完成 {graded}/{total} 张,失败 {failed} 张。"
            f"\n原因:{fatal_err}"
            f"\n建议:检查 .env 里的 M3_API_KEY / M3_BASE_URL / M3_MODEL,"
            f"或网络连接,修好后重新启动任务。"
        )
    else:
        summary = (
            f"修图完成:共 {total} 张,成功 {graded} 张,失败 {failed} 张。"
            f"输出目录:{output_folder.name} (JPEG + XMP sidecar)"
        )
    # 同样把 output_folder 写进 task catalog(同 cull_photos 修复)。
    ctx.catalog.update_task(
        task_id, status="done", summary=summary, output_folder=str(output_folder)
    )
    await _emit(ctx, "task_done", {"task_id": task_id, "summary": summary})

    return {
        "ok": True,
        "data": {
            "task_id": task_id,
            "output_folder": str(output_folder),
            "total": total,
            "graded": graded,
            "failed": failed,
            "summary": summary,
        },
    }


async def set_photo_feedback(
    ctx: ToolContext, photo_id: int, feedback: str
) -> dict:
    """Record user's 👍/👎 on a graded photo. Data for future style learning."""
    if feedback not in ("up", "down"):
        return {"ok": False, "error": "feedback must be 'up' or 'down'"}
    ctx.catalog.set_photo_feedback(photo_id, feedback)
    return {"ok": True, "data": {"photo_id": photo_id, "feedback": feedback}}


async def ask_photography(
    ctx: ToolContext, question: str
) -> dict:
    """Answer a photography knowledge question using M3."""
    if not question.strip():
        return {"ok": False, "error": "Empty question"}

    system = (
        "你是一名资深摄影讲师。回答摄影相关问题时:\n"
        "1. 简洁(默认 200 字以内),除非用户明确要展开\n"
        "2. 给出可操作的具体建议,而非抽象概念\n"
        "3. 必要时用 1-2 个例子说明\n"
        "4. 不要泛泛而谈,要有针对性"
    )

    chunks: list[str] = []
    async for chunk in ctx.m3.stream_chat(
        system=system, user_text=question, temperature=0.4
    ):
        chunks.append(chunk)
    answer = "".join(chunks)
    return {"ok": True, "data": {"answer": answer}}


async def finalize_output(ctx: ToolContext, task_id: str) -> dict:
    """Mark task as finalized. Returns the output folder path."""
    task = ctx.catalog.get_task(task_id)
    if not task:
        return {"ok": False, "error": f"Task not found: {task_id}"}
    output_folder = task.get("output_folder")
    if not output_folder:
        return {"ok": False, "error": "No output folder recorded for this task"}
    ctx.catalog.update_task(task_id, status="finalized")
    return {"ok": True, "data": {"output_folder": output_folder}}


async def emit_event(ctx: ToolContext, event: str, payload: dict) -> dict:
    """Allow the agent to broadcast a custom event to the UI."""
    await _emit(ctx, event, payload)
    return {"ok": True, "data": {"emitted": event}}


# -------- Tool registry (exposed to the agent's function-calling loop) --------

TOOL_REGISTRY: dict[str, Callable[..., Awaitable[dict]]] = {
    "list_input_folders": list_input_folders,
    "rename_to_in": rename_to_in,
    "list_photos": list_photos,
    "cull_photos": cull_photos,
    "grade_photos": grade_photos,
    "set_photo_feedback": set_photo_feedback,
    "ask_photography": ask_photography,
    "finalize_output": finalize_output,
    "emit_event": emit_event,
}


# -------- OpenAI-compatible tool schemas for function calling --------

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "list_input_folders",
            "description": "列出当前 workspace/input/ 里所有待处理的文件夹,让用户选择。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_to_in",
            "description": "把选中的 input 文件夹重命名为 `<时间>-in` 标记任务开始。",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "input 文件夹名"}
                },
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_photos",
            "description": "列出某文件夹里的所有照片(支持 RAW/JPG/PNG)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string"}
                },
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grade_photos",
            "description": "对文件夹中所有照片执行一键调色/修图,输出 JPEG+XMP 到 output。",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string"},
                    "task_id": {"type": "string"},
                    "scene_hint": {
                        "type": "string",
                        "description": "可选,摄影师用自然语言写的意图(场景/保留/避开什么),例如 '逆光人像,保留背景冷蓝对比'",
                    },
                },
                "required": ["folder_name", "task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cull_photos",
            "description": "对文件夹中所有照片执行废片筛取,合格品复制到 output,绝不删除原图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string"},
                    "task_id": {"type": "string"},
                    "scene_hint": {"type": "string"},
                },
                "required": ["folder_name", "task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_photo_feedback",
            "description": "记录用户对某张修图结果的 👍/👎 反馈。",
            "parameters": {
                "type": "object",
                "properties": {
                    "photo_id": {"type": "integer"},
                    "feedback": {"type": "string", "enum": ["up", "down"]},
                },
                "required": ["photo_id", "feedback"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_photography",
            "description": "回答摄影知识问题(光圈、快门、构图、后期等)。",
            "parameters": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finalize_output",
            "description": "标记任务完成,锁定输出目录。",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emit_event",
            "description": "向前端 UI 广播自定义事件(进度、提示等)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "event": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["event", "payload"],
            },
        },
    },
]


# -------- Helpers --------

def _timestamp_from_in_name(name: str) -> str | None:
    m = re.match(r"^(\d{8}-\d{6})_?v?\d*-in$", name)
    return m.group(1) if m else None


def _parse_json_response(response: str) -> dict | None:
    """Extract JSON object from M3 response, handling markdown fences."""
    # Strip markdown code fences
    text = response.strip()
    if text.startswith("```"):
        # Remove first and last fence
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    # Find the first { ... } block
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


async def _emit(ctx: ToolContext, event: str, payload: dict) -> None:
    """Forward a tool event to the active task's SSE subscribers."""
    try:
        await ctx.emit_event(event, payload)
    except Exception as e:
        logger.warning("emit_event failed (non-fatal): %s", e)
