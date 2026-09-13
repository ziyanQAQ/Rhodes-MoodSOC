"""mood_soc/scenario.py —— 场景构建（输入解析层）。

把"字典 / JSON"形式的输入解析成 BaseLayout，供核心引擎使用。
这是 CLI 与核心引擎之间的适配层，隔离了输入格式与内部模型（低耦合）。
"""
from __future__ import annotations

from .battery import to_decimal
from .config import parse_facility_type, MOOD_MAX
from .models import BaseLayout, Facility, Operator
from .skills import DEFAULT_OPERATORS, TRAITS


def build_operator(spec) -> Operator:
    """由一条干员描述（dict）构建 Operator。"""
    if isinstance(spec, str):
        spec = {"name": spec}
    name = spec["name"]
    skill_ids = list(spec.get("skill_ids") or DEFAULT_OPERATORS.get(name, []))
    trait = spec.get("trait", TRAITS.get(name))
    mood = to_decimal(spec.get("mood", MOOD_MAX))
    return Operator(name=name, mood=mood, skill_ids=skill_ids, trait=trait)


def build_base_layout(data) -> BaseLayout:
    """由整个场景描述构建 BaseLayout。

    期望格式：
        {
            "facilities": [
                {"type": "控制中枢", "level": 1, "operators": ["玛恩纳", "魔王"]},
                {"type": "制造站", "level": 3, "operators": [
                     {"name": "泡泡", "mood": 24.0}, "普通干员A"
                ]},
                ...
            ]
        }
    其中 "type" 既可用中文名也可用英文枚举值；
    干员既可用名字字符串，也可用 {"name", "mood", "skill_ids", "trait"} 对象。
    """
    facilities = []
    for f in data.get("facilities", []):
        ftype = parse_facility_type(f["type"])
        if ftype is None:
            raise ValueError(f"无法识别的设施类型：{f['type']!r}")
        operators = [build_operator(o) for o in f.get("operators", [])]
        atmosphere = f.get("atmosphere")
        facilities.append(Facility(
            ftype=ftype,
            level=int(f.get("level", 1)),
            operators=operators,
            atmosphere=to_decimal(atmosphere) if atmosphere is not None else None,  # None=按等级满氛围
        ))
    return BaseLayout(facilities=facilities)
