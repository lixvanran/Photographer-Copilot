#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
摄影师助手 (Photographer Copilot) — 诊断

生成 diagnose.txt,包含:
- 系统信息
- Python / Node / pnpm 版本
- 项目结构
- .env 状态(不显示真 key)
- sidecar / Vite 是否在跑
- workspace 状态
- 最近 30 行 sidecar 日志

用法: python diagnose.py
输出: ./diagnose.txt
"""
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
REPORT = ROOT / "diagnose.txt"
IS_WINDOWS = platform.system() == "Windows"
NOW = time.strftime("%Y-%m-%d %H:%M:%S")


def run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=IS_WINDOWS)
        return (r.returncode, (r.stdout or "") + (r.stderr or ""))
    except Exception as e:
        return (-1, str(e))


def section(title):
    return f"\n--- {title} ---\n"


def main():
    lines = []
    lines.append("=" * 60)
    lines.append(f"  Photographer Copilot - Diagnostic Report")
    lines.append(f"  {NOW}")
    lines.append("=" * 60)

    # 系统
    lines.append(section("System"))
    lines.append(f"OS:        {platform.system()} {platform.release()} ({platform.version()})")
    lines.append(f"Arch:      {platform.machine()}")
    lines.append(f"Hostname:  {socket.gethostname()}")
    lines.append(f"User:      {os.environ.get('USERNAME') or os.environ.get('USER')}")
    lines.append(f"CWD:       {os.getcwd()}")
    lines.append(f"ROOT:      {ROOT}")
    if IS_WINDOWS:
        _, out = run("ver")
        lines.append(f"ver:       {out.strip()}")

    # Python
    lines.append(section("Python"))
    lines.append(f"Version:   {sys.version.split()[0]}")
    lines.append(f"Exec:      {sys.executable}")
    lines.append(f"Pip config:")
    _, out = run(["python", "-m", "pip", "config", "list"])
    for l in out.strip().split("\n")[:10]:
        lines.append(f"  {l}")
    venv = ROOT / "backend" / ".venv"
    if venv.exists():
        vpy = venv / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
        lines.append(f"Venv:      {vpy} (exists)")
    else:
        lines.append(f"Venv:      (missing)")

    # Node / pnpm
    lines.append(section("Node / pnpm"))
    for tool in ["node", "npm", "pnpm", "corepack"]:
        path = shutil.which(tool)
        lines.append(f"  {tool:10s} {path or '(not found)'}")
    for tool in ["node", "pnpm"]:
        path = shutil.which(tool)
        if path:
            _, out = run([path, "--version"] if tool == "node" else [path, "-v"])
            lines.append(f"  {tool:10s} {out.strip()}")

    # 项目结构
    lines.append(section("Project Structure"))
    for p in sorted(ROOT.iterdir()):
        if p.name.startswith(".") and p.name not in (".env", ".run"):
            continue
        kind = "/" if p.is_dir() else ""
        size = ""
        if p.is_file():
            try:
                size = f" ({p.stat().st_size} B)"
            except OSError:
                pass
        lines.append(f"  {p.name}{kind}{size}")

    # .env
    lines.append(section("Environment (.env)"))
    env = ROOT / ".env"
    if env.exists():
        lines.append(f"  exists: yes ({env.stat().st_size} B)")
        for line in env.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "KEY" in line.upper() or "SECRET" in line.upper() or "TOKEN" in line.upper():
                key, _, _ = line.partition("=")
                lines.append(f"  {key}=*** (hidden)")
            else:
                lines.append(f"  {line}")
    else:
        lines.append("  exists: NO")
        lines.append("  hint: copy .env.example to .env and fill in M3_API_KEY")

    # 后端 / 前端是否在跑
    lines.append(section("Running Processes (port check)"))
    for port, name in [(8765, "sidecar"), (1420, "vite")]:
        if IS_WINDOWS:
            _, out = run(["netstat", "-ano", "-p", "TCP"])
            listening = [l for l in out.splitlines() if f":{port} " in l and "LISTENING" in l]
        else:
            _, out = run(["lsof", "-i", f":{port}"])
            listening = [l for l in out.splitlines() if "LISTEN" in l]
        if listening:
            lines.append(f"  port {port:4d} ({name:8s}): UP")
            for l in listening[:3]:
                lines.append(f"    {l.strip()}")
        else:
            lines.append(f"  port {port:4d} ({name:8s}): DOWN")

    # sidecar /health (if up)
    lines.append(section("Sidecar /health (if up)"))
    try:
        import urllib.request, json
        with urllib.request.urlopen("http://127.0.0.1:8765/health", timeout=2) as r:
            data = json.loads(r.read())
            for k, v in data.items():
                lines.append(f"  {k}: {v}")
    except Exception as e:
        lines.append(f"  (not reachable: {e})")

    # workspace
    lines.append(section("Workspace"))
    ws = ROOT / "workspace"
    for sub in ["input", "output", ".tasks", ".logs"]:
        p = ws / sub
        if p.exists():
            count = sum(1 for _ in p.iterdir()) if p.is_dir() else 0
            lines.append(f"  {sub:8s}: exists ({count} items)")
        else:
            lines.append(f"  {sub:8s}: missing")

    # 最近日志
    lines.append(section("Recent Logs"))
    log_files = [
        ROOT / "workspace" / ".logs" / "sidecar.log",
        ROOT / "workspace" / ".logs" / "rust.log",
    ]
    for lf in log_files:
        if not lf.exists():
            lines.append(f"  {lf.relative_to(ROOT)}: (no file)")
            continue
        lines.append(f"  {lf.relative_to(ROOT)} (last 20 lines):")
        try:
            content = lf.read_text(errors="replace")
            for line in content.splitlines()[-20:]:
                lines.append(f"    {line}")
        except Exception as e:
            lines.append(f"    (read error: {e})")

    # 写文件
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text, encoding="utf-8")

    print()
    print("=" * 60)
    print(f"  Diagnostic report written to:")
    print(f"  {REPORT}")
    print("=" * 60)
    print()
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
