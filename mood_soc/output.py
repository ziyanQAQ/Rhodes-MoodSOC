"""mood_soc/output.py —— **兼容转发**：结果序列化已搬到 `store/serialize.py`。

结果 → JSON dict / 写文件属于数据管理层（`store/`）。
本文件只做转发，历史写法继续可用：

```python
from mood_soc.output import mood_result_to_dict, dump_json    # 仍然可以
from store.serialize import mood_result_to_dict, dump_json    # 新写法（推荐）
```
"""
from __future__ import annotations

from store.serialize import *          # noqa: F401,F403
