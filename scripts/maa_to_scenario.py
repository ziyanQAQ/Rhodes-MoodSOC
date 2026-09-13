"""scripts/maa_to_scenario.py —— 把 MAA 基建排班 JSON 转成本工具的测试场景 JSON。

MAA 排班文件的 rooms 键名与本工具的 FacilityType 对应关系：
    control      -> 控制中枢 (control_center)
    manufacture  -> 制造站   (manufacturing)
    trading      -> 贸易站   (trading)
    power        -> 发电站   (power)
    meeting      -> 会客室   (reception)
    hire         -> 办公室   (office)
    processing   -> 加工站   (workshop)
    dormitory    -> 宿舍     (dormitory)

用法：
    .venv/Scripts/python.exe scripts/maa_to_scenario.py [源文件] [输出目录]

默认读取 resources/arknights-infra-schedule-maa.json，输出到 scenarios/，
为每个排班（Shift）生成一个 maa_shift{N}.json。
"""
from __future__ import annotations

import json
import os
import sys

# MAA room 键 -> (本工具的设施中文名, 默认等级)
ROOM_MAP = {
    "control": ("控制中枢", 1),
    "manufacture": ("制造站", 3),
    "trading": ("贸易站", 3),
    "power": ("发电站", 3),
    "meeting": ("会客室", 2),
    "hire": ("办公室", 3),
    "processing": ("加工站", 3),
    "dormitory": ("宿舍", 5),
}

# 输出顺序（保证生成的 JSON 可读、稳定）
ROOM_ORDER = [
    "control", "manufacture", "trading", "power",
    "meeting", "hire", "processing", "dormitory",
]


def convert_plan(plan: dict) -> list:
    """把单个排班计划转换成 facilities 列表。"""
    rooms = plan.get("rooms", {})
    facilities = []
    for key in ROOM_ORDER:
        if key not in rooms:
            continue
        label, level = ROOM_MAP[key]
        for room in rooms[key]:
            # skip 的房间 / 无干员的房间不纳入场景
            if room.get("skip") or not room.get("operators"):
                continue
            facilities.append({
                "type": label,
                "level": level,
                "operators": list(room["operators"]),
            })
    return facilities


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "resources/arknights-infra-schedule-maa.json"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "scenarios"

    with open(src, "r", encoding="utf-8") as f:
        data = json.load(f)

    os.makedirs(outdir, exist_ok=True)
    for i, plan in enumerate(data.get("plans", []), start=1):
        out = {
            "facilities": convert_plan(plan),
            # 附注：仅用于追溯来源，工具会忽略 facilities 之外的字段
            "_source_plan": plan.get("name", ""),
            "_source_title": data.get("title", ""),
        }
        path = os.path.join(outdir, f"maa_shift{i}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"生成 {path}  <-  {plan.get('name', '')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
