"""
End-to-end smoke test for the Python sidecar.

What it does:
1. Creates a temporary workspace with sample JPEG images
2. Starts the sidecar as a subprocess
3. Calls /health, /config, /input/folders, /tasks/cull, /tasks/grade
4. Streams task events to verify progress events fire
5. Verifies output files exist and are valid

Run: python scripts/smoke_test.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SIDECAR_DIR = REPO_ROOT / "sidecar"


def make_sample_jpeg(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> None:
    img = Image.new("RGB", size, color)
    img.save(path, format="JPEG", quality=85)


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port_file(workspace: Path, timeout: float = 20.0) -> int:
    port_file = workspace / ".sidecar-port"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_file.exists():
            return int(port_file.read_text().strip())
        time.sleep(0.1)
    raise TimeoutError(f"Sidecar did not write port file within {timeout}s")


def wait_for_tcp(port: int, timeout: float = 10.0) -> None:
    """Wait until the sidecar's TCP port actually accepts connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.1)
    raise TimeoutError(f"Port {port} did not become reachable within {timeout}s")


def main() -> int:
    print("=" * 60)
    print("Photographer Copilot - Smoke Test")
    print("=" * 60)

    # 1. Set up a temp workspace
    workspace = REPO_ROOT / "workspace" / "smoke-test"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "input").mkdir()
    (workspace / "output").mkdir()

    # Create sample folder with 5 fake "photos" (mock M3 will accept any)
    sample_folder = workspace / "input" / "smoke-photos"
    sample_folder.mkdir()
    for i, color in enumerate([
        (200, 100, 100),  # red-ish
        (100, 200, 100),  # green-ish
        (100, 100, 200),  # blue-ish
        (200, 200, 100),  # yellow-ish
        (100, 200, 200),  # cyan-ish
    ]):
        make_sample_jpeg(sample_folder / f"photo_{i:02d}.jpg", color)
    print(f"✓ Created 5 sample JPEGs in {sample_folder}")

    # 2. Start sidecar
    env = os.environ.copy()
    env.update({
        "WORKSPACE_PATH": str(workspace),
        # No M3 keys -> mock mode
        "M3_BASE_URL": "",
        "M3_API_KEY": "",
        "M3_MODEL": "MiniMax-M3",
        "LOG_LEVEL": "INFO",
        "PYTHONUNBUFFERED": "1",
    })
    sidecar_port = pick_free_port()
    env["SIDECAR_PORT"] = str(sidecar_port)
    print(f"✓ Starting sidecar (port {sidecar_port})...")

    proc = subprocess.Popen(
        [sys.executable, "-m", "agent.main"],
        cwd=str(SIDECAR_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        # 3. Wait for it to be ready
        actual_port = wait_for_port_file(workspace, timeout=15)
        assert actual_port == sidecar_port, f"port mismatch: {actual_port} vs {sidecar_port}"
        wait_for_tcp(sidecar_port, timeout=10)
        base_url = f"http://127.0.0.1:{sidecar_port}"
        print(f"✓ Sidecar up on {base_url}")

        client = httpx.Client(base_url=base_url, timeout=30.0)

        # 4. /health
        r = client.get("/health")
        r.raise_for_status()
        health = r.json()
        assert health["ok"] is True
        assert health["m3_mock"] is True, f"expected mock, got {health}"
        print(f"✓ /health OK (m3_mock={health['m3_mock']})")

        # 5. /config
        r = client.get("/config")
        r.raise_for_status()
        config = r.json()
        assert config["m3_mock"] is True
        assert config["workspace"] == str(workspace)
        print(f"✓ /config OK (model={config['m3_model']})")

        # 6. /input/folders
        r = client.get("/input/folders")
        r.raise_for_status()
        folders = r.json()["data"]["folders"]
        assert len(folders) == 1
        assert folders[0]["name"] == "smoke-photos"
        print(f"✓ /input/folders found 1 folder: {folders[0]['name']}")

        # 7. /input/rename
        r = client.post("/input/rename", json={"folder_name": "smoke-photos"})
        r.raise_for_status()
        renamed = r.json()["data"]
        print(f"✓ Renamed: {renamed['new_name']}")
        assert renamed["new_name"].endswith("-in")

        # 8. /tasks/cull — start and stream events
        print("→ Starting cull task...")
        r = client.post("/tasks/cull", json={"folder_name": renamed["new_name"]})
        r.raise_for_status()
        task_id = r.json()["task_id"]
        print(f"  task_id={task_id[:8]}")

        # Stream events
        events_received: list[dict] = []
        with client.stream("GET", f"/events/{task_id}") as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        evt = json.loads(line[6:])
                        events_received.append(evt)
                        etype = evt.get("event", "?")
                        if etype in ("task_started", "photo_done", "task_done"):
                            payload = evt.get("payload", {})
                            if etype == "task_done":
                                print(f"  ✓ {etype}: {payload.get('summary', '')[:80]}")
                            else:
                                print(f"  · {etype}")
                        if etype == "task_done":
                            break
                    except json.JSONDecodeError:
                        pass

        assert any(e["event"] == "task_done" for e in events_received), "no task_done event"
        print(f"✓ Cull task completed ({len(events_received)} events)")

        # 9. /tasks/<id> — verify photos in catalog
        r = client.get(f"/tasks/{task_id}")
        r.raise_for_status()
        task_data = r.json()
        assert task_data["photo_count"] == 5
        kept = sum(1 for p in task_data["photos"] if p.get("keep") == 1)
        culled = sum(1 for p in task_data["photos"] if p.get("keep") == 0)
        print(f"✓ Task catalog: 5 photos, {kept} kept, {culled} culled")

        # 10. /tasks/grade — same folder
        print("→ Starting grade task...")
        r = client.post("/tasks/grade", json={"folder_name": renamed["new_name"]})
        r.raise_for_status()
        grade_task_id = r.json()["task_id"]
        print(f"  task_id={grade_task_id[:8]}")

        # Stream until done
        grade_events = []
        with client.stream("GET", f"/events/{grade_task_id}") as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        evt = json.loads(line[6:])
                        grade_events.append(evt)
                        if evt.get("event") == "task_done":
                            break
                    except json.JSONDecodeError:
                        pass
        print(f"✓ Grade task completed ({len(grade_events)} events)")

        # 11. Verify output files exist
        output_dir = workspace / "output"
        out_folders = list(output_dir.iterdir())
        assert len(out_folders) >= 1
        out_folder = out_folders[0]
        jpegs = list(out_folder.glob("*.jpg"))
        print(f"✓ Output: {out_folder.name} contains {len(jpegs)} JPGs")
        assert len(jpegs) == 5, f"expected 5 graded jpegs, got {len(jpegs)}"

        # 12. /photos/<id>/feedback
        first_photo = task_data["photos"][0]
        r = client.post(f"/photos/{first_photo['id']}/feedback", json={"feedback": "up"})
        r.raise_for_status()
        print(f"✓ Feedback recorded: photo #{first_photo['id']} = up")

        # 13. /chat (streaming)
        print("→ Testing /chat (streaming)...")
        chunks: list[str] = []
        with client.stream("POST", "/chat", json={"message": "什么是光圈?"}) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    try:
                        evt = json.loads(line[6:])
                        if evt.get("event") == "chunk":
                            chunks.append(evt.get("payload", {}).get("text", ""))
                        elif evt.get("event") == "done":
                            break
                    except json.JSONDecodeError:
                        pass
        full_answer = "".join(chunks)
        assert "光圈" in full_answer, f"chat didn't mention 光圈: {full_answer}"
        print(f"✓ /chat OK ({len(chunks)} chunks, {len(full_answer)} chars)")
        print(f"  answer: {full_answer[:80]}...")

        client.close()
        print()
        print("=" * 60)
        print("✅ ALL SMOKE TESTS PASSED")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n❌ Smoke test FAILED: {e}", file=sys.stderr)
        # Print sidecar output for debugging
        try:
            proc.stdout.flush() if proc.stdout else None
        except Exception:
            pass
        return 1
    finally:
        # Terminate sidecar
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        # Cleanup
        # Leave the workspace for inspection (don't auto-clean)


if __name__ == "__main__":
    sys.exit(main())
