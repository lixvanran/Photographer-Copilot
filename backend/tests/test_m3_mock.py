"""Unit tests for the M3 client mock mode."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.agent.m3_client import M3Client, M3Config, encode_image_for_m3  # noqa: E402


def test_mock_config_when_no_keys():
    cfg = M3Config(base_url="", api_key="", model="MiniMax-M3")
    assert cfg.is_mock is True


def test_real_config_with_keys():
    cfg = M3Config(base_url="https://api.example.com", api_key="sk-test", model="MiniMax-M3")
    assert cfg.is_mock is False


def test_encode_image_downsamples_large(tmp_path):
    src = tmp_path / "big.jpg"
    Image.new("RGB", (4000, 3000), (128, 128, 128)).save(src, "JPEG", quality=90)
    encoded = encode_image_for_m3(src, max_edge=1024)
    assert encoded["type"] == "image_url"
    # data URL should be much smaller than original 4K image
    assert len(encoded["image_url"]["url"]) < 200_000


def test_encode_image_small_unchanged(tmp_path):
    src = tmp_path / "small.jpg"
    Image.new("RGB", (100, 100), (128, 128, 128)).save(src, "JPEG")
    encoded = encode_image_for_m3(src, max_edge=2048)
    assert encoded["type"] == "image_url"


def test_mock_chat_returns_text():
    async def run():
        cfg = M3Config(base_url="", api_key="", model="MiniMax-M3")
        client = M3Client(cfg)
        try:
            resp = await client.chat(
                system="test",
                user_text="什么是光圈?",
                images=None,
                response_format="text",
            )
            assert "光圈" in resp
            assert "[MOCK]" in resp
        finally:
            await client.close()
    asyncio.run(run())


def test_mock_chat_grade_returns_json():
    async def run():
        cfg = M3Config(base_url="", api_key="", model="MiniMax-M3")
        client = M3Client(cfg)
        try:
            resp = await client.chat(
                system="test",
                user_text="请调色这张图",
                images=None,
                response_format="json",
            )
            import json
            data = json.loads(resp)
            assert "exposure" in data
            assert "contrast" in data
            assert "hsl" in data
        finally:
            await client.close()
    asyncio.run(run())


def test_mock_stream_chat():
    async def run():
        cfg = M3Config(base_url="", api_key="", model="MiniMax-M3")
        client = M3Client(cfg)
        try:
            chunks: list[str] = []
            async for c in client.stream_chat(
                system="test", user_text="什么是快门?"
            ):
                chunks.append(c)
            full = "".join(chunks)
            assert "快门" in full
        finally:
            await client.close()
    asyncio.run(run())
