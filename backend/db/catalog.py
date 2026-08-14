"""
SQLite catalog: index of every photo the agent has seen, with status,
M3 decisions, and user feedback (👍/👎). This is the data foundation for
future "personal style learning".

Schema is forward-compatible — adding columns is non-breaking. We avoid
SQLAlchemy to keep dependencies minimal.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    output_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / analyzing / graded / kept / culled / failed
    quality_score INTEGER,                    -- 1..5 (for culling)
    keep INTEGER,                             -- 0/1 (culling decision)
    reasons TEXT,                             -- JSON array
    tags TEXT,                                -- JSON array
    comment TEXT,
    grade_params TEXT,                        -- JSON of color grade params
    feedback TEXT,                            -- 'up' / 'down' / NULL
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(task_id, source_path)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,                       -- 'grade' | 'cull'
    input_folder TEXT NOT NULL,               -- workspace/input/<时间>-in
    output_folder TEXT,                       -- workspace/output/<时间>-out
    params TEXT,                              -- JSON
    status TEXT NOT NULL DEFAULT 'pending',   -- pending / running / done / failed
    summary TEXT,                             -- human-readable summary
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_photos_task ON photos(task_id);
CREATE INDEX IF NOT EXISTS idx_photos_status ON photos(status);
CREATE INDEX IF NOT EXISTS idx_photos_feedback ON photos(feedback);
"""


class Catalog:
    """Thread-safe wrapper around aiosqlite / sqlite3 (we use sync for simplicity here)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        logger.info("Catalog initialized at %s", db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---------- Task operations ----------

    def create_task(self, task_id: str, type_: str, input_folder: str, params: dict) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tasks
                (id, type, input_folder, params, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (task_id, type_, input_folder, json.dumps(params, ensure_ascii=False), now, now),
            )

    def update_task(
        self,
        task_id: str,
        status: str | None = None,
        output_folder: str | None = None,
        summary: str | None = None,
    ) -> None:
        updates = []
        values = []
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if output_folder is not None:
            updates.append("output_folder = ?")
            values.append(output_folder)
        if summary is not None:
            updates.append("summary = ?")
            values.append(summary)
        updates.append("updated_at = ?")
        values.append(time.time())
        values.append(task_id)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values
            )

    def get_task(self, task_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    # ---------- Photo operations ----------

    def upsert_photo(
        self,
        task_id: str,
        source_path: str,
        status: str = "pending",
        **fields: Any,
    ) -> int:
        now = time.time()
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM photos WHERE task_id = ? AND source_path = ?",
                (task_id, source_path),
            ).fetchone()
            if existing:
                updates = ["status = ?", "updated_at = ?"]
                values: list[Any] = [status, now]
                for k, v in fields.items():
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, ensure_ascii=False)
                    updates.append(f"{k} = ?")
                    values.append(v)
                values.append(existing["id"])
                conn.execute(
                    f"UPDATE photos SET {', '.join(updates)} WHERE id = ?", values
                )
                return existing["id"]
            else:
                cols = ["task_id", "source_path", "status", "created_at", "updated_at"]
                vals: list[Any] = [task_id, source_path, status, now, now]
                for k, v in fields.items():
                    cols.append(k)
                    if isinstance(v, (list, dict)):
                        v = json.dumps(v, ensure_ascii=False)
                    vals.append(v)
                placeholders = ",".join(["?"] * len(cols))
                cur = conn.execute(
                    f"INSERT INTO photos ({','.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
                return cur.lastrowid

    def set_photo_feedback(self, photo_id: int, feedback: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE photos SET feedback = ?, updated_at = ? WHERE id = ?",
                (feedback, time.time(), photo_id),
            )

    def get_photo(self, photo_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
            return dict(row) if row else None

    def list_task_photos(self, task_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM photos WHERE task_id = ? ORDER BY id", (task_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- Aggregations (for future style learning) ----------

    def count_feedback(self, feedback: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM photos WHERE feedback = ?", (feedback,)
            ).fetchone()
            return row["c"] if row else 0

    def recent_feedback(self, limit: int = 50) -> list[dict]:
        """Get recent photos with feedback, for style learning analysis."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM photos
                WHERE feedback IS NOT NULL AND grade_params IS NOT NULL
                ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
