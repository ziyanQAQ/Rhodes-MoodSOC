"""data/conditions.py —— 技能**条件函数**与阵营查询（数据的一部分，不含计算逻辑）。

## 为什么单独一个模块

条件函数是"技能数据"的一部分（`Skill.condition` 字段要拿它们当值），
而技能数据表 `data/skills_data.py` 是**生成物**。历史做法是让生成物反过来
`from mood_soc.skills import _cond_*` —— 于是「生成脚本无法重新生成自己」，
而且数据包反向依赖计算包。现在条件函数住在数据包里，生成物只 import 本模块：

```
data/domain  ←  data/conditions  ←  data/skills_data
                    ↑
        mood_soc/skills（re-export，保持历史 import 路径可用）
```

## 鸭子类型契约（别在这里 import 计算层）

所有 `_cond_*` 只通过鸭子类型读上下文 `ctx`，因此本模块**不需要** import `rules`/`models`：

| 属性 | 含义 |
|---|---|
| `ctx.world` | 基建布局（`BaseLayout`：`facilities` / `control_center()` / `all_dormitories()`） |
| `ctx.owner` | 技能持有者（`Operator`：`name` / `mood` / `factions` / `trait`） |
| `ctx.target` | 技能作用目标（无目标时为 `None`） |
| `ctx.facility` | 持有者所在设施（`Facility`：`operators` / `ftype`） |

`ctx.facility.operators[0] is ctx.owner` 这类**同一性判断**也是契约的一部分
（`_cond_alone_in_facility` 依赖它）。
"""
from __future__ import annotations

from decimal import Decimal

from data.domain import MOOD_MAX, FacilityType


def _cond_mood_below_18(ctx) -> bool:
    """刺玫：目标干员心情低于 18。"""
    return ctx.target.mood < Decimal("18")


def _cond_mood_below_20(ctx) -> bool:
    """净化呼吸：目标干员心情低于 20。"""
    return ctx.target.mood < Decimal("20")


def _cond_with_cc_mogui(ctx) -> bool:
    """维什戴尔"巴别塔之帜"条件：控制中枢内同时有"魔王"。"""
    cc = ctx.world.control_center()
    return cc is not None and any(o.name == "魔王" for o in cc.operators)


def _cond_alone_in_facility(ctx) -> bool:
    """「该设施内只有自身处于工作状态时」。

    用于会客室 6 条：双面间谍 / 「职业操守」α·β / 我自己的愿望 / 专业经理·α·β。
    上游原文（`meet_spd&cost_condChar[00x]`）：
        「进驻会客室时，如果会客室内只有自身处于工作状态时，线索搜集速度提升 X%，
          心情每小时消耗 +N」
    → 心情消耗这一半是**有条件的**：只有在会客室里独自一人时才生效。
    """
    ops = ctx.facility.operators
    return len(ops) == 1 and ops[0] is ctx.owner


def _cond_target_in_faction(*factions: str):
    """「如果目标是 <阵营/标签> 干员」（M08/M09 的定向加成）。

    可传多个阵营，任一命中即可（上游用「和」连接，语义上是并集）：
      - 资深料理人「如果目标是 **莱欧斯小队** 干员，则恢复效果额外 +0.15」
      - 降生于冰寒「如果目标是 **萨米** 干员，则恢复效果额外 +0.45」
      - 狩猎好帮手「如果目标是 **怪物猎人小队**成员**和泡影国狩猎小队**，
        则恢复效果额外 +0.45」（`cc.tag.mh` + `cc.tag.mh2`）
    """
    def cond(ctx) -> bool:
        target = getattr(ctx, "target", None)
        if target is None:
            return False
        fs = _factions_of(target)
        return any(f in fs for f in factions)
    return cond


def _cond_target_is(*names: str):
    """「如果目标是 <具体干员>」（M09 的定向加成）。

    上游原文（`dorm_rec_single_P[000]` 等）：
        「…使该宿舍内除自身以外心情未满的某个干员每小时恢复+0.55（同种效果取最高），
          **如果目标是 <干员>**，则恢复效果额外+0.45」
    被点名的目标是具体干员而非阵营，故单独一个条件函数：
      - 沏茶 → 锡兰 · 烤肉大师 → 嘉维尔 · 毒剂师之友 → 蓝毒
    """
    def cond(ctx) -> bool:
        target = getattr(ctx, "target", None)
        return target is not None and getattr(target, "name", "") in names
    return cond


def _cond_self_full_mood(ctx) -> bool:
    """「进驻时**自身为满心情**」——M15a 患难之交的触发条件。

    上游原文（`dorm_exchangeAp[000]`）：「进驻宿舍时，**如果自身为满心情**，
    则与当前宿舍**前一位进驻**的干员互换心情」。
    """
    return ctx.owner.mood >= MOOD_MAX


def _cond_no_abyssal_outside_dorm(ctx) -> bool:
    """潮汐守望「反之」：没有深海猎人进驻在宿舍以外的设施。

    上游原文：「每有 1 个深海猎人干员进驻在宿舍以外的设施，则自身心情每小时消耗 +0.5；
    **反之**则自身心情每小时恢复 +0.5」。

    ⚠️ 口径（用户拍板）：**含技能持有者自身**，与 `variables.basis_count` 的
    `abyssal_non_dorm` 分支同一口径（那边也把她自己数进去）。因此：
    歌蕾蒂娅自己就进驻在控制中枢（＝宿舍以外）时，本条件恒为 False，
    潮汐守望的「反之」恢复分支（`#2`）与依赖它的「宿舍内深海猎人满心情」（`#3`）
    在实际布局中**不可达**——两个分句保留在数据里备查，但不产生回复。
    详见 `documents/04-特殊机制.md` 第 23 条。
    """
    for f in ctx.world.facilities:
        if f.ftype in (FacilityType.DORMITORY, FacilityType.PRIVATE):
            continue
        for o in f.operators:
            if "深海猎人" in _factions_of(o):
                return False
    return True


def _cond_dorm_abyssals_full_mood(ctx) -> bool:
    """潮汐守望额外 +0.5：宿舍内的深海猎人**均为满心情**。

    口径：要求宿舍内至少有 1 名深海猎人（否则"均为满心情"空真，会把额外 +0.5 白送）。
    该分句以「反之」（`_cond_no_abyssal_outside_dorm`）为前提，故随其一起不可达（见上）。
    """
    if not _cond_no_abyssal_outside_dorm(ctx):
        return False
    in_dorm = [o for f in ctx.world.all_dormitories() for o in f.operators
               if o is not ctx.owner and "深海猎人" in _factions_of(o)]
    return bool(in_dorm) and all(o.mood >= MOOD_MAX for o in in_dorm)


def _cond_with_cc_xiangzi(ctx) -> bool:
    """丰川祥子相关联动：控制中枢内同时有"丰川祥子"。"""
    cc = ctx.world.control_center()
    return cc is not None and any(o.name == "丰川祥子" for o in cc.operators)


def _cond_with_cc_operator(name: str):
    """条件：控制中枢内同时有某干员（如"与阿米娅一起进驻控制中枢"）。"""
    def cond(ctx) -> bool:
        cc = ctx.world.control_center()
        return cc is not None and any(o.name == name for o in cc.operators)
    return cond


def _cond_with_facility_operator(name: str):
    """条件：目标所在设施内同时有某干员（如"德克萨斯与拉普兰德同驻贸易站"）。"""
    def cond(ctx) -> bool:
        return any(o.name == name for o in ctx.facility.operators)
    return cond


def _cond_with_cc_faction(faction: str):
    """条件：控制中枢内存在"其他"某阵营干员（排除技能持有者自身）。

    如摆渡人"英雄的骄傲"：与萨尔贡干员一起进驻控制中枢（摆渡人自己也是萨尔贡，
    但"一起工作"指其他萨尔贡干员，故排除自身）。
    """
    def cond(ctx) -> bool:
        cc = ctx.world.control_center()
        return cc is not None and any(
            o is not ctx.owner and faction in _factions_of(o) for o in cc.operators)
    return cond


# ----------------------------------------------------------------------------
# 阵营 / 标签表：**自动生成**（data/skills_data.py 的 OPERATOR_FACTIONS / FACTION_MEMBERS）。
#
# 来源：scripts/generate_factions.py 读上游 `gamedata_const.json → termDescriptionDict`
#       的 `cc.g.*`（阵营）/ `cc.tag.*`（标签），人工补充见 data/factions_supplement.txt。
#
# ⚠️ 历史上这里有一张手工表，实测是错的（把「米诺斯」6 人误标为「萨尔贡」、
#    「岁」只收 3/7），故改为上游自动生成。修数据请改上游或 supplement，不要改这里。
#
# ⚠️ import 放在**函数体内**：`skills_data.py` 末尾会反过来 import 本模块（取 `_cond_*`），
#    模块级 import 会造成循环导入。
# ----------------------------------------------------------------------------
def _factions_of(who):
    """干员的阵营/标签元组。

    who 可以是干员名（str）或 Operator：
      - 自动生成的 `OPERATOR_FACTIONS`（上游权威）为基准；
      - Operator.factions 可额外覆盖/补充（自定义场景用）；
      - 兼容：Operator.trait 也视为一个阵营（历史 API，如手写 trait="岁"）。
    """
    from data.skills_data import OPERATOR_FACTIONS
    if isinstance(who, str):
        return OPERATOR_FACTIONS.get(who, ())
    name = getattr(who, "name", None)
    facs = list(OPERATOR_FACTIONS.get(name, ()))
    for extra in (getattr(who, "factions", None) or ()):
        if extra not in facs:
            facs.append(extra)
    trait = getattr(who, "trait", None)
    if trait and trait not in facs:
        facs.append(trait)
    return tuple(facs)


#: 生成器按名字挂条件时用的名单（`scripts/generate_skills_data.py` 的 `CLAUSE_COND`/`COOP_COND`）。
CONDITION_NAMES = (
    "_cond_mood_below_18",
    "_cond_mood_below_20",
    "_cond_with_cc_mogui",
    "_cond_with_cc_xiangzi",
    "_cond_with_cc_operator",
    "_cond_with_facility_operator",
    "_cond_with_cc_faction",
    "_cond_alone_in_facility",
    "_cond_no_abyssal_outside_dorm",
    "_cond_dorm_abyssals_full_mood",
    "_cond_target_in_faction",
    "_cond_target_is",
    "_cond_self_full_mood",
)

__all__ = [
    "CONDITION_NAMES",
    "_factions_of",
    "_cond_mood_below_18",
    "_cond_mood_below_20",
    "_cond_with_cc_mogui",
    "_cond_with_cc_xiangzi",
    "_cond_with_cc_operator",
    "_cond_with_facility_operator",
    "_cond_with_cc_faction",
    "_cond_alone_in_facility",
    "_cond_no_abyssal_outside_dorm",
    "_cond_dorm_abyssals_full_mood",
    "_cond_target_in_faction",
    "_cond_target_is",
    "_cond_self_full_mood",
]
