"""tests/test_layers.py —— **分层与兼容**的回归网（重构后不许悄悄破掉）。

四组断言：

1. **分层方向**：`data` / `mood_soc` 不许 import `store` / `ui` / `api`（依赖只能单向向下）；
   `store` 不许 import `ui` / `api`（`store.layout` 被 `mood_soc/__init__` 引的那一处除外，
   它由 `mood_soc/__init__.py` 自己负责，见那里的注释）；`ui` 与 `api` 互不 import。
2. **兼容转发壳**：老路径（`mood_soc.importer` / `mood_soc.output` / `mood_soc.scenario` /
   `mood_soc.maa` / `ui.schedule`）导出的**每一个**公开名字都能取到，且与新版是**同一个对象**。
   这条是"搬家不破功能"的硬保证：漏一个名字就会静默断链。
3. **程序接口不拉图形界面**：`api` / `store` 的 import 链里不许出现 tkinter。
4. **数据只有一处**：`data/paths.py` 是唯一拼资源路径的地方
   （源码里不许再出现 `"resources"` 字面路径拼接）。

运行：.venv/Scripts/python.exe -m unittest tests.test_layers -v
"""
from __future__ import annotations

import importlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 老路径 → 新路径（兼容转发壳）
SHIMS = {
    "mood_soc.importer": "store.sources",
    "mood_soc.output": "store.serialize",
    "mood_soc.scenario": "store.layout",
    "mood_soc.maa": "store.maa",
    "ui.schedule": "store.schedule",
}

#: 允许"向上"出现的那一条例外（`mood_soc/__init__.py` 为了保持公开 API 而引 store.layout）
ALLOWED = {("mood_soc", "store")}


def module_source(pkg: str) -> str:
    """把一个包/目录下所有 .py 的源码拼起来（用于静态检查 import 方向）。"""
    root = ROOT / pkg
    if root.is_dir():
        return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.py")))
    return (ROOT / (pkg + ".py")).read_text(encoding="utf-8")


class Test分层方向(unittest.TestCase):
    def test_data_不依赖上层(self):
        src = module_source("data")
        for forbidden in ("store", "ui", "api", "mood_soc.rules", "mood_soc.models"):
            self.assertNotIn(f"import {forbidden}", src, f"data/ 不许 import {forbidden}")

    def test_mood_soc_不依赖上层(self):
        src = module_source("mood_soc")
        for line in src.splitlines():
            m = re.match(r"\s*(?:from|import)\s+(store|ui|api)\b", line)
            if m:
                self.assertIn(("mood_soc", m.group(1)), ALLOWED,
                              f"mood_soc 只允许经 __init__ 引 store.layout：{line.strip()}")

    def test_store_不依赖界面与接口(self):
        src = module_source("store")
        for forbidden in ("ui", "api"):
            self.assertNotRegex(src, rf"(?m)^\s*(?:from|import)\s+{forbidden}\b",
                                f"store/ 不许 import {forbidden}")

    def test_ui_与_api_互不依赖(self):
        self.assertNotRegex(module_source("ui"), r"(?m)^\s*(?:from|import)\s+api\b")
        self.assertNotRegex(module_source("api"), r"(?m)^\s*(?:from|import)\s+ui\b")

    def test_程序接口不拉tkinter(self):
        """**代码**里不许 import tkinter（docstring 里提到它没关系）。"""
        for pkg in ("api", "store", "data"):
            src = module_source(pkg)
            self.assertNotRegex(src, r"(?m)^\s*(?:from|import)\s+tkinter\b",
                                f"{pkg}/ 里不许 import tkinter")


class Test兼容转发壳(unittest.TestCase):
    """老 import 路径必须**一个名字都不少**，而且指向同一个对象。"""

    def test_每个老名字都还在(self):
        for old_name, new_name in SHIMS.items():
            old, new = importlib.import_module(old_name), importlib.import_module(new_name)
            # 契约是 `__all__`（老模块有的有、有的没有：没有 `__all__` 的模块
            # `from ... import *` 会把全部公开名带过来，所以这里按 `__all__` 校验）
            for n in getattr(new, "__all__", []):
                self.assertTrue(hasattr(old, n), f"{old_name} 少了 {n}（转发壳漏名字）")

    def test_名字指向同一个对象(self):
        for old_name, new_name in SHIMS.items():
            old, new = importlib.import_module(old_name), importlib.import_module(new_name)
            for n in getattr(new, "__all__", []):
                self.assertIs(getattr(old, n), getattr(new, n),
                              f"{old_name}.{n} 与 {new_name}.{n} 不是同一个对象")

    def test_mood_soc_顶层API不丢(self):
        """`from mood_soc import ...` 的公开符号必须仍然齐全（tests 全靠它）。"""
        import mood_soc
        for name in ("build_base_layout", "evaluate", "evaluate_base", "simulate",
                     "mood_ledger", "apply_entry_events", "apply_idle_to_dorm",
                     "Operator", "Facility", "BaseLayout", "MoodResult", "BaseResult"):
            self.assertTrue(hasattr(mood_soc, name), f"mood_soc.{name} 不见了")

    def test_技能数据只有一份(self):
        """`mood_soc.skills` 的 SKILLS 就是 `data.skills_data.SKILLS`（同一对象，不是拷贝）。"""
        from data.skills_data import SKILLS, OPERATOR_FACTIONS
        from mood_soc.skills import SKILLS as S2, OPERATOR_FACTIONS as F2
        self.assertIs(SKILLS, S2)
        self.assertIs(OPERATOR_FACTIONS, F2)


class Test数据路径只有一处(unittest.TestCase):
    def test_源码里不再手拼resources路径(self):
        offenders = []
        for pkg in ("mood_soc", "store", "ui", "api", "data", "scripts", "tests"):
            root = ROOT / pkg
            files = sorted(root.rglob("*.py")) if root.is_dir() else [root.with_suffix(".py")]
            for p in files:
                text = p.read_text(encoding="utf-8")
                for i, line in enumerate(text.splitlines(), 1):
                    if re.search(r'''["']resources["']\s*/''', line) or "/ \"resources\"" in line:
                        offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
        # `data/paths.py` 是唯一允许拼资源路径的地方（它自己就是那个出口）
        offenders = [o for o in offenders if "data\\paths.py" not in o and "data/paths.py" not in o]
        self.assertEqual(offenders, [], "资源路径请一律经 data/paths.py：\n" + "\n".join(offenders))


class Test数据目录只放表(unittest.TestCase):
    """`data/` **只放要 import 的表**：样例 JSON / 说明文档 / 报告不许放进来。

    这条是给"把 `resources/` 搬进 `data/`"这种想法立的规矩（曾做过一次，已改回）：
    `data/` 是 Python 包，测试发现与打包会整体遍历它；样例与报告是项目级资产，
    落在这里只会让"数据表"这个概念变糊。分工见 `documents/01-架构.md` §1.1。
    """

    #: `data/` 里允许出现的扩展名
    ALLOWED = {".py", ".txt"}
    #: 例外：`data/` 下的子目录一律不许存在（样例/报告都在仓库根 `resources/`）
    def test_data里没有子目录(self):
        subs = [p.name for p in (ROOT / "data").iterdir()
                if p.is_dir() and p.name != "__pycache__"]
        self.assertEqual(subs, [], f"data/ 下不该有子目录：{subs}（样例/报告请放仓库根 resources/）")

    def test_data里只有表和代码(self):
        bad = [p.name for p in (ROOT / "data").iterdir()
               if p.is_file() and p.suffix.lower() not in self.ALLOWED]
        self.assertEqual(bad, [], f"data/ 里不该出现这些文件：{bad}")

    def test_样例与报告在仓库根resources(self):
        from data.paths import (MAA_SAMPLE, OUTPUT_CONTRACT_MD, REQUIREMENTS_DOCX,
                                SKILL_VERIFY_REPORT, V4_SAMPLE)
        for path in (MAA_SAMPLE, V4_SAMPLE, OUTPUT_CONTRACT_MD, SKILL_VERIFY_REPORT,
                     REQUIREMENTS_DOCX):
            self.assertTrue(path.exists(), f"{path} 不存在")
            self.assertEqual(path.parent.name, "resources", f"{path} 应在仓库根 resources/ 下")


if __name__ == "__main__":
    unittest.main()
