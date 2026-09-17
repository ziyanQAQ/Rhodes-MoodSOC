"""mood_soc/scenario.py —— **兼容转发**：输入解析层已搬到 `store/layout.py`。

`dict / JSON → BaseLayout` 属于数据管理层（`store/`），不是心情计算（`mood_soc/`）。
本文件只做转发，历史写法继续可用：

```python
from mood_soc.scenario import build_base_layout, DEFAULT_OPERATOR_LEVEL   # 仍然可以
from store.layout import build_base_layout                                # 新写法（推荐）
```

⚠️ **import 顺序有讲究**（别改成"先 store 后 data"）：
`DEFAULT_OPERATOR_LEVEL` 现在住在 `data/domain.py`（不与任何包成环），而
`build_base_layout` 要从 `store.layout` 取 —— 后者会 `import mood_soc.battery`，
从而**先跑完 `mood_soc/__init__.py`**，而那里又会 import 本模块。
先拿 `data.*` 的名字再拉 `store.*`，这条链就不会"转回来时还没有这个名字"。
"""
from __future__ import annotations

# ① 先取数据包里的领域基元（`data` 只依赖标准库，永远拿得到）
from data.domain import DEFAULT_ELITE, DEFAULT_OPERATOR_LEVEL   # noqa: F401

# ② 再转发 store 里的解析函数（会顺带触发 mood_soc 包的初始化）
from store.layout import *                                       # noqa: F401,F403
