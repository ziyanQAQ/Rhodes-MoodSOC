"""scripts/generate_factions.py —— 从上游生成「干员 ↔ 阵营/标签」表。

## 为什么需要它

`mood_soc/skills.py` 里原先有一张**手工维护**的 `OPERATOR_FACTIONS`。实测发现它是错的：

| 项目手工表 | 上游权威（`gamedata_const.json → termDescriptionDict`） |
|---|---|
| 「萨尔贡」= 埃拉托、帕拉斯、铸铁、断罪者、火神、摆渡人 | 这 6 人是 **`cc.g.minos` 米诺斯**；真正的萨尔贡是泡泡、燧石、蜜蜡、异客、狮蝎… 共 22 人 |
| 岁 只有 令/夕/重岳（写在 `TRAITS`） | `cc.g.sui` 岁 = 年、夕、令、重岳、黍、余、望（7 人）|
| 覆盖 9 个阵营、0 个标签 | 上游定义 21 个阵营 + 7 个标签 = **28 个** |

影响：摆渡人「英雄的骄傲」判断的是错的人；令「杯莫停」只能消除 7 名岁干员中的 3 名。

## 产物

1. `resources/factions.txt` —— 生成表（`faction,member,source,kind`）
   - `source`：上游术语键（`cc.g.sui`）或 `manual`
   - `kind`：`faction` / `tag` / `supplement`
2. `resources/factions_supplement.txt` —— **人工补充**（上游只写"包含所有异格干员"而不列名单的，
   如 `cc.g.sp` 异格）。生成时与上游表合并，上游优先。

`scripts/generate_skills_data.py` 读这两份文件 → 生成 `skills_data.OPERATOR_FACTIONS` /
`FACTION_MEMBERS`，并以阵营反推 `TRAITS`。**不再手工维护阵营表。**

## 用法

    .venv/Scripts/python.exe scripts/generate_factions.py --agd <ArknightsGameData>
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "resources"
OUT = RES / "factions.txt"
SUPPLEMENT = RES / "factions_supplement.txt"

SUPPLEMENT_HEADER = ["faction", "member", "source", "kind", "note"]
OUT_HEADER = ["faction", "member", "source", "kind"]

STRIP_TAGS = re.compile(r"</?(?:@cc\.\w+|\$cc\.\w+)>")
strip_tags = lambda s: STRIP_TAGS.sub("", s or "")

# 上游术语表里「不给成员名单」的阵营（描述只写"包含所有XX干员"），必须人工补充。
NO_MEMBER_LIST = {"cc.g.sp"}   # 异格：「包含所有异格干员」

# 上游 name → 本项目沿用的名字（若有出入）。
# 目前无需改名：`FACTION_COUNT_SKILLS` 里早露「学生会会长」已用上游名「乌萨斯学生自治团」。
RENAME = {}


def parse_members(desc: str) -> list[str]:
    """把术语描述的成员名单解析成列表。

    形如：
        包含以下干员
        陈、星熊、诗怀雅
        包含以下干员
        薪火、岁
    即：跳过「包含…」引导行，其余行按「、」切分。
    """
    lines = [l.strip() for l in strip_tags(desc).splitlines() if l.strip()]
    members: list[str] = []
    for line in lines:
        if line.startswith("包含") or line.startswith("由以下"):
            continue
        members.extend(x.strip() for x in line.split("、") if x.strip())
    return members


def build(agd: Path) -> list[list[str]]:
    gc = json.loads((agd / "zh_CN/gamedata/excel/gamedata_const.json").read_text(encoding="utf-8"))
    td = gc["termDescriptionDict"]

    rows: list[list[str]] = []
    for key, entry in sorted(td.items()):
        m = re.match(r"cc\.(g|tag)\.", key)
        if not m:
            continue
        name = RENAME.get(entry.get("termName"), entry.get("termName"))
        kind = "faction" if m.group(1) == "g" else "tag"
        members = parse_members(entry.get("description", ""))
        if key in NO_MEMBER_LIST or not members:
            continue                      # 交给 supplement
        for member in members:
            rows.append([name, member, key, kind])

    # 合并人工补充（上游没有名单的）
    if SUPPLEMENT.exists():
        with SUPPLEMENT.open(encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("member"):
                    rows.append([r["faction"], r["member"],
                                 r.get("source") or "manual", "supplement"])
    return rows


def main() -> int:
    if "--agd" not in sys.argv:
        print("用法：generate_factions.py --agd <ArknightsGameData>")
        return 2
    agd = Path(sys.argv[sys.argv.index("--agd") + 1])
    rows = build(agd)

    # 去重（上游优先：source != manual 的排在前）
    seen, dedup = set(), []
    for r in sorted(rows, key=lambda r: (r[2] == "manual",)):
        key = (r[0], r[1])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(r)

    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(OUT_HEADER)
        w.writerows(dedup)

    by_faction = defaultdict(list)
    for fac, member, src, kind in dedup:
        by_faction[fac].append(member)

    print(f"[OK] 写出 {OUT.relative_to(ROOT)}：{len(dedup)} 条（{len(by_faction)} 个阵营/标签）")
    for fac, members in sorted(by_faction.items(), key=lambda x: -len(x[1])):
        print(f"     {fac:16s} {len(members):3d} 人  {'、'.join(members[:6])}{'…' if len(members) > 6 else ''}")
    missing = len(NO_MEMBER_LIST) and [k for k in NO_MEMBER_LIST]
    if missing:
        print(f"\n⚠️  上游未列名单、依赖 {SUPPLEMENT.name} 补充的术语：{missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
