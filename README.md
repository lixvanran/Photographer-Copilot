# 摄影师助手 · Photographer Copilot

一个跑在用户本地的「摄影工作区 Agent」,用 MiniMax M3 多模态帮摄影师做**一键调色 / 一键筛片 / 回答摄影问题**。数据不离开电脑。

![Version](https://img.shields.io/badge/version-v0.2.0-blue.svg)
![Status](https://img.shields.io/badge/status-stable-green.svg)
![License](https://img.shields.io/badge/license-MIT-purple.svg)

## v0.2.0 升级要点

这一版核心解决两个真在用的痛点 + 顺手清理了若干历史包袱。

- **修图不再"静默失败"** — 之前修图/筛片过程的所有日志(`Grading xxx.jpg` / `Wrote XMP sidecar` / `M3 returned invalid JSON` 等)只在磁盘 `sidecar.log` 里能看到,UI 上不显示。v0.2.0 之后 sidecar 启动时**主动验证 M3 key**(`M3Client.verify_key`),OpenRouter 走 `/auth/key` 不耗 credits,其他 provider 走 1-token chat。401 / 余额 0 / 网络错误都会在 cmd 启动瞬间打到屏幕,不用等用户开了 UI 看到"修图全失败"才发现 key 配错。后端 `m3_client.chat/stream_chat` 捕获 401 抛 `M3 API key 无效或被拒绝 (401): <err>` 友好提示,前端 chat 把它写到消息流末尾,修图每张照片显示 `photo_failed` + 具体原因
- **后端活动从 UI 拿掉,改到 cmd** — 之前左边栏的「后端活动」tab 接 SSE 拿日志,但 (a) EventSource 频繁 mount/unmount 会导致"刷新一次就不显示";(b) 用户切到 logs tab 一次切走再切回,recentLogs state 丢失,什么也看不到。v0.2.0 砍掉这个 tab,改由 `start.py` 把 sidecar 的 stdout/stderr 全部继承给启动脚本,**所有后端日志实时显示在你运行的 cmd 窗口里**。需要查历史可以 `tail -f workspace/.logs/sidecar.log`
- **"文件传不上去"修复** — `/upload` 端点之前对 `f.size is None` 的流式上传会漏检 payload size,大文件夹上传可能突然 413 但 cmd 看不到原因。v0.2.0 把 pre-check 改成"size 缺失时按 50MB/张估算"兜底,所有失败路径(超大文件 / payload too large / 全部被 reject / 中途写盘失败)都打 `logger.warning`,cmd 里直接看到具体哪一步出问题
- **m3_client 清理** — 删了 `_iter_stream_chat` 里一坨 `continue` 之后永远不会执行的死代码、修了 `import asyncio` 放文件末尾的丑问题、M3 plugin header 改成只在非 OpenRouter 时发(避免被原样转发给上游 provider 触发 4xx)
- **mock 模式筛片修好** — `build_cull_prompt` 第一句"请判断这张照片是否应该保留"不含 cull/screen/废片 等关键字,旧 mock fallback 返回的 dict 没有 `keep` 字段,导致 mock 模式下筛片全留。v0.2.0 加"判断这张照片 / 保留"作为更宽泛的兜底关键字
- **版本号** — `Sidebar` 左下角显示 `v0.2.0`,与 `backend/pyproject.toml` / `frontend/package.json` 同步

## v0.1.2 升级要点(上一版)

- **M3 key 启动时验证** — 见上文 v0.2.0 说明,这一版做了第一版但只发了 warning,UI 仍是裸的。后端 `_unwrap` 拿不到这个信息,所以修图/chat 看着像"网络挂了"
- **M3 401 友好提示** — 同上,后端抛 `RuntimeError("M3 API key 无效或被拒绝 (401): ...")`,前端 chat 把 message 写到消息流末尾
- **详细排查指南** — 见下方「常见问题 → M3 报 401 / User not found」

## v0.1.1 升级要点(更早)

- **上传入口** — 前端加「上传文件 / 上传文件夹」按钮,选完自动落盘到 `workspace/input/<时间>-uploaded/`,只接受 arw/cr2/cr3/nef/dng/jpg/jpeg/png,其他自动过滤 + 计数
- **修图识别修复** — 之前"应用无法识别工作区里的照片"是因为 `iterdir()` 不递归 + 散文件被忽略;现在 `list_input_folders` 返回 `loose_files`,`grade/cull_photos` 递归扫描并跳过系统目录
- **修图/筛片独立页面** — 从聊天框上方移出来,和「智能对话」平级放在 Sidebar(v0.2.0 把「后端活动」tab 拿掉)
- **进度条 bug 修复** — SSE race condition:前端 EventSource 建立前,后端已发的 `task_started` 和早期事件丢失。现在后端每个 task 维护 event buffer,SSE 建立时先 replay;同时 `start_grade/cull` 等前端订阅(最多 3 秒)再开跑
- **刷新持久化** — 聊天记录、任务进度、当前 view 全部 localStorage 持久化,刷新页面不丢;Sidebar 加「清空历史」按钮
- **完整测试** — 新增 23 个上传相关单测,后端 52/52 通过

完整变更(从 v0.1.0 累计):新文件(`upload/`, `views/`, `usePersistedState`, `WorkspacePanel`, `UploadButton`) + 重构(`App.tsx`, `Sidebar.tsx`, `api.ts`) + 修复(`tools.py` `_iter_photos`, `main.py` SSE buffer + pending_start, v0.2.0 删 LogPanel/SSE/前端日志总线、删 m3_client 死代码、修 mock cull prompt 关键字、upload 端点加强日志和大小预检)。

## 30 秒上手

Windows:解压后双击 `启动.bat`,等 1-2 分钟,看到「应用已就绪」后浏览器开 `http://localhost:1420`。

macOS / Linux:
```bash
chmod +x start.sh && ./start.sh
python3 stop.py        # 停
python3 diagnose.py    # 诊断,生成 diagnose.txt
```

启动脚本会自动装 Python venv + Node 依赖、配 pip / pnpm 镜像源、起 sidecar + Vite。**不动 MSVC,不动 Rust 编译**。

## 架构

```
┌──────────────────────────────────────────────────────┐
│              React 前端 (Vite :1420)                 │
│   智能对话 / 一键修图 / 一键筛片                     │
│   (后端日志从 v0.2.0 改到启动 cmd 实时显示)          │
└────────────────────┬─────────────────────────────────┘
                     │ HTTP + SSE
                     ▼
┌──────────────────────────────────────────────────────┐
│           Python Sidecar (FastAPI :8765)            │
│   任务调度 · 图像处理(rawpy / opencv) · M3 调用     │
│   工作区管理(input / output / .tasks / .logs)        │
└────────────────────┬─────────────────────────────────┘
                     │ HTTPS
                     ▼
┌──────────────────────────────────────────────────────┐
│      M3 (MiniMax-M3) via OpenRouter                  │
│   图像质量评分 · 构图分析 · 摄影知识问答             │
└──────────────────────────────────────────────────────┘
```

v0.3.0+ 计划接入 Tauri 2 (Rust) 桌面壳,代码在 `src-tauri/` 已就位(v0.2.0 暂不启用,需要 MSVC Build Tools)。

## 已实现

- **一键修图 (grade)** — 批量调色,自动色温/曝光/对比度
- **一键筛片 (cull)** — 智能去重,质量评分,保留/剔除建议
- **摄影问答 (chat)** — 问「什么是光圈优先」之类,SSE 流式响应
- **后端活动(cmd)** — sidecar 的所有 stdout/stderr 实时显示在启动 cmd 里(替代了 v0.1.x 时的"后端活动" UI 面板)。文件日志同步写到 `workspace/.logs/sidecar.log` 方便 `tail -f`
- **照片反馈** — 用户对结果点赞/踩,后续风格学习用
- **本地工作区** — `workspace/input/` 放照片,`workspace/output/` 存结果
- **M3 离线模式** — 不配 key 自动进 mock,本地开发无障碍

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 18 + Vite 5 + TypeScript + Tailwind 3 + lucide-react |
| 后端 | Python 3.10+ + FastAPI + uvicorn |
| 图像 | rawpy + opencv + Pillow |
| LLM | MiniMax M3 (openai-compatible, 走 OpenRouter) |
| 桌面壳 (预留) | Tauri 2 + Rust + WebView2 |
| 构建 | pnpm 11 + Vite |

## 目录结构

```
photographer-copilot/
├── 启动.bat / 停止.bat / 诊断.bat     # Windows 一键脚本
├── start.sh / start.py / stop.py      # macOS / Linux / 跨平台
├── diagnose.py                        # 诊断脚本
├── .env / .env.example                # M3 配置(env 不进 git)
├── package.json + pnpm-lock.yaml      # 根 workspace
│
├── backend/                           # Python FastAPI sidecar
│   ├── agent/  (main.py / tools.py / m3_client.py / prompts.py)
│   ├── db/     (SQLite catalog)
│   ├── image/  (color_grade.py / raw_processor.py)
│   ├── tests/  (pytest 4 模块)
│   └── requirements.txt + pyproject.toml
│
├── frontend/                          # React 前端
│   ├── src/
│   │   ├── App.tsx + main.tsx
│   │   ├── components/  (7 个组件: Sidebar / ChatBox / WorkspacePanel / UploadButton / TaskProgress / ToolCallBlock / MiniMarkdown)
│   │   ├── views/       (GradeView / CullView)
│   │   ├── lib/api.ts   (HTTP 封装,无前端日志总线)
│   │   └── styles/      (Apple 毛玻璃基础类)
│   └── vite.config.ts + tailwind.config.js
│
├── src-tauri/                         # Tauri 桌面壳 (v0.3.0+ 启用,需要 MSVC Build Tools)
│   ├── src/  (5 个 Rust 文件)
│   ├── capabilities/ + icons/
│   └── tauri.conf.json + Cargo.toml
│
├── docs/ARCHITECTURE.md               # 架构文档
├── examples/sample_jpegs/             # 8 张场景示例图
├── scripts/smoke_test.py
├── knowledge_base/                    # M3 提示词预设(v0.3.0+ 充实)
└── samples/                           # 演示数据
```

## 使用流程

1. 双击 `启动.bat` (或 `./start.sh`) 起应用
2. 浏览器开 `http://localhost:1420`
3. 把照片文件夹丢进 `workspace/input/<随便起名>/`
4. 前端选文件夹 → 点「一键修图」或「一键筛片」
5. 等任务跑完,在 TaskProgress 面板看结果
6. 对结果反馈(赞/踩),数据会进风格学习
7. 聊天问摄影问题,SSE 流式响应

## 配置

`.env`(从 `.env.example` 复制):
```ini
M3_BASE_URL=https://openrouter.ai/api/v1
M3_API_KEY=sk-or-v1-...你的 key...
M3_MODEL=minimax/minimax-m3
LOG_LEVEL=INFO
```

不配 `M3_API_KEY` 会进 mock 模式(返回假数据,适合离线开发)。

## 开发模式

```bash
python start.py              # 完整流程(默认)
python start.py --check      # 只检查环境
python start.py --install    # 只装依赖
python start.py --desktop    # 启用 Tauri 桌面壳(需 MSVC Build Tools)
```

后端日志:**v0.2.0 起直接显示在启动 cmd 里**(`sidecar_proc` 的 stdout/stderr 继承给父进程),同时同步写到 `workspace/.logs/sidecar.log`(可 `tail -f` 查历史)。v0.1.x 时的前端「后端活动」tab 已被移除,见 v0.2.0 升级要点
前端日志:浏览器 DevTools console(`[api] POST /upload ✗ HTTP ...` 这类)

## 常见问题

### 修图全失败 / chat 报"M3 API key 无效或被拒绝 (401)"

**症状**:启动后 chat 第一条消息就报 `错误: M3 API key 无效或被拒绝 (401): ...`,修图每张照片 `photo_failed`,任务最终 `0/N 成功`。

**排查步骤**(从最快到最慢):

1. **看 sidecar 启动日志** — `workspace/.logs/sidecar.log` 开头几行会有 `M3 key 验证失败` 或 `M3 key 验证通过`,看错误信息:
   - `401 Unauthorized — key 无效或被撤销` → key 在 OpenRouter 端被删了,去 https://openrouter.ai/keys 重新生成
   - `401 No cookie auth credentials set` / `User not found` → key 复制错了,前后多了空格 / 多了换行
   - `网络错误: ...` → 电脑没网,或 OpenRouter 临时挂了,看 https://status.openrouter.ai

2. **手动验证 key** — 在终端跑:
   ```bash
   curl -s https://openrouter.ai/api/v1/auth/key \
     -H "Authorization: Bearer $(grep M3_API_KEY .env | cut -d= -f2)" | python3 -m json.tool
   ```
   应该看到 `{"data": {"limit_remaining": 4.99, ...}}`。`User not found` / `401` 就说明 key 废了。

3. **检查 .env** — 注意:
   - `M3_API_KEY` 值不要带引号(写成 `M3_API_KEY="sk-..."` 也会被读成字面值,key 里多了双引号)
   - `M3_BASE_URL` 必须带 `/v1` 结尾(`https://openrouter.ai/api/v1`,不是 `https://openrouter.ai/api`)
   - `M3_MODEL` 必须和 OpenRouter 上真实存在的 model id 一致(当前 v0.2.0 默认 `minimax/minimax-m3`,去 https://openrouter.ai/models 搜 "minimax" 看最新列表)

4. **余额** — `limit_remaining: 0` 时 OpenRouter 会返回 402,不是 401,错误信息会说 "insufficient credits",去 https://openrouter.ai/credits 充值。

5. **还是不行** — 把 `workspace/.logs/sidecar.log` 开头 20 行 + `curl /auth/key` 的输出贴给开发者。

### 修图卡在 "running" 不动

**症状**:点了「一键修图」,进度条一直转,30 秒后还显示 0%。

**排查**:这通常是 SSE / 拉取双重路径里有一个挂了,v0.1.1+ 加了 5 秒一次的兜底轮询,正常情况下 30 秒内会强制收尾。如果一直不动:
- 看启动 cmd 窗口里有没有 `httpx: HTTP Request: POST ... "401"` 或 `timeout` — 网络问题
- 看启动 cmd 窗口里有没有 `Grading xxx.jpg` — 在跑,等
- v0.1.x 时的前端「后端活动」tab 已被 v0.2.0 移除,改看启动 cmd 窗口(或 `tail -f workspace/.logs/sidecar.log`)

### 上传文件失败 / "文件传不上去"

**症状**:点上传按钮后,前端提示错误或一直没反应。

**排查**(v0.2.0 起所有失败都会在 cmd 留痕,这是最快的诊断入口):
1. **看启动 cmd 窗口** — 找 `Upload start` / `Upload done` / `Upload rejected: ...` / `Skip oversized file: ...` / `Upload partial-fail: ...` 这类行,直接看到底是哪一步失败
2. **常见原因**:
   - 选了一堆 .txt / .DS_Store / 视频等被 reject(`Upload partial-fail: ...readme.txt reason: unsupported type: .txt`)。v0.2.0 只接受 arw/cr2/cr3/nef/dng/jpg/jpeg/png
   - 文件超大(单张 > 500 MB 或总 > 2 GB),会被 pre-check 拦下
   - 文件名里有特殊字符 / Windows 反斜杠(被 `_sanitize_rel_path` 当 invalid path 拒绝)
3. **cmd 里没看到 `Upload start` 行** — 说明 multipart 根本没到后端,要么浏览器 CORS 失败要么 sidecar 死了。看 cmd 有没有 `CORS` 相关报错,或 `tail -f workspace/.logs/sidecar.log` 看 sidecar 是不是挂了

### 怎么在启动 cmd 之外看后端日志

v0.2.0 起,启动脚本会把 sidecar 的 stdout 全部继承,所以**最简单**就是看启动 cmd 窗口。但如果你在另一个终端想看历史:

```bash
# Windows (PowerShell)
Get-Content workspace\.logs\sidecar.log -Wait

# macOS / Linux
tail -f workspace/.logs/sidecar.log
```

文件 log 包含所有子模块(`backend.agent.m3_client` / `backend.image.color_grade` / `backend.db.catalog` 等)的 logger 输出,带行号和模块名。

### 启动报 "Sidecar 端口 8765 仍被占用"

**原因**:上一次 start.py 没正常退出,或者手动跑了 `python3 -m backend.agent.main`。

**处理**:
- Windows: `netstat -ano | findstr :8765` → 找到 PID → `taskkill /F /PID <pid>`
- macOS/Linux: `lsof -i :8765` → 找到 PID → `kill -9 <pid>`

或者直接 `python3 stop.py`,脚本会清掉所有 v0.2.x 启动时登记的 PID。

