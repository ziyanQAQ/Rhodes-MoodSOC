"""scripts/maa_to_scenario.py —— 把 MAA 基建排班 JSON 转成本工具的场景 JSON（命令行）。

解析逻辑在库里（`mood_soc/maa.py`），本脚本只是薄 CLI，负责"一个 plan 写一个文件"。

用法：
    .venv/Scripts/python.exe scripts/maa_to_scenario.py [源文件] [输出目录]

默认读取 `resources/arknights-infra-schedule-maa.json`，输出到 `scenarios/`，
为每个排班（Shift）生成一个 `maa_shift{N}.json`。

MAA 的 rooms 键名 ↔ 本工具设施：

    control      -> 控制中枢        trading     -> 贸易站      meeting  -> 会客室
    manufacture  -> 制造站          power       -> 发电站      hire     -> 办公室
    training     -> 训练室          processing  -> 加工站      dormitory -> 宿舍

> 图形界面（`ui/`）用的是同一个解析模块，不要再在别处复制房间映射表。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:          # 允许直接 `python scripts/maa_to_scenario.py`
    sys.path.insert(0, str(ROOT))

from mood_soc.maa import plans_from_data  # noqa: E402


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "resources/arknights-infra-schedule-maa.json"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "scenarios"

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)
    plans = plans_from_data(data)

    os.makedirs(outdir, exist_ok=True)
    for i, plan in enumerate(plans, start=1):
        out = {
            "facilities": plan.facilities,
            # 附注：仅用于追溯来源，工具会忽略 facilities 之外的字段
            "_source_plan": plan.name,
            "_source_title": data.get("title", ""),
        }
        path = os.path.join(outdir, f"maa_shift{i}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"生成 {path}  <-  {plan.name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
