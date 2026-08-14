# 端到端 Demo 走查

本文档记录一次完整的「摄影师助手」MVP 0.1.0 走查流程,你可以照着这份流程在本地复现。

---

## 0. 准备工作

把 `examples/sample_jpegs/` 整个文件夹复制到 `workspace/input/demo-photos/`。

或者在 UI 里直接拖拽也行。

---

## 1. 启动应用

```bash
cd photographer-copilot
pnpm tauri:dev
```

预期:
- Tauri 窗口弹出,标题"摄影师助手",1200×800,深色主题
- 左侧 Sidebar 显示 Sidecar 状态(绿色圆点 + "运行中")
- 中部主区是聊天框,顶部是快捷按钮
- 几秒后(等 Python sidecar 启动)系统消息"Sidecar 已就绪"出现

> 📷 截图位置:`docs/screenshots/01-startup.png`

---

## 2. 选文件夹

顶部下拉框里出现 `demo-photos`,选中它。

> 📷 截图位置:`docs/screenshots/02-select-folder.png`

---

## 3. 一键筛片(30 秒)

点「✂️ 一键筛片」按钮。

预期:
- 任务进度条开始走,显示"当前 1/8 - demo-photos/forest.jpg (analyzing)"
- 聊天框里出现 "开始筛片任务 47885a69 (demo-photos)"
- 每张照片处理完,任务进度面板更新"✅ 保留 / ❌ 剔除 / 失败"计数
- 大约 30 秒后,出现 toast "筛片完成:共 8 张,保留 5 张,剔除 3 张,失败 0 张。输出目录:20250812-143022-out"

> 📷 截图位置:`docs/screenshots/03-culling.png`
> 📷 截图位置:`docs/screenshots/04-cull-done.png`

**检查产出**:
```bash
ls workspace/output/20250812-143022-out/
# 应该看到 5 个 jpg(被保留的)
ls workspace/output/20250812-143022-out/../../
# 还要确认 input 里的 8 张图原封不动
```

---

## 4. 一键修图(60 秒)

在输入文件夹仍选中的情况下,点「🎨 一键修图」。

预期:
- 任务进度条走到 "grading" 阶段
- 每张图处理完后,任务进度下方列出最近 10 张已修图,鼠标悬停可见 👍/👎 按钮
- 完成后 toast "修图完成:共 8 张,成功 8 张,失败 0 张。输出目录:..."

> 📷 截图位置:`docs/screenshots/05-grading.png`

**检查产出**:
```bash
ls workspace/output/<时间>-out/
# 8 个 jpg + 8 个 xmp + 8 个 json sidecar
# 每个 jpg 旁边有同名 .xmp(Lightroom 可读)和 .json(我们自己的)
```

---

## 5. 反馈数据(为风格学习埋伏笔)

在任务进度面板,把鼠标移到任意一张已修图上,点 👍 或 👎。

预期:
- 立即有 toast "已记录 👍" / "已记录 👎"
- 数据库里 `catalog.sqlite` 的对应行 feedback 字段被写入

**验证**:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('workspace/catalog.sqlite')
for row in conn.execute('SELECT id, source_path, feedback FROM photos WHERE feedback IS NOT NULL'):
    print(row)
"
```

---

## 6. 摄影知识问答

在聊天框输入:「什么是光圈优先?」

预期:
- 流式输出,逐字显示
- 大约 200 字以内,给出可操作的解释

> 📷 截图位置:`docs/screenshots/06-chat.png`

再问几个不同的:
- 「逆光人像怎么补救?」
- 「Sony A7M4 拍 4K 选什么 profile?」

---

## 7. 托盘行为

关闭窗口(点右上角 ×):**应用不退出**,只是窗口隐藏。

预期:
- 窗口消失,但托盘图标还在
- 鼠标左键点托盘 → 窗口重新出现
- 鼠标右键点托盘 → 菜单(显示主窗口 / 暂停监听 / 退出)

> 📷 截图位置:`docs/screenshots/07-tray.png`

---

## 常见问题

**Q: 任务卡在某张照片不动了?**
A: 看 `workspace/.logs/sidecar.log`,通常是 M3 调用超时或限流,任务会在 3 次重试后跳过这张继续。

**Q: 修出来的图效果不好?**
A: 1) 确认 M3_API_KEY 配了(mock 模式的图是随机的);2) 单图预览比整批更稳定,先单张调;3) M3 输出受 prompt 影响,可以在 `sidecar/agent/prompts.py` 里微调。

**Q: 能在 Lightroom 里继续编辑吗?**
A: 可以。output 里每个 jpg 旁边有同名 .xmp,Lightroom 导入时会自动读取调色参数。
