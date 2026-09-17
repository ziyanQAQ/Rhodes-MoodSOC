"""store —— **数据管理层**：排班模型、整周期心情轨迹、输入解析、结果序列化。

它坐在"纯计算"（`mood_soc` + `data`）与"外部入口"（`ui/` 图形界面、`api/` 程序接口）
之间：**凡是有状态、要读写文件、要回答"这个排班怎么样"的东西都在这里**。

```
data/ + mood_soc/   ← 纯计算：给定一个布局，谁的心情怎么变
       ↓
     store/         ← 本包：状态与 IO（排班 / 轨迹 / 解析 / 序列化 / 会话）
       ↓
  ui/     api/      ← 两个并列入口：tkinter 图形界面 / JSON 程序接口（供 Rust 调用）
```

| 模块 | 职责 | 原位置 |
|---|---|---|
| `schedule.py` | 多班排班模型 + **整周期心情轨迹**（事件驱动精确积分，不依赖 GUI） | `ui/schedule.py` |
| `session.py` | **会话状态**：把界面上的全部可调项收成一个对象，界面与程序接口共用同一套语义 | 新增（原散在 `ui/app.py`） |
| `sources.py` | 排班 / 蓝图 JSON 的**格式自动识别与一键导入**（场景 / MAA / v3 输出 / v4 蓝图） | `mood_soc/importer.py` |
| `layout.py` | 场景 dict → `BaseLayout`（隔离输入格式与内部模型） | `mood_soc/scenario.py` |
| `maa.py` | MAA 排班 JSON → facilities（唯一的房间映射表） | `mood_soc/maa.py` |
| `serialize.py` | 结果 → JSON dict / 写文件（inf → null） | `mood_soc/output.py` |

**依赖方向**：本包可以 import `mood_soc` / `data`；`mood_soc` 与 `data` **不许** import 本包
（`mood_soc/__init__.py` 里对 `store.layout` 的那一处是唯一的例外，见该文件注释）。
`ui/` 与 `api/` 都只经本包与 `mood_soc` 的公开 API 做事，互相之间不 import。
"""
from __future__ import annotations

__all__: list = []
