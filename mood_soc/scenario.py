"""mood_soc/scenario.py —— **兼容转发**：输入解析层已搬到 `store/layout.py`。

`dict / JSON → BaseLayout` 属于数据管理层（`store/`），不是心情计算（`mood_soc/`）。
本文件只做转发，历史写法继续可用：

```python
from mood_soc.scenario import build_base_layout, DEFAULT_OPERATOR_LEVEL   # 仍然可以
from store.layout import build_base_layout                                # 新写法（推荐）
```
"""
from __future__ import annotations

from store.layout import *                        # noqa: F401,F403
from store.layout import DEFAULT_OPERATOR_LEVEL   # noqa: F401
