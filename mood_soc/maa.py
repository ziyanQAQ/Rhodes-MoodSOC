"""mood_soc/maa.py —— MAA 基建排班 JSON 的解析（输入解析层，与 `scenario.py` 同类）。

[MAA（MaaAssistantArknights）](https://github.com/MaaAssistantArknights/MaaAssistantArknights)
的「基建换班」排班文件格式（`arknights-infra-schedule-maa.json`）：

```json
{
  "title": "可露希尔基建终端 · 333",
  "planTimes": "3班",
  "plans": [
    {"name": "Shift 1 · 12h",
     "rooms": {"manufacture": [{"skip": false, "sort": true, "operators": ["森蚺","温蒂","清流"]}, ...],
               "trading": [...], "control": [...], "dormitory": [...], ...},
     "drones": {...}, "Fiammetta": {...}, "description": ""}
  ]
}
```

一个文件可以含**多个 plan**（＝多个班次）；班次时长通常写在 `plans[].name` 里
（如 `Shift 1 · 12h`），`planTimes` 只是个"3班"之类的摘要文本，**不含时长**。

本模块只做**解析**（MAA 结构 → 本工具的 facilities 结构），不做任何心情计算，
供两处共用：

- `scripts/maa_to_scenario.py`（命令行：排班 JSON → 场景 JSON）
- `ui/schedule.py`（图形界面：排班 JSON → 多班周期）

⚠️ 映射关系（MAA 的 rooms 键名 → 本工具的 `FacilityType`）只有这一份，
不要再在别处复制一张表。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

# MAA room 键 -> (本工具的设施中文名, 默认等级)
#
# ⚠️ MAA 的排班文件**不带房间等级**（它只记"把谁放进哪个房间"），所以这里给的只是**兜底默认值**。
# 真正的等级按"实际放了几个人"反推最低可行等级（`min_level_for_slots`），再与默认值取大：
#   制造站放 3 人 → Lv3；控制中枢放 5 人 → Lv5（默认是 1，不推断就会出现"Lv1 容量 1 却站 5 人"）；
#   宿舍保持默认 Lv5 —— 宿舍等级决定**基础回复**（`dormitory_recovery`），不能往下压。
# 导入后可以在界面里逐间改（批量设置的「房间等级」区 / 点看板房间卡头）。
ROOM_MAP: Dict[str, tuple] = {
    "control": ("控制中枢", 1),
    "manufacture": ("制造站", 3),
    "trading": ("贸易站", 3),
    "power": ("发电站", 3),
    "meeting": ("会客室", 2),
    "hire": ("办公室", 3),
    "training": ("训练室", 3),
    "processing": ("加工站", 3),
    "dormitory": ("宿舍", 5),
}

# 输出顺序（保证生成的场景 JSON 可读、稳定；也决定图形界面的房间排列顺序）
ROOM_ORDER: List[str] = [
    "control", "manufacture", "trading", "power",
    "meeting", "hire", "training", "processing", "dormitory",
]

# 中文设施名 -> MAA room 键（反向查表）
ROOM_KEY_BY_LABEL: Dict[str, str] = {label: key for key, (label, _lv) in ROOM_MAP.items()}

# 班次名里的时长：`Shift 1 · 12h` / `12小时` / `6 h`
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|H|小时|時)")


@dataclass
class MaaPlan:
    """MAA 的一个排班（＝一个班次）。

    - `name`：原始排班名（如 `Shift 1 · 12h`），也用作界面上的班次标签
    - `hours_hint`：从 `name` 里解析出的时长（小时）；解析不到为 None
    - `facilities`：本工具的场景结构 `[{type, level, name, operators, deputies, ...}]`，
      可直接喂给 `scenario.build_base_layout`
    - `extra`：`drones` / `Fiammetta` / `description` 等原始字段（本工具不建模，留档备查）
    """
    name: str = ""
    hours_hint: Optional[Decimal] = None
    facilities: List[dict] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def operators(self) -> List[str]:
        """本班次出现的全部干员名（按房间顺序，去重保序）。"""
        seen, out = set(), []
        for f in self.facilities:
            for name in f.get("operators", ()):
                if name not in seen:
                    seen.add(name)
                    out.append(name)
        return out


def parse_duration_hint(name: str) -> Optional[Decimal]:
    """从排班名里解析时长（小时）。解析不到返回 None。"""
    m = _DURATION_RE.search(name or "")
    return Decimal(m.group(1)) if m else None


def convert_plan(plan: dict) -> List[dict]:
    """把单个排班计划转换成 facilities 列表（skip / 空房间不纳入）。

    等级取 `max(该类型的默认等级, 按实际人数反推的最低等级)` —— MAA 文件不带等级，
    这样才能保证"放得下"（详见 `ROOM_MAP` 上方注释与 `min_level_for_slots`）。
    """
    from .config import min_level_for_slots, parse_facility_type

    rooms = plan.get("rooms", {}) or {}
    facilities: List[dict] = []
    for key in ROOM_ORDER:
        if key not in rooms:
            continue
        label, level = ROOM_MAP[key]
        ftype = parse_facility_type(label)
        for idx, room in enumerate(rooms[key], start=1):
            if not isinstance(room, dict):
                continue
            if room.get("skip") or not room.get("operators"):
                continue
            ops = [str(o) for o in room["operators"] if o]
            lv = max(int(level), min_level_for_slots(ftype, len(ops)))
            facilities.append({
                "type": label,
                "level": lv,
                # 同名多房间在界面上要能区分，故带序号（如"制造站#2"）
                "name": f"{label}#{idx}" if len(rooms[key]) > 1 else label,
                "operators": ops,
            })
    return facilities


def plan_from_dict(plan: dict) -> MaaPlan:
    """单个排班 dict → `MaaPlan`。"""
    name = str(plan.get("name", "") or "")
    extra = {k: v for k, v in plan.items() if k in ("drones", "Fiammetta", "description")}
    return MaaPlan(name=name, hours_hint=parse_duration_hint(name),
                   facilities=convert_plan(plan), extra=extra)


def plans_from_data(data: Union[dict, str, Path]) -> List[MaaPlan]:
    """MAA 排班数据（dict，或 JSON 文件路径）→ `[MaaPlan, ...]`。

    一个文件里的**每个 plan 都是一个班次**（时长取 `plans[].name` 里的提示值）。
    """
    if isinstance(data, (str, Path)):
        with open(data, "r", encoding="utf-8") as f:
            data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("MAA 排班文件顶层应是一个 JSON 对象")
    plans = data.get("plans")
    if not isinstance(plans, list) or not plans:
        raise ValueError("MAA 排班文件里没有 plans（期望非空数组）")
    return [plan_from_dict(p) for p in plans]


def read_maa(path: Union[str, Path]) -> List[MaaPlan]:
    """读取一个 MAA 排班 JSON 文件 → `[MaaPlan, ...]`。"""
    return plans_from_data(path)


__all__ = [
    "ROOM_MAP", "ROOM_ORDER", "ROOM_KEY_BY_LABEL", "MaaPlan",
    "parse_duration_hint", "convert_plan", "plan_from_dict", "plans_from_data", "read_maa",
]
