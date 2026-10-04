# -*- coding: utf-8 -*-
"""documents/ 文档自检 —— 结构 / 引用 / 索引 / `AGENTS.md` 预算（入库的驻留检查）。

骨架与 `verify_idle.py` / `verify_modules.py` / `verify_settings.py` 一致：
分节 `=== §N … ===`、`check(条件, 说明)` 计成败、`-v` 逐条打印、失败打印**实际值 vs 期望值**、
末尾 `N 条 / M fail`、退出码 0/1。

四节检查的都是"**机器能判、而且真出过事**"的东西：

| 节 | 查什么 | 为什么 |
|---|---|---|
| §1 | 每份 1 个 H1；同一文件里没有**重复的章节号** | 2026-10 合并时 `13`+`16`（旧编号）的 `### 1.`~`### 7.` **撞号**过 |
| §2 | 全仓每条 `documents/xx.md` / 裸 `NN-名称` 都落到**真实存在**的文件 | 改名/合并后最容易留下的就是悬空引用 |
| §3 | `documents/README.md` 的「文档清单」⇄ 实盘**双向**一致 | 索引是入口，漏一份等于那份"不存在" |
| §4 | `AGENTS.md` ≤ 65536 字节（它自己的硬预算）；其文档索引树与实盘一致 | 超预算会被**截断、尾部读不到** |

⚠️ **刻意不查"合并前的标识符是否还在"**：那需要一份快照做基线，属于一次性合并工具，
留在 `_scratch/check_doc_ids.py`，不当驻留检查。

用法：
    .venv\\Scripts\\python.exe scripts\\verify_docs.py          # 全绿 → 退出码 0
    .venv\\Scripts\\python.exe scripts\\verify_docs.py -v       # 打印每一条通过项
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "documents"
AGENTS = ROOT / "AGENTS.md"

#: `AGENTS.md` 的指令预算（见它自己的维护约定第 3 条）
AGENTS_BUDGET = 65536

#: 不参与扫描的目录
SKIP_DIRS = {".git", "_scratch", "__pycache__", "dist", "build", "version", "_tmp",
             ".venv", "node_modules", ".agents"}
SKIP_PARTS = {"__pycache__", "_tmp"}
EXTS = {".md", ".py", ".txt", ".json", ".toml", ".cfg", ".ini", ".ps1", ".bat"}

#: `documents/xx.md` 或裸的 `xx.md`
RE_PATH = re.compile(r"(?:documents/)?(\d{2}-[^\s`)\]，。、|：:（(]+\.md)")
#: 裸文件名（不带 `.md`），如「构建见 06-设计史 §9.6」
RE_BARE = re.compile(r"(?<![\w./-])(\d{2}-[^\s`)\]，。、|：:（(.]+)(?=\s*§|\s|$)")
#: 合并溯源行「本节原为 `documents/xx.md`」——**故意指向已被并入的文件**，不算悬空
RE_PROVENANCE = re.compile(r"原(?:为)?\s*`?documents/\d{2}-")
#: 显式豁免开关：夹在 `<!-- verify-docs: allow-missing -->` 与 `<!-- /verify-docs -->` 之间的行跳过 §2。
#: 用在"故意列出已被并入的文件名"那种**历史/映射表**上（例：`10-现状与校准记录.md` 的「合并映射」表）。
#: 之所以要显式开关而不是靠猜（比如"表格里就放过"），是因为**误放过一次悬空引用就白建了这个检查**。
ALLOW_OPEN = "<!-- verify-docs: allow-missing -->"
ALLOW_CLOSE = "<!-- /verify-docs -->"
#: 章节号（`## 7.` / `### 7.1`）
RE_SECTION = re.compile(r"^#{2,3} (\d+)\. ")

PASS = 0
FAIL = 0
VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv


def check(name: str, ok: bool, detail: str = "") -> None:
    """一条断言：通过就 +1（`-v` 时打印），失败打印原因并计入失败数。"""
    global PASS, FAIL
    if ok:
        PASS += 1
        if VERBOSE:
            print(f"  ok   {name}")
        return
    FAIL += 1
    print(f"  FAIL {name}" + (f"\n       {detail}" if detail else ""))


def is_fence(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("```") or s.startswith("~~~")


def headings(text: str):
    """围栏外的标题 `[(行号, 原文)]`（⚠️ 必须跟踪围栏，否则 bash 注释会被当成标题）。"""
    out, fence = [], False
    for i, line in enumerate(text.splitlines(), 1):
        if is_fence(line):
            fence = not fence
            continue
        if fence:
            continue
        if line.startswith("#"):
            out.append((i, line))
    return out


def iter_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in EXTS:
            continue
        if set(p.parts) & SKIP_DIRS or any(x in p.parts for x in SKIP_PARTS):
            continue
        yield p


def doc_names() -> set:
    return {p.name for p in DOCS.glob("*.md")}


# ============================================================================
# §1 结构
# ============================================================================
def section_structure() -> None:
    print("=== §1 结构（每份 1 个 H1；无重复章节号） ===")
    for p in sorted(DOCS.glob("*.md")):
        hs = headings(p.read_text(encoding="utf-8"))
        h1 = [x for x in hs if x[1].startswith("# ")]
        check(f"{p.name} 恰好 1 个 H1", len(h1) == 1, f"实际 {len(h1)} 个：{[x[1] for x in h1]}")
        nums = [m.group(1) for _, line in hs if (m := RE_SECTION.match(line))]
        dup = sorted({n for n in nums if nums.count(n) > 1}, key=int)
        check(f"{p.name} 无重复章节号", not dup, f"重复：{dup}（同号会让 §N 引用指向两处）")
        check(f"{p.name} 非空", bool(p.read_text(encoding="utf-8").strip()))


# ============================================================================
# §2 悬空引用
# ============================================================================
def dangling_refs() -> None:
    print("=== §2 悬空引用（每条文档引用都落到真实文件） ===")
    exist = doc_names()
    bad = []
    scanned = 0
    for p in iter_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        rel = p.relative_to(ROOT).as_posix()
        allow = False
        for i, line in enumerate(text.splitlines(), 1):
            if ALLOW_OPEN in line:
                allow = True
                continue
            if ALLOW_CLOSE in line:
                allow = False
                continue
            if allow or RE_PROVENANCE.search(line):
                continue          # 显式豁免 / 合并溯源行：故意指向已被并入的文件
            for m in RE_PATH.finditer(line):
                if m.group(1) not in exist:
                    bad.append(f"{rel}:{i} «{m.group(1)}»")
            for m in RE_BARE.finditer(line):
                name = m.group(1)
                if not re.search(r"[\u4e00-\u9fff]", name):
                    continue      # 裸名只认带中文的（避免误报编号/版本号）
                if f"{name}.md" not in exist:
                    bad.append(f"{rel}:{i} «{name}»（裸名）")
    check(f"扫描了 {scanned} 个文件", scanned > 0)
    check("没有悬空引用", not bad,
          "指向了不存在的文档（改名/合并后最容易漏）：\n       " + "\n       ".join(bad[:20]))


# ============================================================================
# §3 索引完备（`documents/README.md` 的文档清单 ⇄ 实盘）
# ============================================================================
def index_completeness() -> None:
    print("=== §3 索引完备（documents/README.md 的「文档清单」⇄ 实盘） ===")
    readme = DOCS / "README.md"
    text = readme.read_text(encoding="utf-8")
    # 只取「## 文档清单」到下一个 `## ` 之间的表格，避免把 §编号约定 等处的文件名也算进来
    m = re.search(r"^## 文档清单.*?(?=^## )", text, re.M | re.S)
    check("README 里有「文档清单」表", m is not None)
    listed = set(re.findall(r"`(\d{2}-[^`]+\.md)`", m.group(0))) if m else set()
    actual = doc_names() - {readme.name}
    missing = sorted(actual - listed)
    extra = sorted(listed - actual)
    check("实盘里的每份文档都在清单里", not missing, f"清单里没有：{missing}")
    check("清单里的每份文档都真实存在", not extra, f"盘上没有：{extra}")
    check(f"清单覆盖 {len(listed)} 份（实盘 {len(actual)} 份）", listed == actual)


# ============================================================================
# §4 `AGENTS.md`（预算 + 索引树与实盘一致）
# ============================================================================
def agents_entry() -> None:
    print("=== §4 AGENTS.md（预算 + 文档索引树） ===")
    raw = AGENTS.read_bytes()
    size = len(raw)
    check(f"AGENTS.md 在 {AGENTS_BUDGET} 字节预算内", size <= AGENTS_BUDGET,
          f"实际 {size} 字节（超 {size - AGENTS_BUDGET}）—— 超了会被截断、尾部读不到")
    check("AGENTS.md 预算用量不超过 95%", size <= AGENTS_BUDGET * 0.95,
          f"实际 {size} 字节（{size / AGENTS_BUDGET:.1%}）—— 快满了，新增内容要往 documents/ 挪")
    text = raw.decode("utf-8")
    exist = doc_names()
    # 索引树：`## 📚 文档索引` 之后的第一个围栏代码块
    m = re.search(r"^## 📚 文档索引.*?```(.*?)```", text, re.M | re.S)
    check("AGENTS.md 里有文档索引树", m is not None)
    if m:
        names = set(re.findall(r"(\d{2}-[^\s│]+\.md)", m.group(1)))
        stale = sorted(n for n in names if n not in exist)
        check("索引树里没有已不存在的文档", not stale, f"索引树仍列着：{stale}")
        check("索引树覆盖全部 10 份门类文档",
              names == exist - {"README.md"},
              f"树里 {len(names)} 份、实盘 {len(exist) - 1} 份；缺 {sorted(exist - {'README.md'} - names)}")


def main() -> int:
    if not DOCS.is_dir():
        print(f"!! 找不到 {DOCS}")
        return 1
    section_structure()
    dangling_refs()
    index_completeness()
    agents_entry()
    print(f"\ndocuments 文档自检：通过 {PASS} 条，失败 {FAIL} 条")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
