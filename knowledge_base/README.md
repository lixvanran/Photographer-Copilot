# 知识库 (knowledge_base)

> M3 提示词、摄影预设、风格模板等。运行时由 backend/agent/ 加载。

## 用途

- **prompts/** — M3 的 system prompt / few-shot 例子(v0.2.0+)
- **presets/** — 调色预设(胶片、复古、高饱和等)
- **styles/** — 风格模板(人像 / 风光 / 街拍)
- **faq/** — 摄影知识问答种子数据

## 当前状态

MVP 0.1.0 阶段,内容直接在 `backend/agent/prompts.py` 里 hardcode。
后期重构到本目录,实现"非技术人员可编辑"。

## 规划 (v0.2.0+)

```
knowledge_base/
├── prompts/
│   ├── cull.md          # 筛片 system prompt
│   ├── grade.md         # 调色 system prompt
│   └── chat.md          # 摄影问答 system prompt
├── presets/
│   ├── film_emulation.json
│   ├── portrait.json
│   └── landscape.json
├── styles/
│   └── ...
└── faq/
    └── seed_qa.json
```

## 设计原则

1. **纯文本优先** — `.md` / `.json`,不引入新格式
2. **可被 Python 直接 load** — `json.load(open(...))`
3. **可热更新** — 后端 watch 目录,文件改了立即生效(v0.3.0+)
