"""ui —— Rhodes-MoodSOC 的图形化工具（纯标准库 tkinter，无第三方依赖）。

```
python -m ui            # 启动图形界面
```

分层（与 `mood_soc` 一样保持单向向下、GUI 只做展示与交互）：

| 模块 | 职责 | 依赖 GUI？ |
|---|---|---|
| `ui/schedule.py` | 多班排班模型 + **整周期心情轨迹**（事件驱动精确积分） | ❌ 纯计算，可被测试直接调用 |
| `ui/theme.py` | 配色 / 字体 / 间距令牌（"简洁明了"集中在一处改） | ❌ |
| `ui/board.py` | 基建布局看板：房间卡片 + 位置上干员名与实时心情 | ✅ |
| `ui/chart.py` | 心情曲线图（Canvas 手绘，含坐标轴/网格/班次分界/悬停读数） | ✅ |
| `ui/dialogs.py` | 选人、设心情、班次时长设置等对话框 | ✅ |
| `ui/app.py` | 主窗口：工具栏 + 看板 + 曲线 + 时间滑块 + 状态栏 | ✅ |

设计约定：**所有业务计算都在 `ui/schedule.py`**，`app.py` 只负责把状态画出来，
因此引擎可以用黑盒测试覆盖（`tests/test_ui_schedule_blackbox.py`），不依赖显示器。
"""
from __future__ import annotations

__all__ = ["schedule"]
