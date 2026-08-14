# 工作区目录

这是用户照片和数据进出的运行时目录。**不需要手动建子目录** —— 启动 sidecar (`python start.py`) 会自动创建:

| 子目录 | 用途 |
|---|---|
| `input/` | 放待处理照片(可手动复制文件夹进去,或用前端「上传」按钮) |
| `output/` | 处理完的调色/筛片结果(每任务一个 `<时间>-out` 文件夹) |
| `.tasks/` | 任务运行时数据(SQLite 目录、preview 缓存) |
| `.logs/` | sidecar 日志 |
| `.sidecar-port` | 当前 sidecar 监听端口(Tauri 壳用) |
| `catalog.sqlite` | 任务/照片索引数据库 |

子目录在第一次写入时自动创建,无需预建。
