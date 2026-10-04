"""scripts/generate_operator_names.py —— 从上游生成「别名 → 中文名」表（`data/operator_names.py`）。

## 为什么需要它

别的工具（例如 ArknightsInfraCalc-v3 的 `plan_compute` 家族）在 JSON 里写的干员名可能是
**英文名或干员 id**：

```json
{"operbox": [{"id": "op_001", "name": "Amiya", "elite": 2, "level": 90, ...}]}
```

而本项目的技能库（`data/operators.txt` / `data/skills_data.py`）**一律按中文名索引**
（上游 `character_table.json` 的 `name` 字段）。不建立这层映射，导入进来的人就成了
"不在技能库里的干员"——基础心情还算得出来，**技能全丢**。

上游 `character_table.json` 里每个干员同时有：

| 字段 | 例子 | 说明 |
|---|---|---|
| `name` | `阿米娅` | 中文名（本项目用的主键） |
| `appellation` | `Amiya` | 英文名（别的工具常用） |
| 键 `char_xxx` | `char_002_amiya` | 干员 id（有些工具会直接写它） |

## 产物

`data/operator_names.py`（**生成物，勿手改**）：一个 `OPERATOR_ALIASES` 字典。
历史版本写的是 CSV（`operator_names.txt`），改成 .py 是为了和 `skills_data.py` 一致
——数据只有一种形态，导入方不需要自己解析文本、也不需要处理"文件不存在"。

`store/sources.py` 在导入时用 `resolve_name()` 查这张表：先看是不是本来就是中文名
（在本项目技能库里），不是再查别名表；都查不到就**原样保留**并写进导入报告的
"不在技能库"清单（按无技能干员参与计算）。

## 用法

    .venv/Scripts/python.exe scripts/generate_operator_names.py --agd <ArknightsGameData>

上游仓库怎么拿见 `documents/05-数据来源.md`（不要整仓 clone）。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# 路径一律走 `data/paths.py`（数据的唯一路径出口）。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from data.paths import (  # noqa: E402
    OPERATOR_NAMES_DATA as OUT,
    OPERATORS_TXT as OPERATORS,
)

HEADER = ["alias", "chinese", "source"]


def load_upstream_names(agd: Path):
    """上游 character_table.json → `[(alias, chinese, source), ...]`。"""
    path = agd / "zh_CN" / "gamedata" / "excel" / "character_table.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for cid, c in data.items():
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name:
            continue
        app = (c.get("appellation") or "").strip().strip('"')
        if app and app != name:
            rows.append((app, name, "appellation"))
        if cid.startswith("char_"):
            rows.append((cid, name, "char_id"))
    return rows


def known_chinese_names():
    """本项目技能库里的中文名（`data/operators.txt` 的第 2 列）。"""
    if not OPERATORS.exists():
        return set()
    out = set()
    with open(OPERATORS, encoding="utf-8", newline="") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2 and row[1]:
                out.add(row[1].strip())
    return out


def render(dedup) -> str:
    """把 `[(alias, chinese), ...]` 渲染成 `data/operator_names.py` 的全文。"""
    lines = [
        '"""data/operator_names.py —— 别名 → 中文名（**生成物，勿手改**）。',
        "",
        "由 `scripts/generate_operator_names.py` 从上游 `character_table.json` 生成：",
        "别的工具可能写英文名或干员 id（`Amiya` / `char_002_amiya`），导入时要用这张表译成中文名。",
        "",
        "```bash",
        ".venv/Scripts/python.exe scripts/generate_operator_names.py --agd <ArknightsGameData>",
        "```",
        "",
        "消费方：`store/sources.py`（导入层的 `alias_table()` / `resolve_name()`）。",
        '"""',
        "from __future__ import annotations",
        "",
        "# 别名（英文名 / appellation / char_id）→ 本项目技能库里的中文名",
        "OPERATOR_ALIASES = {",
    ]
    for a, n in dedup:
        lines.append(f"    {a!r}: {n!r},")
    lines += [
        "}",
        "",
        "# 兼容别名：旧代码/文档里写作 ALIASES 的地方继续可用。",
        "ALIASES = OPERATOR_ALIASES",
        "",
        '__all__ = ["OPERATOR_ALIASES", "ALIASES"]',
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 data/operator_names.py（别名→中文名）")
    ap.add_argument("--agd", required=True, help="上游仓库路径")
    ap.add_argument("--check", action="store_true",
                    help="只校验（不写文件）：重算结果与现有文件是否一致")
    args = ap.parse_args(argv)

    agd = Path(args.agd)
    rows = load_upstream_names(agd) if (agd / "zh_CN").exists() else []
    if not rows:
        print(f"[ERR] 在 {agd} 下没找到 zh_CN/gamedata/excel/character_table.json")
        return 1
    # 只留"别名能落到本项目技能库里的中文名"的条目：别让没技能库的人混进来
    known = known_chinese_names()
    kept = [(a, n, s) for (a, n, s) in rows if n in known]
    # 别名冲突（同一个别名指向**不同**中文名）才值得报；上游 `trap_*` 与 `char_*` 常是
    # 同一个人的两份记录（中文名相同），那不算冲突。
    seen: dict = {}
    dedup, conflicts = [], []
    for a, n, s in kept:
        if a in seen:
            if seen[a] != n:
                conflicts.append((a, seen[a], n))
            continue
        seen[a] = n
        dedup.append((a, n))
    dedup.sort(key=lambda r: (r[1], r[0]))

    text = render(dedup)
    if args.check:
        old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if old != text:
            print(f"[ERR] {OUT} 与重新生成的结果不一致（手改过生成物？）")
            return 1
        print(f"[OK] {OUT} 与重新生成一致（{len(dedup)} 条）")
        return 0

    OUT.write_text(text, encoding="utf-8")
    print(f"[OK] 生成 {OUT}")
    print(f"     上游条目 {len(rows)}，落到技能库 {len(kept)}，去重后 {len(dedup)} 条")
    if conflicts:
        print(f"     ⚠ 别名冲突 {len(conflicts)} 条（保留首条）：{conflicts[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
