#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
摄影师助手 (Photographer Copilot) — 停止器

杀掉之前 start.py 起的 sidecar 和 vite 进程。
读 .run/pids.json 拿 PID,Windows 用 taskkill,Unix 用 kill。
如果 PID 文件不在,会按端口(8765, 1420)兜底杀。
"""
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
RUN_DIR = ROOT / ".run"
PIDS_FILE = RUN_DIR / "pids.json"

IS_WINDOWS = platform.system() == "Windows"

# ANSI 颜色
class C:
    B = "\033[1m"; N = "\033[0m"; Y = "\033[93m"; G = "\033[92m"; R = "\033[91m"; C = "\033[96m"

def _print(c, prefix, msg):
    print(f"{c}{C.B}{prefix}{C.N} {msg}")

def info(m): _print("\033[94m", "[INFO]", m)
def ok(m):   _print("\033[92m", "[ OK ]", m)
def warn(m): _print("\033[93m", "[WARN]", m)
def err(m):  _print("\033[91m", "[ERR ]", m)


def kill_pid(pid: int, name: str) -> bool:
    """杀单个 PID。返回是否成功。"""
    if not pid or pid <= 0:
        return False
    try:
        if IS_WINDOWS:
            r = subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                ok(f"已杀 {name} (PID {pid})")
                return True
            else:
                warn(f"taskkill PID {pid}: {r.stderr.strip()[:200]}")
                return False
        else:
            os.kill(pid, 9)
            ok(f"已杀 {name} (PID {pid})")
            return True
    except (ProcessLookupError, PermissionError, OSError) as e:
        warn(f"杀 {name} (PID {pid}) 失败: {e}")
        return False


def kill_by_port(port: int, name: str) -> bool:
    """按端口找占用进程并杀。"""
    try:
        if IS_WINDOWS:
            r = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5,
            )
            killed_any = False
            for line in r.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit() and int(pid) > 0:
                        if kill_pid(int(pid), f"{name}@:{port}"):
                            killed_any = True
            return killed_any
        else:
            r = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5,
            )
            killed_any = False
            for pid in r.stdout.split():
                if pid.isdigit():
                    if kill_pid(int(pid), f"{name}@:{port}"):
                        killed_any = True
            return killed_any
    except Exception as e:
        warn(f"按端口 {port} 杀进程失败: {e}")
        return False


def main():
    print(f"\n{C.C}{C.B}{'='*60}{C.N}")
    print(f"{C.C}{C.B}  摄影师助手 — 停止{C.N}")
    print(f"{C.C}{C.B}{'='*60}{C.N}\n")

    pids = {}
    if PIDS_FILE.exists():
        try:
            pids = json.loads(PIDS_FILE.read_text())
        except Exception as e:
            warn(f"读 PID 文件失败: {e}")

    # 1. 先按已知 PID 杀
    if pids.get("sidecar"):
        kill_pid(int(pids["sidecar"]), "sidecar")
    if pids.get("vite"):
        kill_pid(int(pids["vite"]), "vite")

    # 2. 兜底:按端口杀
    info("按端口兜底 (8765 sidecar, 1420 vite)...")
    kill_by_port(8765, "sidecar")
    kill_by_port(1420, "vite")

    # 3. 清 PID 文件
    if PIDS_FILE.exists():
        PIDS_FILE.unlink()
        ok("PID 文件已清")

    print()
    ok("已停止。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中断。")
        sys.exit(130)
