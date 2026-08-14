"""Unit tests for the upload pipeline (sanitize + recursive scan + tool layer)."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.agent.tools import (  # noqa: E402
    _iter_photos,
    _sanitize_rel_path,
    save_uploads,
)


# -------- _sanitize_rel_path --------

class TestSanitizeRelPath:
    def test_normal_relative(self):
        assert _sanitize_rel_path("subdir/IMG_001.jpg") == "subdir/IMG_001.jpg"

    def test_windows_separator(self):
        assert _sanitize_rel_path(r"subdir\IMG_001.jpg") == "subdir/IMG_001.jpg"

    def test_reject_absolute_unix(self):
        assert _sanitize_rel_path("/etc/passwd") is None

    def test_reject_absolute_windows(self):
        assert _sanitize_rel_path("C:/Windows/system32") is None
        assert _sanitize_rel_path(r"C:\Windows\system32") is None

    def test_reject_path_traversal(self):
        assert _sanitize_rel_path("../escape.jpg") is None
        assert _sanitize_rel_path("a/../../etc/passwd") is None

    def test_reject_hidden_segments(self):
        assert _sanitize_rel_path(".secret/file.jpg") is None
        assert _sanitize_rel_path("normal/.hidden.jpg") is None

    def test_reject_empty(self):
        assert _sanitize_rel_path("") is None
        assert _sanitize_rel_path(".") is None
        assert _sanitize_rel_path("/") is None

    def test_reject_control_chars(self):
        assert _sanitize_rel_path("bad\x00name.jpg") is None
        assert _sanitize_rel_path("bad\nname.jpg") is None

    def test_cap_segment_length(self):
        long = "a" * 500 + ".jpg"
        result = _sanitize_rel_path(long)
        assert result is not None
        # Whole path kept; segment is capped at 200
        assert len(result.split("/")[0]) == 200


# -------- _iter_photos --------

class TestIterPhotos:
    def test_finds_recursive(self, tmp_path: Path):
        (tmp_path / "top.jpg").write_bytes(b"x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "deep.jpg").write_bytes(b"x")
        (tmp_path / "sub" / "sub2").mkdir()
        (tmp_path / "sub" / "sub2" / "very.cr2").write_bytes(b"x")
        # Non-photo file
        (tmp_path / "readme.txt").write_text("hi")

        results = list(_iter_photos(tmp_path))
        names = sorted([p.name for _, p in results])
        assert names == ["deep.jpg", "top.jpg", "very.cr2"]

    def test_skips_hidden_dirs(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "secret.jpg").write_bytes(b"x")
        (tmp_path / "real.jpg").write_bytes(b"x")

        results = list(_iter_photos(tmp_path))
        assert [p.name for _, p in results] == ["real.jpg"]

    def test_skips_system_dirs(self, tmp_path: Path):
        for d in ["Thumbs.db", ".DS_Store", "@eaDir"]:
            (tmp_path / d).mkdir()
            (tmp_path / d / "x.jpg").write_bytes(b"x")
        (tmp_path / "real.jpg").write_bytes(b"x")

        results = list(_iter_photos(tmp_path))
        assert [p.name for _, p in results] == ["real.jpg"]

    def test_respects_max_depth(self, tmp_path: Path):
        deep = tmp_path
        for i in range(10):
            deep = deep / f"d{i}"
            deep.mkdir()
        (deep / "deep.jpg").write_bytes(b"x")
        (tmp_path / "shallow.jpg").write_bytes(b"x")

        results = list(_iter_photos(tmp_path, max_depth=3))
        names = [p.name for _, p in results]
        assert "shallow.jpg" in names
        assert "deep.jpg" not in names

    def test_supports_raw_extensions(self, tmp_path: Path):
        for ext in ["cr2", "cr3", "nef", "arw", "dng"]:
            (tmp_path / f"photo.{ext}").write_bytes(b"raw")
        (tmp_path / "doc.txt").write_text("not a photo")

        results = list(_iter_photos(tmp_path))
        exts = sorted([p.suffix.lstrip(".") for _, p in results])
        assert exts == ["arw", "cr2", "cr3", "dng", "nef"]


# -------- save_uploads --------

class _FakeUploadFile:
    """Mimics enough of fastapi.UploadFile for save_uploads's `await .read(n)` contract."""
    def __init__(self, data: bytes):
        self._buf = data

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            out, self._buf = self._buf, b""
            return out
        out, self._buf = self._buf[:n], self._buf[n:]
        return out


def _make_ctx(workspace: Path) -> Any:
    """Build a ToolContext-like object (the fields save_uploads actually reads)."""
    class _Ctx:
        pass
    ctx = _Ctx()
    ctx.workspace = workspace
    return ctx


class TestSaveUploads:
    @pytest.mark.asyncio
    async def test_basic_files(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        files = [
            ("a.jpg", _FakeUploadFile(b"jpg1")),
            ("b.png", _FakeUploadFile(b"png1")),
            ("c.cr2", _FakeUploadFile(b"raw1")),
        ]
        result = await save_uploads(ctx, files=files, folder_label="trip")

        assert result["ok"] is True
        data = result["data"]
        assert data["accepted"] == 3
        assert data["rejected"] == 0
        assert data["folder_name"].endswith("-uploaded-trip")
        target = tmp_path / "input" / data["folder_name"]
        assert (target / "a.jpg").read_bytes() == b"jpg1"
        assert (target / "b.png").read_bytes() == b"png1"
        assert (target / "c.cr2").read_bytes() == b"raw1"

    @pytest.mark.asyncio
    async def test_rejects_unsupported_types(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        files = [
            ("photo.jpg", _FakeUploadFile(b"good")),
            ("doc.txt", _FakeUploadFile(b"bad")),
            ("film.heic", _FakeUploadFile(b"bad")),
            ("vector.svg", _FakeUploadFile(b"bad")),
        ]
        result = await save_uploads(ctx, files=files, folder_label="mix")

        assert result["ok"] is True
        data = result["data"]
        assert data["accepted"] == 1
        assert data["rejected"] == 3
        reasons = {r["rel_path"]: r["reason"] for r in data["rejected_files"]}
        assert ".txt" in reasons["doc.txt"]
        assert ".heic" in reasons["film.heic"]
        assert ".svg" in reasons["vector.svg"]

    @pytest.mark.asyncio
    async def test_preserves_subdir_structure(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        files = [
            ("day1/a.jpg", _FakeUploadFile(b"a")),
            ("day1/sub/b.jpg", _FakeUploadFile(b"b")),
            ("day2/c.jpg", _FakeUploadFile(b"c")),
        ]
        result = await save_uploads(ctx, files=files, folder_label="wedding")
        data = result["data"]
        assert data["accepted"] == 3
        target = tmp_path / "input" / data["folder_name"]
        assert (target / "day1" / "a.jpg").exists()
        assert (target / "day1" / "sub" / "b.jpg").exists()
        assert (target / "day2" / "c.jpg").exists()

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        files = [
            ("../../../etc/passwd.jpg", _FakeUploadFile(b"x")),
            ("normal/../escape.jpg", _FakeUploadFile(b"x")),
        ]
        result = await save_uploads(ctx, files=files, folder_label="evil")
        data = result["data"]
        assert data["accepted"] == 0
        assert data["rejected"] == 2

    @pytest.mark.asyncio
    async def test_skips_empty_files(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        files = [
            ("empty.jpg", _FakeUploadFile(b"")),
            ("real.jpg", _FakeUploadFile(b"x")),
        ]
        result = await save_uploads(ctx, files=files, folder_label="empty")
        data = result["data"]
        assert data["accepted"] == 1
        assert data["rejected"] == 1
        reasons = {r["rel_path"]: r["reason"] for r in data["rejected_files"]}
        assert "empty" in reasons["empty.jpg"]

    @pytest.mark.asyncio
    async def test_label_sanitized(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        files = [("a.jpg", _FakeUploadFile(b"x"))]
        # Malicious label with traversal + weird chars
        result = await save_uploads(ctx, files=files, folder_label="../../../etc/passwd")
        data = result["data"]
        # Should still create folder, but inside input/
        target = tmp_path / "input" / data["folder_name"]
        assert target.exists()
        # No folder created outside input/
        assert not (tmp_path / "etc").exists()

    @pytest.mark.asyncio
    async def test_total_bytes_accurate(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        files = [
            ("a.jpg", _FakeUploadFile(b"x" * 1024)),
            ("b.jpg", _FakeUploadFile(b"y" * 2048)),
        ]
        result = await save_uploads(ctx, files=files, folder_label="size")
        assert result["data"]["total_bytes"] == 1024 + 2048

    @pytest.mark.asyncio
    async def test_unique_folder_names(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        r1 = await save_uploads(ctx, files=[("a.jpg", _FakeUploadFile(b"x"))], folder_label="a")
        # Sleep at least 1s because timestamp uses %Y%m%d-%H%M%S (second resolution)
        time.sleep(1.1)
        r2 = await save_uploads(ctx, files=[("b.jpg", _FakeUploadFile(b"y"))], folder_label="a")
        assert r1["data"]["folder_name"] != r2["data"]["folder_name"]


# -------- list_input_folders integration --------

class TestListInputFoldersWithLoose:
    @pytest.mark.asyncio
    async def test_returns_folders_and_loose(self, tmp_path: Path):
        from backend.agent.tools import list_input_folders
        # Setup: one subfolder with photos, plus two loose files at input/ root
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        sub = input_dir / "trip"
        sub.mkdir()
        (sub / "a.jpg").write_bytes(b"x")
        (input_dir / "loose1.jpg").write_bytes(b"x")
        (input_dir / "loose2.cr2").write_bytes(b"x")
        (input_dir / "junk.txt").write_text("not photo")
        (input_dir / ".hidden.jpg").write_bytes(b"x")  # should be skipped

        ctx = _make_ctx(tmp_path)
        result = await list_input_folders(ctx)
        assert result["ok"] is True
        data = result["data"]
        folder_names = {f["name"] for f in data["folders"]}
        assert folder_names == {"trip"}
        loose_names = {f["name"] for f in data["loose_files"]}
        assert loose_names == {"loose1.jpg", "loose2.cr2"}
