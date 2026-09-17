"""mood_soc/maa.py —— **兼容转发**：MAA 排班解析已搬到 `store/maa.py`。

房间映射表与 MAA plan 解析属于数据管理层（`store/`）。
本文件只做转发，历史写法继续可用：

```python
from mood_soc.maa import read_maa, ROOM_MAP     # 仍然可以
from store.maa import read_maa, ROOM_MAP        # 新写法（推荐）
```
"""
from __future__ import annotations

from store.maa import *          # noqa: F401,F403
from store.maa import __all__    # noqa: F401
