"""
M3 (MiniMax M3) client - multimodal LLM client with mock fallback.

When M3_API_KEY is not set, the client operates in mock mode and returns
deterministic but realistic-looking responses. This allows:
- Local dev without burning API credits
- M0 acceptance without an M3 key
- Automated testing

Reserved for future: style profile injection (persona), tool-calling loop,
batch optimization, multi-photo context windows.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import httpx

logger = logging.getLogger(__name__)


# Maximum image dimension sent to M3 (longest edge).
# 50MP RAW would be wasteful; 2048px preserves detail for grading/culling.
M3_MAX_IMAGE_EDGE = 2048


@dataclass
class M3Config:
    base_url: str
    api_key: str
    model: str
    timeout: float = 60.0
    max_concurrent: int = 5

    @classmethod
    def from_env(cls) -> "M3Config":
        base_url = os.environ.get("M3_BASE_URL", "").strip()
        api_key = os.environ.get("M3_API_KEY", "").strip()
        model = os.environ.get("M3_MODEL", "MiniMax-M3").strip() or "MiniMax-M3"
        return cls(base_url=base_url, api_key=api_key, model=model)

    @property
    def is_mock(self) -> bool:
        return not (self.base_url and self.api_key)


# --- Error taxonomy ---
# These are all RuntimeError subclasses so existing `except Exception` in
# tools.py still catches them, but callers that want to distinguish "this
# photo specifically is bad" (just skip + record) from "the whole task is
# hopeless, abort" (M3 auth/quota/network down) can match the specific
# subclass and react.
#
# Naming convention: the error message is user-facing Chinese; the class
# name is what `isinstance(e, M3AuthError)` checks against.
class M3Error(RuntimeError):
    """Base class for all M3 client errors."""


class M3AuthError(M3Error):
    """401 / 403 — key is wrong, revoked, or out of credits. Retry won't help.
    Tools should abort the whole task when they see this."""


class M3QuotaError(M3Error):
    """402 / 429 — rate limit or out of credits. Same: abort task."""


class M3ServerError(M3Error):
    """5xx — provider's fault. Could be transient; tools retry once then abort."""


class M3NetworkError(M3Error):
    """Timeout / connection refused / DNS — the network is down. Abort task."""


class M3BadResponseError(M3Error):
    """200 with malformed body — should be rare. Treat as per-photo fail."""


def encode_image_for_m3(path: Path, max_edge: int = M3_MAX_IMAGE_EDGE) -> dict[str, Any]:
    """
    Read an image, downsample to max_edge longest side, return as base64 data URL.

    Skips downsampling if image is already small. Preserves JPEG for JPEG,
    preserves RAW previews (already JPEG) without re-encoding to avoid quality loss.
    """
    from PIL import Image

    with Image.open(path) as im:
        # Convert mode if needed (RGBA → RGB for JPEG)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGB")

        w, h = im.size
        longest = max(w, h)
        if longest > max_edge:
            scale = max_edge / longest
            new_size = (int(w * scale), int(h * scale))
            im = im.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        # Use JPEG q=85 to keep payload small but visually fine
        im.save(buf, format="JPEG", quality=85, optimize=True)
        data = buf.getvalue()

    b64 = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
    }


class M3Client:
    """
    Async client for M3 multimodal chat completions.

    Supports:
    - Single-turn text + image chat
    - Streaming (for UI token-by-token display)
    - Function calling (for tool use)
    - Mock mode when keys are missing

    Future hooks (预留):
    - persona: per-photographer style profile injected into system prompt
    - batch: group multiple photos into one call for cost reduction
    - cache: hash image content, skip M3 call for identical inputs
    """

    # M3-specific header that enables tool/function calling.
    # Without it, M3 silently downgrades to plain text mode.
    # Reference: https://platform.minimaxi.com/ (M3 function calling spec)
    M3_PLUGIN_HEADER = "X-MiniMax-Plugin-Version"
    M3_PLUGIN_VALUE = "2"

    def __init__(self, config: M3Config | None = None):
        self.config = config or M3Config.from_env()
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        if not self.config.is_mock:
            headers = {
                "Authorization": f"Bearer {self.config.api_key}",
            }
            # M3-specific header that enables tool/function calling. Required
            # when hitting the M3 platform directly, but OpenRouter will pass
            # it through verbatim to the upstream provider and many don't
            # recognise it, so we skip it there to avoid 4xx noise.
            if "openrouter.ai" not in self.config.base_url:
                headers[self.M3_PLUGIN_HEADER] = self.M3_PLUGIN_VALUE
            # OpenRouter also recommends a referer/title for app attribution.
            # We only set them when the base URL looks like openrouter.
            if "openrouter.ai" in self.config.base_url:
                headers["HTTP-Referer"] = "https://github.com/MiniMax/photographer-copilot"
                headers["X-Title"] = "Photographer Copilot"
            self._http = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                headers=headers,
            )
        logger.info(
            "M3 client initialized (model=%s, mock=%s, base=%s)",
            self.config.model,
            self.config.is_mock,
            self.config.base_url or "(mock)",
        )

    async def close(self) -> None:
        if not self.config.is_mock:
            await self._http.aclose()

    async def verify_key(self) -> tuple[bool, str]:
        """
        Cheaply verify the API key works. Returns (ok, message).

        - For OpenRouter: hits /auth/key (does not consume credits).
        - For other providers: a 1-token chat ping (negligible cost).
        - For mock mode: returns (True, "mock mode (no key required)").

        We deliberately do NOT raise — the caller logs and decides whether
        to abort startup. The user should still be able to start the app
        and see a clear "key invalid" toast when they try to chat/grade.
        """
        if self.config.is_mock:
            return True, "mock mode (M3_API_KEY not set)"
        try:
            if "openrouter.ai" in self.config.base_url:
                resp = await self._http.get("/auth/key")
                if resp.status_code == 200:
                    try:
                        data = resp.json().get("data", {})
                        remaining = data.get("limit_remaining")
                        if remaining is not None:
                            return True, f"OK, balance ≈ ${remaining:.2f}"
                        return True, "OK"
                    except Exception:
                        return True, "OK"
                try:
                    err_body = resp.json()
                    err_msg = err_body.get("error", {}).get("message") or err_body
                except Exception:
                    err_msg = resp.text[:200]
                if resp.status_code == 401:
                    return False, f"401 Unauthorized — key 无效或被撤销: {err_msg}"
                return False, f"HTTP {resp.status_code}: {err_msg}"
            # Generic provider: 1-token chat ping
            resp = await self._http.post(
                "/chat/completions",
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            if resp.status_code == 200:
                return True, "OK"
            if resp.status_code == 401:
                try:
                    err_body = resp.json()
                    err_msg = err_body.get("error", {}).get("message") or err_body
                except Exception:
                    err_msg = resp.text[:200]
                return False, f"401 Unauthorized: {err_msg}"
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, f"网络错误: {e}"

    async def chat(
        self,
        system: str,
        user_text: str,
        images: list[Path] | None = None,
        response_format: Literal["text", "json"] = "text",
        temperature: float = 0.3,
    ) -> str:
        """Single-turn chat. Returns the assistant text (or JSON string)."""
        async with self._semaphore:
            if self.config.is_mock:
                return self._mock_response(user_text, images, response_format)

            messages = self._build_messages(system, user_text, images)
            payload: dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "temperature": temperature,
            }
            if response_format == "json":
                payload["response_format"] = {"type": "json_object"}

            try:
                resp = await self._http.post("/chat/completions", json=payload)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                raise M3NetworkError(f"M3 网络错误: {e} (可能是 .env 配错 / 网络挂了 / 防火墙拦了)")
            except httpx.RequestError as e:
                # Any other httpx-level request error
                raise M3NetworkError(f"M3 请求错误: {e}")
            if resp.status_code == 401:
                # 401 means the API key is rejected. Surface a clear error
                # so the UI / logs can show "key invalid" instead of a generic
                # "HTTP 401" — saves the user a lot of debugging time.
                try:
                    err_body = resp.json()
                    err_msg = err_body.get("error", {}).get("message") or err_body
                except Exception:
                    err_msg = resp.text[:200]
                raise M3AuthError(
                    f"M3 API key 无效或被拒绝 (401): {err_msg}. "
                    f"检查 .env 里 M3_API_KEY / M3_BASE_URL / M3_MODEL 是否正确。"
                )
            if resp.status_code in (402, 429):
                # Quota / rate limit — same semantics as 401 from the
                # caller's perspective: abort the whole task, don't
                # keep charging through N photos just to fail each.
                try:
                    err_body = resp.json()
                    err_msg = err_body.get("error", {}).get("message") or err_body
                except Exception:
                    err_msg = resp.text[:200]
                raise M3QuotaError(
                    f"M3 配额或限流 ({resp.status_code}): {err_msg}. "
                    f"充值或稍后再试:https://openrouter.ai/credits"
                )
            if resp.status_code in (500, 502, 503, 504):
                # Server-side issue — may be transient. Raise as
                # M3ServerError; tools.py will retry once.
                raise M3ServerError(
                    f"M3 服务端错误 ({resp.status_code}): {resp.text[:200]}"
                )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                # Anything else (4xx we didn't categorise) — treat as
                # "this specific call is broken, don't abort task".
                raise M3BadResponseError(f"M3 意外状态码 {resp.status_code}: {resp.text[:200]}") from e
            try:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                raise M3BadResponseError(f"M3 返回内容无法解析: {resp.text[:200]}") from e

    async def stream_chat(
        self,
        system: str,
        user_text: str,
        images: list[Path] | None = None,
        temperature: float = 0.3,
    ) -> AsyncIterator[str]:
        """Streaming chat. Yields text chunks."""
        if self.config.is_mock:
            async with self._semaphore:
                async for chunk in self._mock_stream(user_text, images):
                    yield chunk
            return

        async with self._semaphore:
            messages = self._build_messages(system, user_text, images)
            payload = {
                "model": self.config.model,
                "messages": messages,
                "temperature": temperature,
                "stream": True,
            }
            # Open the connection; M3Error categorisation happens inside
            # _iter_stream_chat, network errors here. We can't yield
            # directly from inside the try/except because Python's
            # async generator semantics: yields must be at the top
            # level of the generator, not inside a try that wraps an
            # `async with` we then `return` from. So we just open the
            # connection here, hand the response to the inner async
            # generator, and re-yield each chunk.
            try:
                stream_cm = self._http.stream("POST", "/chat/completions", json=payload)
                resp = await stream_cm.__aenter__()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                raise M3NetworkError(f"M3 网络错误: {e} (可能是 .env 配错 / 网络挂了 / 防火墙拦了)")
            except httpx.RequestError as e:
                raise M3NetworkError(f"M3 请求错误: {e}")
            try:
                async for chunk in self._iter_stream_chat(resp):
                    yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)

    async def _iter_stream_chat(self, resp) -> AsyncIterator[str]:
        """Inner async generator that walks an open streaming response.
        Extracted so stream_chat can wrap the network call in a try/except
        for M3NetworkError, and the rest of the error categorisation lives
        here against the response object."""
        if resp.status_code == 401:
            await resp.aread()
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message") or err_body
            except Exception:
                err_msg = resp.text[:200]
            raise M3AuthError(
                f"M3 API key 无效或被拒绝 (401): {err_msg}. "
                f"检查 .env 里 M3_API_KEY / M3_BASE_URL / M3_MODEL 是否正确。"
            )
        if resp.status_code in (402, 429):
            await resp.aread()
            try:
                err_body = resp.json()
                err_msg = err_body.get("error", {}).get("message") or err_body
            except Exception:
                err_msg = resp.text[:200]
            raise M3QuotaError(
                f"M3 配额或限流 ({resp.status_code}): {err_msg}."
            )
        if resp.status_code in (500, 502, 503, 504):
            body = ""
            try:
                await resp.aread()
                body = resp.text[:200]
            except Exception:
                pass
            raise M3ServerError(f"M3 服务端错误 ({resp.status_code}): {body}")
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                await resp.aread()
                body = resp.text[:200]
            except Exception:
                pass
            raise M3BadResponseError(f"M3 意外状态码 {resp.status_code}: {body}") from e
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload_str = line[6:]
            if payload_str == "[DONE]":
                break
            try:
                chunk = json.loads(payload_str)
                delta = chunk["choices"][0]["delta"].get("content", "")
                if delta:
                    yield delta
            except (json.JSONDecodeError, KeyError):
                continue

    def _build_messages(
        self, system: str, user_text: str, images: list[Path] | None
    ) -> list[dict[str, Any]]:
        user_content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        if images:
            for img_path in images:
                user_content.append(encode_image_for_m3(img_path))
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    # ---------- Mock mode ----------
    # Returns realistic-looking responses so the UI / pipeline can be developed
    # end-to-end without an M3 key. These are NOT actual M3 outputs.

    def _mock_response(
        self,
        user_text: str,
        images: list[Path] | None,
        response_format: str,
    ) -> str:
        if response_format == "json":
            return self._mock_json_response(user_text, images)
        return self._mock_text_response(user_text)

    def _mock_text_response(self, user_text: str) -> str:
        # Simple keyword-based mock answers for common photography questions
        knowledge = {
            "光圈": "光圈是镜头内控制进光量的可调节开口,用 F 值表示(如 f/1.8、f/8)。F 值越小光圈越大,进光越多,景深越浅(背景虚化越强)。拍人像常用 f/1.4–f/2.8,风光常用 f/8–f/11。",
            "快门": "快门速度是感光元件暴露在光线下的时间,单位是秒的倒数(如 1/100s、1/2000s)。快门越快越能凝固动态,越慢越能表现运动模糊。安全快门约等于 1/焦距。",
            "iso": "ISO 是感光度,数值越高感光越强,但噪点也越多。白天户外常用 ISO 100–400,夜景手持可到 ISO 1600–6400,三脚架夜景可以保持 ISO 100。",
            "曝光": "曝光三要素是光圈、快门、ISO,三者共同决定一张照片的明暗。曝光补偿(EV)是在自动模式下微调明暗,正值提亮,负值压暗。",
            "构图": "常用构图法则:三分法(主体放在九宫格交叉点)、引导线(用线条把视线引向主体)、对称构图、留白、对角线构图。新手先练熟三分法。",
            "raw": "RAW 是相机原始未处理的数据,保留最大后期空间(白平衡、曝光、色彩都可大幅调整而不损失画质)。缺点是文件大、需专用软件(Lightroom/Capture One)处理。",
        }
        for kw, ans in knowledge.items():
            if kw.lower() in user_text.lower():
                return f"[MOCK] {ans}"
        return "[MOCK] 这是 mock 模式的回复。配置 M3_API_KEY 后会调用真实 M3 模型回答这个问题。"

    def _mock_json_response(
        self, user_text: str, images: list[Path] | None
    ) -> str:
        # 关键字检测覆盖了"调色/修图"和"筛/废片/判断"等中文/英文。
        # 注意:`build_cull_prompt` 的第一句是"请判断这张照片是否应该保留",
        # 并没有"cull"/"筛"等关键字 —— 早期版本会误匹配到 cull,后来又
        # 加了"判断"作为更宽泛的兜底,避免 mock 模式走 fallback 路径
        # (那个 fallback 返回的 dict 没有 `keep` 字段,会害得所有照片
        # 都被默认 keep,筛片变成"全留")。
        if "color_grade" in user_text.lower() or "调色" in user_text or "修图" in user_text:
            # v0.2.1 mock:不再预设"风格"标签,而是**真读图**给针对性诊断。
            # 简单分析:主色 + 平均亮度 → 推断场景/光状态 → 写 diagnosis。
            # 这模拟的是真 M3 拿到图像后会做的事(mock 只是占位)。
            scene_label = "[MOCK] 主体居中"
            diagnosis_label = "[MOCK] 整体曝光基本正常"
            exif_lines = []
            stats_lines = []
            if images:
                try:
                    from PIL import Image
                    with Image.open(images[0]) as im:
                        im_rgb = im.convert("RGB").resize((64, 64))
                        stat_rgb = list(im_rgb.getdata())
                        r_sum = g_sum = b_sum = 0
                        n = 0
                        for px in stat_rgb:
                            r_sum += px[0]; g_sum += px[1]; b_sum += px[2]
                            n += 1
                        mr, mg, mb = r_sum / n, g_sum / n, b_sum / n
                        luma = 0.299 * mr + 0.587 * mg + 0.114 * mb
                        # 直方图 8 段
                        gray = im_rgb.convert("L")
                        h = gray.histogram()
                        total = sum(h)
                        bins8 = [sum(h[i*32:(i+1)*32]) * 100.0 / total for i in range(8)]
                        shadow_pct = bins8[0] + bins8[1]
                        highlight_pct = bins8[6] + bins8[7]
                        mid_pct = sum(bins8[2:6])
                        # 估计色温
                        if mb == 0: mb = 0.01
                        rb_ratio = mr / mb
                        if rb_ratio > 1.4: temp_k = 3500
                        elif rb_ratio > 1.1: temp_k = 5000
                        elif rb_ratio > 0.9: temp_k = 6000
                        else: temp_k = 7500
                        # 肤色占比
                        skin = 0; sampled = 0
                        for px in stat_rgb[::8]:
                            r, g, b = px
                            if r > 95 and 40 <= g <= 180 and 20 <= b <= 150 and r > g > b:
                                skin += 1
                            sampled += 1
                        skin_pct = skin / max(1, sampled) * 100
                        # ---- 客观拼诊断 ----
                        parts = [f"亮度 {luma:.0f}/255(直方图 阴影{shadow_pct:.0f}% 中调{mid_pct:.0f}% 高光{highlight_pct:.0f}%)"]
                        if skin_pct > 5:
                            parts.append(f"肤色 {skin_pct:.0f}% 需保护")
                        parts.append(f"色温 ~{temp_k}K")
                        diagnosis_label = "[MOCK] " + ", ".join(parts)
                        # 场景描述:基于主色 + 亮度
                        if luma < 60:
                            scene_label = "[MOCK] 整体偏暗(舞台/夜景/室内弱光)"
                        elif luma > 180:
                            scene_label = "[MOCK] 整体偏亮(强日光/逆光)"
                        elif rb_ratio > 1.3:
                            scene_label = "[MOCK] 偏暖光(室内钨丝灯/黄昏)"
                        elif rb_ratio < 0.85:
                            scene_label = "[MOCK] 偏冷光(阴天/阴影/夜景霓虹)"
                        else:
                            scene_label = "[MOCK] 自然光,色温基本正常"
                        # 模拟 prompt 会拿到的 image_stats 字符串
                        stats_lines = [
                            f"- 平均亮度: {luma:.0f}/255",
                            f"- 估计色温: ~{temp_k}K",
                            f"- 直方图(8 段%): {[round(b, 1) for b in bins8]}",
                            f"- 肤色像素占比: {skin_pct:.0f}%",
                        ]
                except Exception:
                    pass
            # 模拟 prompt 拿到的 exif 字符串(RAW 没 EXIF,mock 走"无")
            exif_lines = ["(mock: RAW/JPG 无 EXIF,按图分析)"]
            params = {
                "scene": scene_label,
                "diagnosis": diagnosis_label,
                "white_balance": {
                    "temp_shift": random.randint(-15, 15),  # v0.2.1:不再 ±200,改成温和
                    "tint_shift": random.randint(-10, 10),
                },
                "exposure": round(random.uniform(-0.3, 0.3), 2),
                "contrast": random.randint(-15, 20),
                "highlights": random.randint(-30, 0),
                "shadows": random.randint(0, 30),
                "whites": random.randint(-10, 15),
                "blacks": random.randint(-15, 10),
                "vibrance": random.randint(-5, 15),
                "saturation": random.randint(-10, 10),
                "hsl": {
                    "red": {"hue": 0, "sat": 0, "lum": 0},
                    "orange": {"hue": 0, "sat": 0, "lum": 0},  # v0.2.1:别动肤色
                    "yellow": {"hue": 0, "sat": 0, "lum": 0},
                    "green": {"hue": 0, "sat": 0, "lum": 0},
                    "aqua": {"hue": 0, "sat": 0, "lum": 0},
                    "blue": {"hue": 0, "sat": 0, "lum": 0},
                    "purple": {"hue": 0, "sat": 0, "lum": 0},
                    "magenta": {"hue": 0, "sat": 0, "lum": 0},
                },
                "crop": None,
                "notes": f"[MOCK] {scene_label} {diagnosis_label} → 给出温和调整",
            }
            return json.dumps(params, ensure_ascii=False)

        if (
            "cull" in user_text.lower()
            or "筛" in user_text
            or "废片" in user_text
            or "判断这张照片" in user_text  # 覆盖 cull prompt 的标准开头
            or "保留" in user_text  # 同上兜底
        ):
            decision = {
                "scene": "[MOCK] 标准静物/风景,主体居中",
                "keep": random.random() > 0.15,
                "quality": random.randint(3, 5),
                "reasons": [],
                "tags": ["mock-tag"],
                "comment": "[MOCK] 主体清晰,构图合理",
            }
            if not decision["keep"]:
                decision["reasons"] = random.choice([
                    ["闭眼"],
                    ["轻微模糊"],
                    ["构图过偏"],
                    ["表情不自然"],
                ])
            return json.dumps(decision, ensure_ascii=False)

        return json.dumps({"mock": True, "echo": user_text[:100]}, ensure_ascii=False)

    async def _mock_stream(
        self, user_text: str, images: list[Path] | None
    ) -> AsyncIterator[str]:
        text = self._mock_text_response(user_text)
        # Emit in small chunks to simulate streaming
        chunk_size = 8
        for i in range(0, len(text), chunk_size):
            yield text[i : i + chunk_size]


# Future-extension surface (预留):
# - Persona: per-photographer style profile (Phase 2)
# - Tool use loop: replace single-turn with multi-step tool calling (Phase 1.5)
# - Batch vision: combine N photos in one call to save tokens (Phase 2)
# - Caching: content-hash based, skip M3 call for unchanged inputs
