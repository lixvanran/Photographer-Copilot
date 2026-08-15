"""
FastAPI entry point for the Python sidecar.

Endpoints (called by the Tauri Rust core):
- GET  /health                  → sidecar health + config status
- GET  /config                  → current M3 + workspace config
- GET  /workspace               → workspace info
- GET  /input/folders           → list input folders
- POST /tasks/grade             → start a grading task (returns task_id)
- POST /tasks/cull              → start a culling task
- GET  /tasks/{id}              → task status + summary
- GET  /tasks/{id}/photos       → photos in a task
- POST /photos/{id}/feedback    → 👍/👎 on a photo
- POST /chat                    → send a chat message (M3 knowledge Q&A)
- GET  /events/{task_id}        → SSE stream of task events

Sidecar discovery:
- Reads port from env SIDECAR_PORT, or picks a free port and writes to
  workspace/.sidecar-port so Rust can find it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Ensure relative imports work when run as `python -m agent.main`
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.agent.m3_client import M3Client, M3Config
from backend.agent.tools import (
    ToolContext,
    _active_task_id,
    ask_photography,
    cull_photos,
    grade_photos,
    list_input_folders,
    rename_to_in,
    save_uploads,
    set_photo_feedback,
)
from backend.image.analysis import (
    analyze_image,
    format_exif,
    format_image_stats,
    parse_exif,
)
from backend.agent.prompts import build_analyze_prompt
from backend.db.catalog import Catalog


# ----- Logging setup -----
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sidecar")


# ----- Paths -----
def _resolve_workspace() -> Path:
    """Resolve the data workspace path.

    Priority:
    1. WORKSPACE_PATH env var (only if non-empty)
    2. Default: <project_root>/workspace/  (project_root is parent of sidecar/)
       We derive it from __file__ to avoid being affected by cwd.
    """
    env = os.environ.get("WORKSPACE_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    # sidecar/agent/main.py → sidecar/agent → sidecar → project_root
    project_root = Path(__file__).resolve().parent.parent.parent
    return (project_root / "workspace").resolve()


WORKSPACE = _resolve_workspace()
WORKSPACE.mkdir(parents=True, exist_ok=True)
(input_dir := WORKSPACE / "input").mkdir(exist_ok=True)
(output_dir := WORKSPACE / "output").mkdir(exist_ok=True)
(logs_dir := WORKSPACE / ".logs").mkdir(exist_ok=True)
(tasks_dir := WORKSPACE / ".tasks").mkdir(exist_ok=True)

# File log — 挂到 root 让所有子 logger 也写进来。
# (历史版本只挂 `sidecar`,所以 m3_client / tools / color_grade 的日志
# 不会进 sidecar.log,排查问题时很迷。)
fh = logging.FileHandler(logs_dir / "sidecar.log", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
root_logger = logging.getLogger()
if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "").endswith("sidecar.log") for h in root_logger.handlers):
    root_logger.addHandler(fh)
    # 顺手把 root level 拉到和 LOG_LEVEL 一致(避免 uvicorn / httpx 之类
    # 子 logger 因为 root level 过高被静默吞掉)
    root_logger.setLevel(LOG_LEVEL)


# ----- Global state -----
import collections

class State:
    m3: M3Client | None = None
    catalog: Catalog | None = None
    event_queues: dict[str, list[asyncio.Queue]] = {}  # task_id → list of subscriber queues
    # Per-task ring buffer of events. New SSE subscribers get the buffer replayed
    # first so they don't miss task_started / early photo_progress that fired
    # before they connected.
    event_buffers: dict[str, collections.deque] = {}
    # Pending start events (one per task_id) — set when an SSE subscriber connects
    # for a task that hasn't actually started yet (e.g. race after start_grade).
    pending_start: dict[str, asyncio.Event] = {}

state = State()


# ----- Lifespan -----
@asynccontextmanager
async def lifespan(app: FastAPI):
    state.m3 = M3Client()
    state.catalog = Catalog(WORKSPACE / "catalog.sqlite")
    logger.info("Sidecar ready. Workspace=%s, M3 mock=%s",
                WORKSPACE, state.m3.config.is_mock)

    # 主动验证 M3 key —— 在用户开 UI 看到"修图/chat 全失败"前,先把问题
    # 摆到 sidecar 启动日志里,免得用户以为代码又坏了。验证失败不阻止
    # 启动 —— UI 后续会显示同样的错误,用户能马上定位 .env 配错。
    if not state.m3.config.is_mock:
        try:
            ok, msg = await state.m3.verify_key()
            if ok:
                logger.info("M3 key 验证通过 — %s (model=%s)", msg, state.m3.config.model)
            else:
                logger.warning("M3 key 验证失败 — %s", msg)
                logger.warning("  → 修图 / chat 会返回 401。请检查 .env 里的 M3_API_KEY / M3_BASE_URL / M3_MODEL。")
        except Exception as e:
            logger.warning("M3 key 验证异常(网络/超时): %s", e)

    yield
    if state.m3:
        await state.m3.close()


app = FastAPI(title="Photographer Copilot Sidecar", lifespan=lifespan)

# 允许浏览器直连(绕开 Tauri 时用)。这是本地工具,所有 localhost / 127.0.0.1
# 端口都放行(任意 Vite 端口),也允许 Tauri 桌面壳。
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|^tauri://localhost$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- Event broadcast (for SSE) -----
_EVENT_BUFFER_MAX = 200  # events per task kept for late SSE subscribers

async def _broadcast(event: str, payload: dict) -> None:
    """Forward a tool event to the active task's SSE subscribers.

    Also writes to a per-task ring buffer so subscribers that connect
    AFTER events fire still get the history (replayed on connect).
    """
    task_id = _active_task_id.get()
    if not task_id:
        return  # outside a task scope — drop
    entry = {"event": event, "payload": payload, "ts": time.time()}
    # 1. buffer (always)
    buf = state.event_buffers.get(task_id)
    if buf is None:
        buf = collections.deque(maxlen=_EVENT_BUFFER_MAX)
        state.event_buffers[task_id] = buf
    buf.append(entry)
    # 2. live subscribers (if any)
    queues = state.event_queues.get(task_id, [])
    dead = []
    for q in queues:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            queues.remove(q)
        except ValueError:
            pass


def make_tool_context() -> ToolContext:
    assert state.m3 is not None and state.catalog is not None
    return ToolContext(
        workspace=WORKSPACE,
        catalog=state.catalog,
        m3=state.m3,
        emit_event=_broadcast,
    )


def _unwrap(result: Any) -> Any:
    """HTTP 边界解包:工具返回 `{"ok": bool, "data"|"error": ...}`,这里剥掉壳。

    约定:工具是给 M3 用的,带 {ok, data} 包装;HTTP 路由给前端用,返回原生 data。
    如果 tool 返回 False,把 error 抛 400 让前端能看到。
    """
    if not isinstance(result, dict):
        return result
    if "ok" in result and ("data" in result or "error" in result):
        if result.get("ok") is True:
            return result.get("data", {})
        if result.get("ok") is False:
            raise HTTPException(400, result.get("error", "tool failed"))
    return result


# ----- Models -----
class GradeRequest(BaseModel):
    folder_name: str
    scene_hint: str | None = None
    # v0.2.2:删 style_preset — 套预设是套滤镜,让 AI 自己看图说话


class CullRequest(BaseModel):
    folder_name: str
    scene_hint: str | None = None


class FeedbackRequest(BaseModel):
    feedback: str = Field(..., pattern="^(up|down)$")


class ChatRequest(BaseModel):
    message: str


# ----- Endpoints -----
@app.get("/health")
async def health():
    return {
        "ok": True,
        "workspace": str(WORKSPACE),
        "m3_mock": state.m3.config.is_mock if state.m3 else None,
        "m3_model": state.m3.config.model if state.m3 else None,
    }


@app.get("/config")
async def get_config():
    cfg = state.m3.config if state.m3 else None
    return {
        "workspace": str(WORKSPACE),
        "m3_base_url_set": bool(cfg.base_url) if cfg else False,
        "m3_api_key_set": bool(cfg.api_key) if cfg else False,
        "m3_model": cfg.model if cfg else "MiniMax-M3",
        "m3_mock": cfg.is_mock if cfg else None,
    }


# 注意:历史上的 /events/log (SSE) 和 /logs/recent (HTTP) 已经被拿掉。
# 后端活动现在通过 start.py 让 sidecar 的 stdout/stderr 直接继承给启动
# 脚本,日志全部显示在用户运行的 cmd 里。文件日志仍在
# workspace/.logs/sidecar.log,可随时 tail 查看历史。


@app.get("/workspace")
async def workspace_info():
    return {
        "path": str(WORKSPACE),
        "input_exists": (WORKSPACE / "input").exists(),
        "output_exists": (WORKSPACE / "output").exists(),
        "tasks_dir": str(WORKSPACE / ".tasks"),
    }


@app.get("/input/folders")
async def list_folders():
    ctx = make_tool_context()
    result = await list_input_folders(ctx)
    return _unwrap(result)


@app.post("/input/rename")
async def rename_input(req: dict):
    folder_name = req.get("folder_name")
    if not folder_name:
        raise HTTPException(400, "folder_name required")
    ctx = make_tool_context()
    return _unwrap(await rename_to_in(ctx, folder_name=folder_name))


# ---- Upload ----

# Hard caps to prevent abuse / accidental DoS.
_UPLOAD_MAX_FILES = 5000
_UPLOAD_MAX_FILE_BYTES = 500 * 1024 * 1024       # 500 MB / file
_UPLOAD_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024 # 2 GB / request


@app.post("/upload")
async def upload(
    files: list[UploadFile] = File(..., description="上传的文件/文件夹(webkitdirectory 模式 path 在 filename 里)"),
    folder_label: str | None = Form(None, description="可选文件夹名(用户给上传批次起的名字)"),
):
    """
    接收 multipart 上传,落盘到 workspace/input/<时间>-uploaded[-<label>]/
    只接受 arw/cr2/cr3/nef/dng/jpg/jpeg/png。其他文件拒绝并计数。
    """
    # 修"文件传不上去"的关键修复:
    # 1) Starlette 在解析 multipart 时,如果单个 part 没有 Content-Length
    #    或者 file.size 为 None,会让 pre-check 漏掉大文件导致后面读时
    #    报 413,但日志看不出来。我们把"claimed size" 改成"按 1MB/张
    #    估算"作为兜底,避免误判。
    # 2) 所有失败路径都打 warn logger,用户在 cmd 里能看到。
    # 3) Webkitdirectory 模式下 filename 里可能含 "\",不是 "/"。统一
    #    修正。
    if not files:
        logger.warning("Upload request with no files")
        raise HTTPException(400, "no files provided")
    if len(files) > _UPLOAD_MAX_FILES:
        logger.warning("Upload rejected: too many files (%d > %d)", len(files), _UPLOAD_MAX_FILES)
        raise HTTPException(413, f"too many files (max {_UPLOAD_MAX_FILES})")

    # Pre-check total claimed size. UploadFile.size may be None for streamed
    # uploads — fall back to a conservative per-file estimate so we still
    # catch obvious DoS attempts.
    claimed = 0
    for f in files:
        if f.size is not None:
            claimed += f.size
        else:
            # Unknown: assume a typical raw size of 50 MB so the cap is safe.
            claimed += 50 * 1024 * 1024
    if claimed > _UPLOAD_MAX_TOTAL_BYTES:
        logger.warning(
            "Upload rejected: payload too large (%d bytes > %d bytes)",
            claimed, _UPLOAD_MAX_TOTAL_BYTES,
        )
        raise HTTPException(413, f"payload too large (>{_UPLOAD_MAX_TOTAL_BYTES // (1024*1024)} MB)")

    pairs: list[tuple[str, Any]] = []
    for f in files:
        # Filename from webkitdirectory mode is "subdir/IMG_001.jpg" via relativePath.
        # Some browsers / OSes (Windows with backslash separators) send
        # "subdir\IMG_001.jpg" — tools._sanitize_rel_path already normalizes
        # that, but we log the raw form to help debugging.
        rel = f.filename or "unnamed"
        if (f.size or 0) > _UPLOAD_MAX_FILE_BYTES:
            # Skip oversized but don't abort the whole batch
            logger.warning("Skip oversized file: %s (%d bytes)", rel, f.size)
            continue
        pairs.append((rel, f))

    if not pairs:
        logger.warning(
            "Upload had %d files but all skipped (oversized or invalid). "
            "Check file types and sizes.",
            len(files),
        )
        raise HTTPException(400, "no valid files to save (all skipped — check file types/sizes)")

    logger.info("Upload start: %d file(s), label=%r", len(pairs), folder_label)

    try:
        ctx = make_tool_context()
        result = await save_uploads(ctx, files=pairs, folder_label=folder_label)
    except Exception as e:
        logger.exception("Upload failed mid-write")
        raise HTTPException(500, f"upload failed: {e}") from e

    # _unwrap: if save_uploads returned ok:False, surface the error to the
    # browser as a 4xx so the XHR handler can show it.
    try:
        return _unwrap(result)
    except HTTPException as e:
        logger.warning("Upload partial-fail: %s", e.detail)
        raise
    else:
        # Log a concise success line so users can confirm in the cmd.
        try:
            data = result.get("data", {}) if isinstance(result, dict) else {}
            logger.info(
                "Upload done: %d accepted, %d rejected, → %s",
                data.get("accepted", 0), data.get("rejected", 0), data.get("folder_name", "?"),
            )
        except Exception:
            pass


@app.post("/tasks/grade")
async def start_grade(req: GradeRequest):
    task_id = str(uuid.uuid4())
    state.catalog.create_task(task_id, "grade", req.folder_name, req.model_dump())
    ctx = make_tool_context()
    # Wait for the front-end to subscribe to /events/{task_id} before kicking
    # off the actual work, so task_started doesn't get lost. Times out gracefully
    # after 3s if the front-end never subscribes.
    state.pending_start[task_id] = asyncio.Event()
    asyncio.create_task(_run_grade_when_ready(ctx, task_id, req))
    return {"task_id": task_id, "status": "started"}


@app.post("/tasks/cull")
async def start_cull(req: CullRequest):
    task_id = str(uuid.uuid4())
    state.catalog.create_task(task_id, "cull", req.folder_name, req.model_dump())
    ctx = make_tool_context()
    state.pending_start[task_id] = asyncio.Event()
    asyncio.create_task(_run_cull_when_ready(ctx, task_id, req))
    return {"task_id": task_id, "status": "started"}


async def _run_grade_when_ready(ctx, task_id, req):
    evt = state.pending_start.get(task_id)
    if evt:
        try:
            await asyncio.wait_for(evt.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
    await grade_photos(
        ctx,
        folder_name=req.folder_name,
        task_id=task_id,
        scene_hint=req.scene_hint,
    )


async def _run_cull_when_ready(ctx, task_id, req):
    evt = state.pending_start.get(task_id)
    if evt:
        try:
            await asyncio.wait_for(evt.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
    await cull_photos(
        ctx, folder_name=req.folder_name, task_id=task_id, scene_hint=req.scene_hint
    )


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = state.catalog.get_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    photos = state.catalog.list_task_photos(task_id)
    return {"task": task, "photos": photos, "photo_count": len(photos)}


@app.post("/photos/{photo_id}/feedback")
async def photo_feedback(photo_id: int, req: FeedbackRequest):
    ctx = make_tool_context()
    return await set_photo_feedback(ctx, photo_id=photo_id, feedback=req.feedback)


@app.post("/chat")
async def chat(req: ChatRequest):
    """Quick chat: ask a photography question, get a streaming SSE response."""
    ctx = make_tool_context()

    async def gen():
        try:
            result = await ask_photography(ctx, question=req.message)
        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'payload': str(e)})}\n\n"
            return
        if not result.get("ok"):
            yield f"data: {json.dumps({'event': 'error', 'payload': result.get('error', 'unknown')})}\n\n"
            return
        # Stream the answer in chunks
        answer = result["data"]["answer"]
        chunk_size = 16
        for i in range(0, len(answer), chunk_size):
            payload = json.dumps({"event": "chunk", "payload": {"text": answer[i:i+chunk_size]}}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
            await asyncio.sleep(0.02)
        yield f"data: {json.dumps({'event': 'done', 'payload': {}})}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/analyze/photo")
async def analyze_photo(
    file: UploadFile = File(..., description="要分析的照片"),
):
    """v0.2.3:AI 看图 — 上传单图,返回 5 维评价 + 问题清单 + 修图建议(JSON)。

    跟 grade 的区别:grade 直接给调色参数并 apply 到图;
    analyze 只给文字评价和建议,不修改原图。
    """
    if not file or not file.filename:
        raise HTTPException(400, "no file provided")
    # 限制大小:AI 看图不要太大,30MB 够
    raw = await file.read()
    if len(raw) > 30 * 1024 * 1024:
        raise HTTPException(413, f"file too large ({len(raw)} bytes > 30MB)")
    # 落到临时目录
    suffix = Path(file.filename).suffix.lower() or ".jpg"
    if suffix not in {".jpg", ".jpeg", ".png"}:
        raise HTTPException(400, f"unsupported format: {suffix}")
    analyze_id = str(uuid.uuid4())[:8]
    analyze_dir = Path(__file__).resolve().parents[2] / "workspace" / ".analyze" / analyze_id
    analyze_dir.mkdir(parents=True, exist_ok=True)
    photo_path = analyze_dir / f"input{suffix}"
    photo_path.write_bytes(raw)
    # 缩成 preview(给 M3 看),省 token
    preview_path = analyze_dir / "preview.jpg"
    from backend.image.raw_processor import to_jpeg_preview
    try:
        await asyncio.to_thread(to_jpeg_preview, photo_path, preview_path)
    except Exception as e:
        logger.warning("analyze: to_jpeg_preview failed: %s", e)
        # fallback:用原图
        preview_path = photo_path
    # 客观分析 + EXIF
    exif_str = image_stats_str = None
    try:
        exif_dict = await asyncio.to_thread(parse_exif, photo_path)
        exif_str = format_exif(exif_dict)
        stats = await asyncio.to_thread(analyze_image, preview_path)
        image_stats_str = format_image_stats(stats)
    except Exception as e:
        logger.debug("analyze: exif/stats failed: %s", e)
    # 调 M3
    prompt = build_analyze_prompt(
        exif_summary=exif_str,
        image_stats=image_stats_str,
    )
    try:
        response = await state.m3.chat(
            system="你是摄影教学教练 + 资深后期师,严格按 schema 输出 JSON。",
            user_text=prompt,
            images=[preview_path],
            response_format="json",
        )
    except Exception as e:
        raise HTTPException(500, f"M3 调用失败: {e}")
    # parse JSON(剥 markdown fence)
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise HTTPException(500, f"M3 returned invalid JSON: {text[:200]}")
    try:
        report = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"JSON parse failed: {e}")
    return {
        "analyze_id": analyze_id,
        "photo_path": str(photo_path),
        "report": report,
    }


@app.get("/events/{task_id}")
async def task_events(task_id: str):
    """SSE stream for task events.

    Replays the per-task event buffer first so late subscribers (i.e. the
    front-end EventSource, which is created AFTER the task starts running)
    don't miss task_started and early photo_progress events.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    state.event_queues.setdefault(task_id, []).append(queue)
    # If a start_grade/cull is still waiting for the frontend to subscribe,
    # signal it now so the actual work can begin.
    pending = state.pending_start.pop(task_id, None)
    if pending:
        pending.set()

    async def gen():
        try:
            # 1. Hello
            yield f"data: {json.dumps({'event': 'connected', 'payload': {'task_id': task_id}})}\n\n"
            # 2. Replay any events that fired before we connected
            buf = list(state.event_buffers.get(task_id, []))
            for ev in buf:
                yield f"data: {json.dumps(ev)}\n\n"
            # 3. Live stream
            while True:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(msg)}\n\n"
                except asyncio.TimeoutError:
                    # heartbeat
                    yield f"data: {json.dumps({'event': 'ping', 'payload': {}})}\n\n"
        finally:
            queues = state.event_queues.get(task_id, [])
            if queue in queues:
                queues.remove(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ----- Port discovery -----
def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_port_file(port: int) -> None:
    port_file = WORKSPACE / ".sidecar-port"
    port_file.write_text(str(port), encoding="utf-8")
    logger.info("Sidecar port %d written to %s", port, port_file)


if __name__ == "__main__":
    import uvicorn

    port_env = os.environ.get("SIDECAR_PORT", "").strip()
    port = int(port_env) if port_env else pick_free_port()
    write_port_file(port)
    logger.info("Starting sidecar on 127.0.0.1:%d", port)
    # v0.2.3: 显式 loop="asyncio" — uvloop 0.17+ API 变更过
    # (.new_event_loop 没了),asyncio loop 一直稳,不会因为
    # sandbox 残留坏 uvloop 报 "module 'uvloop' has no attribute 'new_event_loop'"
    uvicorn.run(app, host="127.0.0.1", port=port, log_level=LOG_LEVEL.lower(), loop="asyncio")
