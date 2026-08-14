"""Unit tests for the catalog (SQLite index)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.db.catalog import Catalog  # noqa: E402


@pytest.fixture
def catalog(tmp_path) -> Catalog:
    return Catalog(tmp_path / "test.sqlite")


def test_create_and_get_task(catalog):
    catalog.create_task("t1", "grade", "/some/path", {"foo": "bar"})
    t = catalog.get_task("t1")
    assert t is not None
    assert t["type"] == "grade"
    assert t["input_folder"] == "/some/path"
    assert t["status"] == "pending"


def test_update_task(catalog):
    catalog.create_task("t2", "cull", "/p", {})
    catalog.update_task("t2", status="done", summary="all done")
    t = catalog.get_task("t2")
    assert t["status"] == "done"
    assert t["summary"] == "all done"


def test_upsert_photo_new(catalog):
    pid = catalog.upsert_photo("t1", "/a/b.jpg", status="graded", quality_score=4, keep=1)
    assert pid > 0


def test_upsert_photo_update(catalog):
    pid1 = catalog.upsert_photo("t1", "/a/b.jpg", status="pending")
    pid2 = catalog.upsert_photo("t1", "/a/b.jpg", status="graded", keep=1, quality_score=5)
    assert pid1 == pid2  # same row updated
    photos = catalog.list_task_photos("t1")
    assert len(photos) == 1
    assert photos[0]["status"] == "graded"
    assert photos[0]["keep"] == 1


def test_feedback_round_trip(catalog):
    pid = catalog.upsert_photo("t1", "/a/b.jpg", status="graded")
    catalog.set_photo_feedback(pid, "up")
    photo = catalog.get_photo(pid)
    assert photo["feedback"] == "up"
    assert catalog.count_feedback("up") == 1


def test_recent_feedback(catalog):
    pid = catalog.upsert_photo("t1", "/a/b.jpg", status="graded", grade_params={"x": 1})
    catalog.set_photo_feedback(pid, "down")
    recent = catalog.recent_feedback(limit=10)
    assert len(recent) == 1
    assert recent[0]["feedback"] == "down"
