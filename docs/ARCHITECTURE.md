# 摄影师助手 (Photographer Copilot) — 架构文档

> **状态**:v0.2.0(已实装)
> **目标读者**:接手开发、扩展或维护本项目的工程师 / Agent

---

## 1. 一句话定义

跑在用户本地的「摄影工作区 Agent」。用户把照片文件夹丢进 `workspace/input/`,在聊天框下达指令(修图 / 筛片 / 提问),Agent 调用 M3 多模态 + 图片处理工具完成任务,产物落到 `workspace/output/`,并按约定重命名文件夹。

---

## 2. 目录结构

```
photographer-copilot/
├── README.md                # 用户入门文档
├── .env / .env.example      # M3 配置(env 已在 .gitignore)
├── .gitignore .dockerignore
├── Dockerfile               # 容器化开发环境
├── Makefile                 # 常用开发命令
├── start.sh / start.bat     # 一键启动
├── package.json             # 根 workspace 脚本(tauri:dev 等)
├── Cargo.toml               # Rust workspace
│
├── src-tauri/               # Rust / Tauri(桌面应用壳)
│   ├── tauri.conf.json
│   ├── capabilities/default.json
│   ├── icons/
│   └── src/
│       ├── main.rs / lib.rs    # Tauri 入口 + 托盘
│       ├── commands.rs          # 暴露给前端的命令
│       ├── sidecar.rs           # Python 子进程管理
│       └── workspace.rs         # 路径安全校验
│
├── sidecar/                 # Python(FastAPI + Agent)
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── pytest.ini          # pytest 配置
│   ├── agent/  image/  db/ # 业务模块
│   └── tests/              # pytest 单元测试
│
├── web/                     # React 前端
│   ├── package.json
│   ├── vite.config.ts
│   ├── pnpm-workspace.yaml # pnpm 构建设置
│   └── src/
│
├── scripts/
│   └── smoke_test.py       # 端到端冒烟测试
│
├── examples/
│   ├── sample_jpegs/       # 8 张测试用图
│   └── workflow_demo.md    # 端到端 demo 走查
│
├── workspace/              # RUNTIME 数据目录(gitignore 大部分内容)
│   ├── input/              # 用户放照片
│   ├── output/             # Agent 产出
│   ├── .tasks/             # 任务元数据
│   └── .logs/              # 日志
│
└── docs/
    └── ARCHITECTURE.md     # 本文件
```

**关键路径约定**:
- 项目根 `<project_root>` = 包含 `src-tauri/` 和 `sidecar/` 的目录
- 数据目录 `<workspace>` = `<project_root>/workspace/`
- 它们是**兄弟关系**,不是父子。Rust 端的 `project_root()` 和 `workspace_path()` 分别处理。

---

## 3. 关键决策(已与用户确认)

| 项 | 决策 |
|---|---|
| 大模型 | MiniMax M3,通过 **OpenRouter** 接入(`sk-or-v1-...` key) |
| 桌面框架 | Tauri 2.x(Rust + WebView) |
| 通信 | Tauri ↔ Sidecar = HTTP localhost 随机端口(sidecar 写 `.sidecar-port` 文件) |
| M3 mock | 无 key 时自动启用,代码层 fallback |
| M3 Plugin Header | 自动加 `X-MiniMax-Plugin-Version: 2`(function calling 必需) |
| 工作区 | `workspace/` 下(input / output / .tasks / .logs) |
| 文件夹命名 | 启动任务时重命名为 `<时间>-in`,完成后产物 `<时间>-out` |
| 输出 | JPEG + XMP sidecar(原图不动);筛片只**复制**合格品 |
| 风格学习数据 | SQLite `feedback` 字段已收 👍/👎,M0 顺手做 |
| 主观反馈 UI | UI 加 👍/👎 按钮,数据进 SQLite |
| Dark mode | 默认开(暗光环境) |
| RAW 支持 | MVP:CR2 / NEF / ARW / DNG(其他格式报清晰错误) |

---

## 4. 实际架构

```
┌────────────── Tauri 桌面应用 (Native Window + System Tray) ──────────────┐
│  ┌───────────────── Frontend (Webview) ─────────────────┐              │
│  │  React 18 + Vite + TS + Tailwind (Dark mode)         │              │
│  │  · 聊天框  · 快捷按钮  · 任务进度  · 👍/👎           │              │
│  └──────────────────────┬───────────────────────────────┘              │
│                         │ Tauri IPC (invoke + event)                    │
│  ┌──────────────────────▼───────────────────────────────┐              │
│  │  Rust Core (Tauri 2.x)                                │              │
│  │  · 文件监听  · 系统托盘  · 路径安全校验              │              │
│  │  · spawn + 管理 Python sidecar 进程                  │              │
│  │  · 转发 sidecar SSE 事件 → 前端 Tauri event          │              │
│  └──────────────────────┬───────────────────────────────┘              │
└─────────────────────────┼──────────────────────────────────────────────┘
                          │ HTTP localhost (workspace/.sidecar-port)
┌─────────────────────────▼──────────────────────────────────────────────┐
│  Python Sidecar (FastAPI)                                                │
│  · M3 client (多模态, OpenAI 兼容, mock fallback)                       │
│  · 9 个工具:列文件夹 / 重命名 / 修图 / 筛片 / 反馈 / 知识问答 / ...   │
│  · RAW → JPEG 预览 (rawpy) + 调色 (Pillow+numpy) + XMP sidecar         │
│  · SQLite 索引 (catalog.sqlite) — 决策 / 反馈 / 教学卡片                │
│  · SSE 事件流 (任务进度实时推)                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 5. 启动流程

```
user runs ./start.sh
  ↓
check python3 / node / cargo / pnpm
  ↓
prepare venv, install deps (first time only)
  ↓
copy .env.example → .env (if missing)
  ↓
source .env into current shell  ← 关键,否则 M3 走 mock
  ↓
exec pnpm tauri:dev
  ↓
tauri reads tauri.conf.json (src-tauri/)
  ↓
beforeDevCommand: "cd ../web && pnpm dev"  ← 启 Vite
  ↓
Tauri opens main window
  ↓
Rust setup: spawn Python sidecar
  ↓
Sidecar starts, writes workspace/.sidecar-port
  ↓
Rust reads port, opens HTTP bridge
  ↓
Frontend ready
```

---

## 6. 关键文件说明

### Python sidecar
- `sidecar/agent/main.py` — FastAPI 入口,9 个 HTTP 端点
- `sidecar/agent/m3_client.py` — M3 client(mock 模式 + 真实模式)
- `sidecar/agent/tools.py` — 9 个工具函数 + tool schemas + ContextVar 任务上下文
- `sidecar/agent/prompts.py` — System prompts + JSON schema hints
- `sidecar/image/raw_processor.py` — RAW → JPEG 预览
- `sidecar/image/color_grade.py` — Lightroom 风格调色 + XMP 输出
- `sidecar/db/catalog.py` — SQLite 索引(照片元数据 + 反馈)

### Rust core
- `src-tauri/src/lib.rs` — Tauri Builder + 托盘 + 窗口事件
- `src-tauri/src/commands.rs` — 暴露给前端的 #[tauri::command] 函数
- `src-tauri/src/sidecar.rs` — Python 进程管理 + HTTP 桥接
- `src-tauri/src/workspace.rs` — 路径解析 + 边界校验
- `src-tauri/tauri.conf.json` — 窗口/打包/托盘/能力配置

### React 前端
- `web/src/App.tsx` — 主布局 + 状态管理
- `web/src/lib/tauri.ts` — Tauri command 包装
- `web/src/components/` — ChatBox / QuickActions / Sidebar / TaskProgress / ToolCallBlock

---

## 7. 验证状态

### ✅ 已通过
- 29 个 Python 单元测试全过
- 端到端冒烟测试全过(mock 模式)
- 前端 build 成功(156 kB → gzip 51 kB)
- **真实 M3 调用验证通过**(OpenRouter 接入 + 多模态 + JSON schema)

### ⏳ 待用户在本地验证
- `pnpm tauri:dev` 启动桌面窗口
- 真实 M3 key 接入后,修图/筛片的视觉质量
- macOS / Windows 跨平台兼容性
- Tauri 打包 (.dmg / .msi)

---

## 8. 后续扩展点

代码里都标了 `Reserved for future:` 注释,主要钩子:

| 模块 | 预留点 | 说明 |
|---|---|---|
| `m3_client.py` | persona / batch / cache | 风格学习、批量、缓存 |
| `tools.py` | `emit_event` + 工具注册表 | 加新功能不改协议 |
| `catalog.py` | `teaching_card` 列 | M1 教学卡片直接加列 |
| `tauri.conf.json` | 托盘 | 智能插板硬件入口 |
| `m3_client.py` | mock 模式 | 无 key 也能 demo + 测试 |
