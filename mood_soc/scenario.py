"""mood_soc/scenario.py —— 场景构建（输入解析层）。

把"字典 / JSON"形式的输入解析成 BaseLayout，供核心引擎使用。
这是 CLI 与核心引擎之间的适配层，隔离了输入格式与内部模型（低耦合）。
"""
from __future__ import annotations

from .battery import to_decimal
from .config import parse_facility_type, MOOD_MAX
from .models import BaseLayout, Facility, Operator, build_entry_event_config
from .models import build_idle_to_dorm_config
from .skills import DEFAULT_OPERATORS, TRAITS

# 干员等级默认值：满练口径（"等级 30 解锁"的技能才不会默认失效）
DEFAULT_OPERATOR_LEVEL = 30


def build_operator(spec) -> Operator:
    """由一条干员描述（dict）构建 Operator。"""
    if isinstance(spec, str):
        spec = {"name": spec}
    name = spec["name"]
    skill_ids = list(spec.get("skill_ids") or DEFAULT_OPERATORS.get(name, []))
    trait = spec.get("trait", TRAITS.get(name))
    mood = to_decimal(spec.get("mood", MOOD_MAX))
    # 精英化等级 / 干员等级：默认满练（elite=2）+ 等级 30
    # ⚠️ 等级只在"等级 30 解锁"这一处被读（上游 `operators.txt` 的 `unlock` 列有 4 条），
    #    默认给 30 才与"默认精英化二"的口径一致（30 = 三星机械满级）；
    #    给 1 会让杜林/THRM-EX/Lancet-2 的那几条技能默认失效。
    elite = int(spec.get("elite", 2))
    level = int(spec.get("level", DEFAULT_OPERATOR_LEVEL))
    factions = spec.get("factions")
    return Operator(name=name, mood=mood, skill_ids=skill_ids, trait=trait,
                    elite=elite, level=level,
                    factions=tuple(factions) if factions else None)


def build_base_layout(data, validate: bool = False) -> BaseLayout:
    """由整个场景描述构建 BaseLayout。

    期望格式：
        {
            "facilities": [
                {"type": "控制中枢", "level": 5, "operators": ["玛恩纳", "魔王"]},
                {"type": "制造站", "level": 3, "name": "制造站#1", "operators": [
                     {"name": "泡泡", "mood": 24.0}, "普通干员A"
                ], "deputies": ["副手A"], "enabled": true},
                ...
            ]
        }
    其中 "type" 既可用中文名也可用英文枚举值；
     干员既可用名字字符串，也可用
     {"name", "mood", "skill_ids", "trait", "factions", "elite", "level"} 对象；
     factions 覆盖自动生成的阵营表（默认 None = 用上游生成值，见 skills.OPERATOR_FACTIONS）。
     elite 为精英化等级（0/1/2，默认 2 满练），level 为干员等级（默认 30，用于"等级30解锁"）。

    设施字段（P1 新增，多房间布局必需）：
      - `name`      实例名（同类型多房间时便于区分，如"制造站#2"）；缺省用类型标签
      - `level`     设施等级（容量按上游 `FACILITY_SLOTS_BY_LEVEL` 推导）
      - `operators` 进驻干员（占位、消耗心情、算"处于工作状态"）
      - `deputies`  副手（不占位；被「不包含副手」类技能排除，不参与心情消耗）
      - `slots`     容量覆盖（缺省按等级查上游容量表）
      - `enabled`   是否已建成（false 则不计入「每有 N 间」）
      - `atmosphere` 仅宿舍：实际氛围

    顶层可选字段（在 `facilities` 之外）：
      - `entry_events`：进驻事件（M15a 患难之交）配置 —— **换不换 / 换谁**：
        `{"enabled": true, "swap_with": "路人"}`（也可写 `true`/`false`，或只写目标人名）。
        见 `models.EntryEventConfig` 与 `rules.apply_entry_events`。

    validate=True 时做容量/房间数自检，有问题抛 ValueError
    （默认 False：历史场景可能刻意超容量，不破坏既有用法）。
    """
    facilities = []
    for f in data.get("facilities", []):
        ftype = parse_facility_type(f["type"])
        if ftype is None:
            raise ValueError(f"无法识别的设施类型：{f['type']!r}")
        operators = [build_operator(o) for o in f.get("operators", [])]
        deputies = [build_operator(o) for o in f.get("deputies", [])]
        atmosphere = f.get("atmosphere")
        slots = f.get("slots")
        facilities.append(Facility(
            ftype=ftype,
            level=int(f.get("level", 1)),
            operators=operators,
            atmosphere=to_decimal(atmosphere) if atmosphere is not None else None,  # None=按等级满氛围
            name=str(f.get("name") or ""),
            deputies=deputies,
            slots=int(slots) if slots is not None else None,
            enabled=bool(f.get("enabled", True)),
        ))
    world = BaseLayout(
        facilities=facilities,
        # 顶层可选的进驻事件配置（M15a 换不换 / 换谁），见 models.EntryEventConfig：
        #   {"entry_events": {"enabled": true, "swap_with": "路人"}, "facilities": [...]}
        entry_events=build_entry_event_config(data.get("entry_events")),
        # 顶层可选的「闲置入宿」配置（未满的闲置干员进宿舍恢复），见 models.IdleToDormConfig：
        #   {"idle_to_dorm": {"enabled": true, "per_operator": {"虎狼丸": "甲"}}, "facilities": [...]}
        idle_to_dorm=build_idle_to_dorm_config(data.get("idle_to_dorm")),
    )
    if validate:
        issues = world.validate()
        if issues:
            raise ValueError("布局自检未通过：\n  - " + "\n  - ".join(issues))
    return world
