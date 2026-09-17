"""ui/schedule.py —— **兼容转发**：排班引擎已搬到 `store/schedule.py`。

`store/schedule.py` 是"这个排班能不能永动"的真计算引擎（**不依赖 tkinter**），
GUI（`ui/`）与程序接口（`api/`，供 Rust 调用）共用同一份，因此它属于数据/状态层而不是图形层。

本文件只做 `from store.schedule import *`：历史写法继续可用

```python
from ui.schedule import Schedule, simulate_schedule, load_schedule   # 仍然可以
from store.schedule import Schedule                                  # 新写法（推荐）
```

⚠️ 不要在这里加任何逻辑 —— 它是迁移期的空壳，`documents/07-设计史.md` 记了删除条件。
"""
from __future__ import annotations

from store.schedule import *          # noqa: F401,F403
from store.schedule import __all__    # noqa: F401
