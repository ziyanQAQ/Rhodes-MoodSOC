"""图形界面入口：`python -m ui`。

**也支持直接运行本文件**（`python ui/__main__.py`，或 IDE 里的 Run/Debug）：

直接运行单个文件时 Python **不会**把 `ui/` 当成包（`__package__` 为空），
相对导入 `from .app import main` 会报
`ImportError: attempted relative import with no known parent package`。
所以这里先把仓库根目录放进 `sys.path`，再用**绝对导入** —— 两种跑法都成立。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 仓库根目录（本文件在 <root>/ui/ 下）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.app import main  # noqa: E402  （必须在 sys.path 就绪之后导入）

if __name__ == "__main__":
    raise SystemExit(main())
