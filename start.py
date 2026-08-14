#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
摄影师助手 (Photographer Copilot) — 一键启动器
==============================================

MVP 0.1.0 跑法:
  1. 检查 Python / Node / pnpm(缺啥自动装)
  2. 配 pip / pnpm 镜像源(默认清华 + npmmirror)
  3. 装 Python 依赖(创建 venv)
  4. 装 Node 依赖
  5. 配 .env(没就复制)
  6. 起 sidecar + Vite,浏览器开 http://localhost:1420 就能用

桌面模式(v0.2.0+ 预留,目前不需要):
  python start.py --desktop    # 走 Tauri 桌面壳(需要 MSVC Build Tools)

用法:
  python start.py              # 默认:web 模式(MVP 0.1.0)
  python start.py --check      # 只检查环境,不装不启
  python start.py --install    # 只装依赖,不启
  python start.py --desktop    # Tauri 桌面壳(v0.2.0+ 预留)
  python start.py --help
"""
import os
import sys
import subprocess
import platform
import shutil
import time
from pathlib import Path

# ---- 常量 ----
ROOT = Path(__file__).parent.resolve()
WORKSPACE = ROOT / "workspace"
BACKEND = ROOT / "backend"     # Python FastAPI sidecar (formerly sidecar/)
FRONTEND = ROOT / "frontend"   # React + Vite (formerly web/)
SIDECAR = BACKEND              # 别名兼容老代码
WEB = FRONTEND                 # 别名兼容老代码
RUN_DIR = ROOT / ".run"        # 存 PID 文件
PIDS_FILE = RUN_DIR / "pids.json"
SCRIPTS = ROOT / "scripts"

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# ---- 颜色(支持现代 Windows Terminal / Linux / macOS)----
class C:
    R = '\033[91m'
    G = '\033[92m'
    Y = '\033[93m'
    B = '\033[94m'
    CY = '\033[96m'
    BOLD = '\033[1m'
    N = '\033[0m'


def _print(color, prefix, msg):
    """带颜色打印。Windows 10 1607+ 的 cmd 默认支持 ANSI。"""
    line = f"{color}{prefix}{C.N} {msg}"
    print(line, flush=True)


def info(msg):  _print(C.B,    "[INFO]", msg)
def ok(msg):    _print(C.G,    "[ OK ]", msg)
def warn(msg):  _print(C.Y,    "[WARN]", msg)
def err(msg):   _print(C.R,    "[ERR ]", msg)
def step(n, total, name):
    print(f"\n{C.CY}{C.BOLD}{'='*60}{C.N}", flush=True)
    print(f"{C.CY}{C.BOLD}  [{n}/{total}] {name}{C.N}", flush=True)
    print(f"{C.CY}{C.BOLD}{'='*60}{C.N}\n", flush=True)


# ---- 工具函数 ----
def run(cmd, **kw):
    """跑命令,失败抛异常。cwd 默认 ROOT。Windows 上自动解析绝对路径。"""
    if "cwd" not in kw:
        kw["cwd"] = ROOT
    if isinstance(cmd, list) and cmd:
        # Windows: 把第一个元素(命令名)替换成绝对路径
        first = cmd[0]
        if isinstance(first, str):
            p = shutil.which(first)
            if p:
                cmd = [p] + list(cmd[1:])
    return subprocess.run(cmd, **kw)


def run_ok(cmd, **kw):
    """跑命令,返回是否成功。"""
    try:
        r = run(cmd, capture_output=True, **kw)
        return r.returncode == 0
    except Exception:
        return False


def check_version(cmd, version_arg="--version", min_major=None):
    """跑 --version,返回 (found, version_string, major)。
    用 shutil.which 拿绝对路径,避免 Windows 上 .cmd / .ps1 找不到的坑。
    """
    path = shutil.which(cmd)
    if not path:
        return False, "", 0
    try:
        r = subprocess.run(
            [path, version_arg],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return False, "", 0
        out = (r.stdout or "") + (r.stderr or "")
        # 抓第一个像 X.Y 的数字
        import re
        m = re.search(r'(\d+)\.(\d+)', out)
        if m:
            major = int(m.group(1))
            return True, out.strip().split('\n')[0], major
        return True, out.strip().split('\n')[0], 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, Exception):
        return False, "", 0


def has(cmd):
    return shutil.which(cmd) is not None


def cmd_path(cmd):
    """返回 cmd 的绝对路径(Windows 上会包含 .exe / .cmd 后缀)。"""
    return shutil.which(cmd)


def run_cmd(cmd_args, **kw):
    """用绝对路径跑命令,避免 Windows shim 问题。"""
    if cmd_args and isinstance(cmd_args[0], str):
        p = shutil.which(cmd_args[0])
        if p:
            cmd_args = [p] + list(cmd_args[1:])
    return subprocess.run(cmd_args, **kw)


def ask_yn(prompt, default="n"):
    """交互式 y/n,EOF 时返回 default(用 N 让 CI 不挂)。"""
    try:
        ans = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default == "y"
    if not ans:
        return default == "y"
    return ans in ("y", "yes")


def wait_for_key(prompt="按回车键关闭此窗口..."):
    """等用户按键,EOF 时直接返回(非交互环境不挂)。"""
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()


def kill_port(port):
    """杀掉占用指定端口的进程(Windows + Unix)。失败也不抛。"""
    try:
        if IS_WINDOWS:
            # netstat 找 PID,taskkill 杀
            r = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5,
            )
            for line in r.stdout.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit():
                        subprocess.run(
                            ["taskkill", "/F", "/PID", pid],
                            capture_output=True, timeout=5,
                        )
        else:
            # lsof 或 fuser
            r = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5,
            )
            for pid in r.stdout.split():
                if pid.isdigit():
                    subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
    except Exception:
        pass  # 静默失败,不影响主流程


def is_port_free(port: int) -> bool:
    """检查端口是否空闲(没被别的进程占用)。"""
    import socket as _sock
    with _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM) as s:
        s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def load_env_file(path=None):
    """读 .env,合并到 os.environ(子进程继承)。返回合并后的 env dict。"""
    env = os.environ.copy()
    if path is None:
        path = ROOT / ".env"
    if not path.exists():
        return env
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    except Exception as e:
        warn(f"读 .env 失败: {e}")
    return env


# ---- 工具检测与安装 ----
def check_python():
    found, ver, major = check_version("python", "-V", 3)
    if not found:
        # Windows 上有时是 py 不是 python
        found, ver, major = check_version("py", "-V", 3)
    if not found:
        return False, ""
    if major < 3 or (major == 3 and int(ver.split('.')[1] if '.' in ver else '0') < 11):
        return False, ver
    return True, ver


def check_node():
    found, ver, major = check_version("node")
    if not found:
        return False, ""
    if major < 20:
        return False, ver
    return True, ver


def check_pnpm():
    return has("pnpm")


def install_pnpm_via_corepack():
    """用 corepack 装 pnpm(免 admin)。"""
    corepack_path = cmd_path("corepack")
    if not corepack_path:
        warn("corepack 不在,试 npm install -g pnpm")
        npm_path = cmd_path("npm")
        if not npm_path:
            return False
        subprocess.run([npm_path, "install", "-g", "pnpm"], capture_output=True)
        return cmd_path("pnpm") is not None
    info("用 corepack 装 pnpm...")
    try:
        subprocess.run([corepack_path, "enable"], capture_output=True, timeout=30)
        subprocess.run([corepack_path, "prepare", "pnpm@latest", "--activate"],
                       capture_output=True, text=True, timeout=60)
    except (subprocess.TimeoutExpired, Exception) as e:
        warn(f"corepack 出错: {e}")
    return cmd_path("pnpm") is not None


def install_via_winget(pkg_id, display_name):
    """Windows: 用 winget 装。"""
    if not has("winget"):
        err(f"winget 不在,手动装 {display_name}")
        err(f"  https://aka.ms/getwinget  (装 winget)")
        err(f"  然后重跑 start.py")
        return False
    info(f"用 winget 装 {display_name}...")
    winget_path = cmd_path("winget")
    r = subprocess.run(
        [winget_path, "install", "-e", "--id", pkg_id,
         "--accept-source-agreements", "--accept-package-agreements"],
        text=True,
    )
    return r.returncode == 0


def install_via_brew(pkg):
    """macOS: 用 brew 装。"""
    if not has("brew"):
        err("brew 不在,装 Homebrew:")
        err("  /bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"")
        return False
    info(f"用 brew 装 {pkg}...")
    return run_ok(["brew", "install", pkg])


def install_via_apt(pkg):
    info(f"用 apt 装 {pkg}...")
    return run_ok(["sudo", "apt", "update"]) and run_ok(["sudo", "apt", "install", "-y", pkg])


def install_via_dnf(pkg):
    info(f"用 dnf 装 {pkg}...")
    return run_ok(["sudo", "dnf", "install", "-y", pkg])


def step1_check_tools():
    step(1, 6, "检查工具")
    print(f"{C.BOLD}工作目录:{C.N} {ROOT}\n")

    results = {}

    # Python
    ok_py, ver = check_python()
    if ok_py:
        ok(f"Python:  {ver}")
        results['python'] = True
    else:
        warn(f"Python 不在或版本不够 (当前: {ver or '没有'})")
        if IS_WINDOWS:
            if install_via_winget("Python.Python.3.12", "Python 3.12"):
                ok("Python 装好了,**新开一个终端**再跑 start.py")
                sys.exit(0)
        elif IS_MAC:
            if install_via_brew("python@3.12"):
                ok("Python 装好了,**新开一个终端**再跑 start.py")
                sys.exit(0)
        elif IS_LINUX:
            pkg_mgr = "apt" if has("apt") else ("dnf" if has("dnf") else "pacman")
            if install_via_apt("python3 python3-pip python3-venv") or install_via_dnf("python3 python3-pip"):
                ok("Python 装好了,**新开一个终端**再跑 start.py")
                sys.exit(0)
        err("Python 装失败,手动装: https://www.python.org/downloads/")
        sys.exit(1)

    # Node
    ok_node, ver = check_node()
    if ok_node:
        ok(f"Node:    {ver}")
    else:
        warn(f"Node 不在或 < 20 (当前: {ver or '没有'})")
        if IS_WINDOWS:
            install_via_winget("OpenJS.NodeJS.LTS", "Node.js LTS")
        elif IS_MAC:
            install_via_brew("node@20")
        elif IS_LINUX:
            install_via_apt("nodejs npm") or install_via_dnf("nodejs npm")
        ok_node, ver = check_node()
        if not ok_node:
            err("Node 装失败,手动装: https://nodejs.org/")
            sys.exit(1)
        ok(f"Node:    {ver},**新开终端**再跑")

    # pnpm
    pnpm_path = cmd_path("pnpm")
    if pnpm_path:
        try:
            r = subprocess.run([pnpm_path, "-v"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                ok(f"pnpm:   v{r.stdout.strip()}")
            else:
                raise FileNotFoundError("pnpm runs but exits non-zero")
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError, Exception) as e:
            warn(f"pnpm 在 PATH 但跑不了 ({e}),重装...")
            pnpm_path = None
    if not pnpm_path:
        warn("pnpm 不在,自动装...")
        if not install_pnpm_via_corepack():
            err("pnpm 装失败,手动: npm install -g pnpm")
            sys.exit(1)
        ok("pnpm 装好了")



def step2_registries():
    step(2, 6, "配 pip / pnpm 镜像源")
    info("设 pip 清华源...")
    run_ok(["python", "-m", "pip", "config", "set", "global.index-url",
            "https://pypi.tuna.tsinghua.edu.cn/simple"])
    ok("pip: pypi.tuna.tsinghua.edu.cn")
    info("设 pnpm npmmirror 源...")
    if (WEB / "pnpm-lock.yaml").exists() or (WEB / "package.json").exists():
        run(["pnpm", "config", "set", "registry", "https://registry.npmmirror.com"], cwd=WEB)
    else:
        run_ok(["pnpm", "config", "set", "registry", "https://registry.npmmirror.com"])
    ok("pnpm: registry.npmmirror.com")


def step3_python_deps():
    step(3, 6, "装 Python 依赖")
    venv = SIDECAR / ".venv"
    venv_python = venv / "Scripts" / "python.exe" if IS_WINDOWS else venv / "bin" / "python"
    req = SIDECAR / "requirements.txt"

    if not venv.exists():
        info("创建 venv...")
        r = run(["python", "-m", "venv", str(venv)])
        if r.returncode != 0:
            err("venv 创建失败")
            sys.exit(1)

    # 检查
    r = subprocess.run(
        [str(venv_python), "-c", "import fastapi, openai, httpx, rawpy"],
        capture_output=True,
    )
    if r.returncode == 0:
        ok("Python 依赖已就绪")
        return

    info("装 Python 依赖(1-2 分钟)...")
    r = run([str(venv_python), "-m", "pip", "install", "-q", "--upgrade", "pip"])
    r = run(
        [str(venv_python), "-m", "pip", "install", "-q", "-r", str(req)],
    )
    if r.returncode != 0:
        err("Python 依赖装失败,看上面错误")
        sys.exit(1)
    ok("Python 依赖 OK")


def step4_node_deps():
    step(4, 6, "装 Node 依赖")
    if (WEB / "node_modules").exists():
        ok("Node 依赖已就绪")
        return

    if not (WEB / "package.json").exists():
        err(f"web/package.json 不存在: {WEB}")
        sys.exit(1)

    info("装 Node 依赖(1-2 分钟)...")
    r = run(["pnpm", "install"], cwd=WEB)
    if r.returncode != 0:
        err("Node 依赖装失败,看上面错误")
        sys.exit(1)
    ok("Node 依赖 OK")


def step5_config():
    step(5, 6, "配 .env")
    env_file = ROOT / ".env"
    example = ROOT / ".env.example"
    if env_file.exists():
        ok(".env 存在")
    elif example.exists():
        shutil.copy2(example, env_file)
        ok(".env 已从 .env.example 复制")
        warn("编辑 .env 填 M3_API_KEY(否则 M3 走 mock 模式)")
    else:
        err(".env 和 .env.example 都不在")
        sys.exit(1)

    # 检查 workspace 目录
    (WORKSPACE / "input").mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "output").mkdir(parents=True, exist_ok=True)
    ok(f"workspace 目录就绪: {WORKSPACE}")



def do_check():
    """只检查环境,不装不启。"""
    print(f"\n{C.CY}{C.BOLD}{'='*60}{C.N}")
    print(f"{C.CY}{C.BOLD}  摄影师助手 — 环境检查{C.N}")
    print(f"{C.CY}{C.BOLD}{'='*60}{C.N}\n")
    print(f"{C.BOLD}工作目录:{C.N} {ROOT}\n")

    py_ok, py_ver = check_python()
    if py_ok: ok(f"Python:  {py_ver}")
    else:     err(f"Python 不在或版本不够 ({py_ver or '没有'})")

    if check_node(): ok(f"Node:    v{sys.version.split()[0]}")  # placeholder
    nd = cmd_path("node")
    if nd:
        r = subprocess.run([nd, "--version"], capture_output=True, text=True, timeout=5)
        if r.stdout: ok(f"Node:    {r.stdout.strip()}")

    if check_pnpm():
        p = cmd_path("pnpm")
        if p:
            r = subprocess.run([p, "-v"], capture_output=True, text=True, timeout=5)
            ok(f"pnpm:    v{r.stdout.strip()}")

    venv = SIDECAR / ".venv"
    venv_py = venv / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
    if venv_py.exists():
        r = subprocess.run([str(venv_py), "-c", "import fastapi, openai, httpx, rawpy"],
                           capture_output=True, timeout=10)
        if r.returncode == 0: ok("Python 依赖:  完整 (fastapi/openai/httpx/rawpy)")
        else: warn("Python 依赖:  缺一些包")
    else:
        warn("Python 依赖:  venv 不在")

    if (WEB / "node_modules").exists():
        ok("Node 依赖:    web/node_modules 存在")
    else:
        warn("Node 依赖:    web/node_modules 不在")

    if (ROOT / ".env").exists(): ok(".env:         存在")
    else: warn(".env:         不在")

    (WORKSPACE / "input").mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "output").mkdir(parents=True, exist_ok=True)
    ok(f"workspace:    {WORKSPACE}")
    print()


def step6_run_web():
    """默认启动:sidecar + Vite,浏览器开 http://localhost:1420 就能用。"""
    step(6, 6, "启动应用 (Web 模式)")

    # 先杀残留(防止上次没正常退出)
    RUN_DIR.mkdir(exist_ok=True)
    if PIDS_FILE.exists():
        info("检测到上次 PID 文件,先清理...")
        try:
            import json as _json
            old = _json.loads(PIDS_FILE.read_text())
            for name, pid in old.items():
                if IS_WINDOWS:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"],
                                   capture_output=True, timeout=5)
                else:
                    try: os.kill(int(pid), 9)
                    except Exception: pass
        except Exception:
            pass
        PIDS_FILE.unlink(missing_ok=True)
    # 端口兜底
    kill_port(8765)
    kill_port(1420)

    env = load_env_file()
    env["WORKSPACE_PATH"] = str(WORKSPACE)
    env["VITE_SIDECAR_PORT"] = "8765"
    env["SIDECAR_PORT"] = "8765"

    if IS_WINDOWS:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0
    else:
        creationflags = 0

    # 起 Sidecar
    SIDECAR_PORT = 8765
    sidecar_proc = None
    # 二次确认端口真闲下来了(有些进程 kill 后 TIME_WAIT 还会占端口几秒)
    if not is_port_free(SIDECAR_PORT):
        err(f"Sidecar 端口 {SIDECAR_PORT} 仍被占用,清理失败。请手动关掉占用进程后重试。")
        err("  占用查询:  Windows: netstat -ano | findstr :8765")
        err("              macOS/Linux: lsof -i :8765")
        if not wait_for_key("按回车键关闭..."):
            sys.exit(1)
        sys.exit(1)
    venv = SIDECAR / ".venv"
    venv_python = (venv / "Scripts" / "python.exe") if IS_WINDOWS else (venv / "bin" / "python")
    if venv_python.exists():
        info(f"启动 Sidecar (端口 {SIDECAR_PORT})...")
        (WORKSPACE / ".sidecar-port").unlink(missing_ok=True)
        # stdout / stderr 留 None(继承父进程),这样 uvicorn 的 access log
        # + 我们所有 logger 的 INFO/WARNING/ERROR 全部实时显示在用户运行
        # 的 cmd 里 —— 替代了前端那个"后端活动" tab 的角色,方便后期
        # 排查 / 优化时直接看上下文。
        # 文件日志同时写到 workspace/.logs/sidecar.log。
        try:
            sidecar_proc = subprocess.Popen(
                [str(venv_python), "-m", "agent.main"],
                cwd=SIDECAR, env=env,
                stdout=None, stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception as e:
            err(f"起 sidecar 失败: {e}")
        if sidecar_proc:
            import urllib.request as _ur
            for _ in range(30):
                if sidecar_proc.poll() is not None:
                    break
                try:
                    with _ur.urlopen(f"http://127.0.0.1:{SIDECAR_PORT}/health", timeout=1) as r:
                        if r.status == 200:
                            ok(f"Sidecar 起来了: http://127.0.0.1:{SIDECAR_PORT}")
                            break
                except Exception:
                    pass
                time.sleep(0.5)
    else:
        warn("sidecar venv 不在,先跑 start.py --install 装依赖")

    # 起 Vite
    info("启动 Vite 开发服务器...")
    kill_port(1420)
    if not is_port_free(1420):
        err("Vite 端口 1420 仍被占用,清理失败。请手动关掉占用进程后重试。")
        err("  Windows: netstat -ano | findstr :1420")
        err("  macOS/Linux: lsof -i :1420")
    pnpm_exe = cmd_path("pnpm")
    vite_proc = None
    if pnpm_exe:
        vite_env = env.copy()
        vite_env["PATH"] = str(WEB / "node_modules" / ".bin") + os.pathsep + vite_env.get("PATH", "")
        # vite 也走 inherited,这样 HMR / 编译错误也会出现在 cmd 里。
        # 加 [vite] 前缀方便和 sidecar 日志区分。
        try:
            vite_proc = subprocess.Popen(
                [pnpm_exe, "dev"], cwd=WEB, env=vite_env,
                stdout=None, stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            import urllib.request as _ur
            for _ in range(30):
                if vite_proc.poll() is not None: break
                try:
                    with _ur.urlopen("http://localhost:1420", timeout=2) as r:
                        if r.status == 200: break
                except Exception: pass
                time.sleep(1)
        except Exception as e:
            err(f"起 vite 失败: {e}")

    # 就绪
    print(f"\n{C.G}{C.BOLD}{'='*60}{C.N}")
    print(f"{C.G}{C.BOLD}  ✓ 应用已就绪{C.N}")
    print(f"{C.G}{'='*60}{C.N}\n")
    print(f"  {C.BOLD}打开浏览器,访问:{C.N}")
    print()
    print(f"    {C.CY}http://localhost:1420{C.N}")
    print()
    print(f"  {C.BOLD}Sidecar:{C.N}   http://127.0.0.1:{SIDECAR_PORT}/health")
    print(f"  {C.BOLD}工作区:{C.N}    {WORKSPACE}")
    print()
    print(f"  {C.BOLD}操作:{C.N}")
    print(f"    · 把照片文件夹放进 workspace/input/")
    print(f"    · 聊天:问摄影问题 / 一键修图 / 一键筛片")
    print(f"    · 状态:看 Sidebar 绿点")
    print(f"    · 日志:点 Sidebar 的「后端活动」")
    print()
    print(f"  {C.BOLD}关闭:{C.N}")
    print(f"    · 当前窗口:  Ctrl+C")
    print(f"    · 其他窗口:  双击 停止.bat")
    print(f"    · 诊断:      双击 诊断.bat (生成 diagnose.txt)")
    print(f"{C.G}{'='*60}{C.N}\n")

    # 写 PID 文件,方便 停止.bat / stop.py 找
    try:
        import json as _json
        RUN_DIR.mkdir(exist_ok=True)
        pids = {}
        if sidecar_proc: pids["sidecar"] = sidecar_proc.pid
        if vite_proc: pids["vite"] = vite_proc.pid
        PIDS_FILE.write_text(_json.dumps(pids))
    except Exception:
        pass

    try:
        while True:
            time.sleep(1)
            if sidecar_proc and sidecar_proc.poll() is not None:
                warn("Sidecar 进程已退出")
                break
            if vite_proc and vite_proc.poll() is not None:
                warn("Vite 进程已退出")
                break
    except KeyboardInterrupt:
        print("\n关闭中...")

    for proc in [sidecar_proc, vite_proc]:
        if proc is None: continue
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
    kill_port(1420)
    kill_port(SIDECAR_PORT)
    print("完成。\n")


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0

    print(f"\n{C.CY}{C.BOLD}{'='*60}{C.N}")
    print(f"{C.CY}{C.BOLD}  摄影师助手 (Photographer Copilot) — 一键启动{C.N}")
    print(f"{C.CY}{C.BOLD}{'='*60}{C.N}")
    print(f"{C.BOLD}  系统:{C.N} {platform.system()} {platform.release()}")
    print(f"{C.BOLD}  Python:{C.N} {sys.version.split()[0]}")
    print(f"{C.BOLD}  工作目录:{C.N} {ROOT}")

    if "--check" in sys.argv:
        do_check()
        return 0

    step1_check_tools()
    if "--install" not in sys.argv:
        step2_registries()
        step3_python_deps()
        step4_node_deps()
        step5_config()
        step6_run_web()
    else:
        step2_registries()
        step3_python_deps()
        step4_node_deps()
        step5_config()
        print(f"\n{C.G}✓ 所有依赖装好了。跑 start.py 启动应用。{C.N}\n")
    wait_for_key()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n用户中断。")
        sys.exit(130)
    except SystemExit as e:
        raise
    except Exception as e:
        err(f"未预期错误: {e}")
        import traceback
        traceback.print_exc()
        wait_for_key()
        sys.exit(1)
