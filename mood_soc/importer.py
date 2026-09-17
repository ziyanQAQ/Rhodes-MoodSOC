"""mood_soc/importer.py —— **兼容转发**：导入层已搬到 `store/sources.py`。

排班 / 蓝图 JSON 的格式自动识别与转换属于数据管理层（`store/`）。
本文件只做转发，历史写法继续可用：

```python
from mood_soc.importer import detect_format, import_file    # 仍然可以
from store.sources import detect_format, import_file        # 新写法（推荐）
```
"""
from __future__ import annotations

from store.sources import *          # noqa: F401,F403
from store.sources import __all__    # noqa: F401
