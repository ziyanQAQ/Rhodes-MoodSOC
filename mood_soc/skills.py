"""mood_soc/skills.py —— 心情类技能框架（游戏规则数据层）。

本模块定义技能的数据模型（Skill / SkillEquip / SkillKind）与条件函数，
具体技能库数据在 skills_data.py（由 scripts/generate_skills_data.py 自动生成），
本模块末尾 import 并 re-export，因此其它模块仍可 `from .skills import SKILLS, ...`。

约定（重要）：技能 value 的符号语义
  - SELF_CONSUME / FACILITY_CONSUME / ROOM_OTHERS_CONSUME 中的 value 是"心情消耗的增减"，
    负号 = 减少消耗（减免），正号 = 增加消耗（加耗）。
  - CC_RECOVER / DORM_* 中的 value 是"心情回复速率"（恒为正值）。

精英化判断（本项目的核心扩展）：
  - 解锁/提升信息属于「干员↔技能」绑定（SkillEquip），而非技能本身（Skill），
    因为同一 skill_id 在不同干员身上解锁等级可能不同。
  - SkillEquip.unlock_elite：解锁所需精英化等级（0/1/2）
  - SkillEquip.unlock_level：解锁所需干员等级（1 或 30，三星机械满级用 30）
  - SkillEquip.enhanced：True = 精英化"提升"（β 替换 α）；False = 独立技能（解锁）
  - SkillEquip.replaces：提升后替换掉的基础技能 id（None 表示无替换）
  - 规则引擎按 op.elite >= unlock_elite 且 op.level >= unlock_level 判定是否已解锁，
    同一 family 内 enhanced 技能解锁后替换其低版本（β 替换 α，不叠加）。

数值均为 decimal.Decimal，保证十进制精确。
数值与规则来源：《resources/moods_skills.txt》《resources/operators.txt》。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional, Tuple

from .config import MOOD_MAX, FacilityType


class SkillKind(str, Enum):
    """技能作用方式。"""
    SELF_CONSUME = "self_consume"               # 自身心情消耗增减（正=加耗，负=减耗）
    FACILITY_CONSUME = "facility_consume"       # 对同设施全体（含自身）的心情消耗增减
    ROOM_OTHERS_CONSUME = "room_others_consume" # 对同设施其他干员（不含自身）的心情消耗增减
    CC_REDUCE = "cc_reduce"                     # 中枢全局心情消耗减免（同类按干员取最高）
    CC_RECOVER = "cc_recover"                   # 中枢提供的心情回复（作用于工作设施/中枢/宿舍）
    DORM_SELF = "dorm_self"                     # 宿舍：自身回复（同种取最高）
    DORM_GROUP = "dorm_group"                   # 宿舍：群体回复（同种取最高）
    DORM_SINGLE = "dorm_single"                 # 宿舍：单体回复（同种取最高，锁定目标）
    DORM_TARGETED = "dorm_targeted"             # 宿舍：定向回复（对满足条件的干员加成，可叠加）
    ELIMINATE_SELF = "eliminate_self"           # 消除同设施干员"自身心情消耗"的影响
    DORM_META = "dorm_meta"                     # 元修正（M17）：强化**他人**在宿舍内的恢复效果


@dataclass(frozen=True)
class Skill:
    """一条心情类技能（一个 skill_id 的一个 clause）。

    facility_types 语义随 kind 而定：
      - 自身/设施/消除类：owner 所在设施（技能在哪类设施内生效）
      - CC_RECOVER：回复作用的目标设施（中枢内/宿舍/工作设施）
      - DORM_*：空（宿舍回复函数内部已限定在宿舍）
    """
    id: str
    name: str
    kind: SkillKind
    value: Decimal = Decimal("0")
    facility_types: Tuple[FacilityType, ...] = ()
    target_faction: Optional[str] = None            # 定向技能匹配的阵营/标签（如"岁"，见 _factions_of）
    condition: Optional[Callable] = None            # 额外触发条件（接收 SkillContext）
    note: str = ""
    exclusive: bool = False                          # 独占回复（菲亚梅塔自律：不接受其它来源）
    pool: bool = False                               # 池分配（冰酿：总额平分给未满成员）
    untranslated: bool = False                       # 骨架确定但暂不生效（见 partial_mode="hold"）
    count_faction: Optional[str] = None              # per-count 阵营：value 是"每个该阵营干员"的加成量
    # —— 分类模板（六轴，见 skill_templates.py）——
    template_id: str = ""                            # 模板 ID（M01~M17 / X01~X11）
    max_group: Optional[str] = None                  # 轴 F3：同组内**取最高**而非求和（官方术语 cc.c.sui2_1）
    spread_whitelist: bool = False                   # 轴 M02c：本技能是"扩散"提供者（玛恩纳公事公办）
    partial: bool = False                            # 模板已确定但条件槽为空（保留效果骨架）
    partial_mode: str = ""                           # "" / "apply"（按骨架生效）/ "hold"（保留骨架但不生效）
    # —— 变量（轴 E4/E5 的"中间货币"版，见 variables.py）——
    var_name: Optional[str] = None                   # 依赖的变量名（人间烟火 / 热情值 / 无声共鸣…）
    var_per: Optional[Decimal] = None                # 「每有 N 点 <变量>」→ 值 × floor(变量/N)
    var_min: Optional[Decimal] = None                # 「<变量> 处于 N 点及以上」→ 门槛条件（不缩放值）
    # —— 计数基准（轴 E3/E4 的"可数条件"版，见 variables.basis_count）——
    basis: Optional[str] = None                      # 每有 N 个"什么"：power_count / dorm_level / …
    # —— 消除类（M13）的两种语义 ——
    self_only: bool = False                          # True = 只消除**自身**的自身消耗影响（若叶睦 互为半身）
                                                     # False = 消除**同设施所有干员**的（槐琥 团队精神 / 令 杯莫停）
    # —— 元修正（M17）：强化**他人**的效果 ——
    boost_provider: Optional[str] = None             # 被强化的技能持有者名（摩根「头号陪练」→ "推进之王"）
    boost_group: Optional[str] = None                # 只强化该提供者在**这个 group** 里的贡献（如 dorm_group）


@dataclass(frozen=True)
class SkillEquip:
    """一名干员对某技能的装备绑定（解锁/提升信息）。

    之所以与 Skill 分离：同一 skill_id 在不同干员身上解锁等级可能不同，
    且"提升"（β 替换 α）是干员个体层面的行为。
    """
    unlock_elite: int = 0                            # 解锁所需精英化等级
    unlock_level: int = 1                            # 解锁所需干员等级
    enhanced: bool = False                           # 是否精英化"提升"（β 替换 α）
    replaces: Optional[str] = None                   # 提升后替换掉的基础技能 id


# ----------------------------------------------------------------------------
# 条件函数：这里只通过鸭子类型读取上下文（ctx.world / ctx.owner / ctx.target / ctx.facility），
# 因此本模块无需 import rules，避免循环依赖。
# ----------------------------------------------------------------------------
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


def _cond_no_abyssal_outside_dorm(ctx) -> bool:
    """潮汐守望「反之」：没有**其他**深海猎人进驻在宿舍以外的设施。

    上游原文：「每有 1 个深海猎人干员进驻在宿舍以外的设施，则自身心情每小时消耗 +0.5；
    **反之**则自身心情每小时恢复 +0.5」。

    ⚠️ 解释口径：**排除技能持有者自身**（歌蕾蒂娅自己就在控制中枢＝宿舍以外设施）。
    若把她自己也算进去，则「每有…」恒 ≥ 1、下面的「反之」分支永远不可达——
    游戏设计不会写一个恒不可达的分支，故取「其他深海猎人」口径。
    """
    for f in ctx.world.facilities:
        if f.ftype in (FacilityType.DORMITORY, FacilityType.PRIVATE):
            continue
        for o in f.operators:
            if o is ctx.owner:
                continue
            if "深海猎人" in _factions_of(o):
                return False
    return True


def _cond_dorm_abyssals_full_mood(ctx) -> bool:
    """潮汐守望额外 +0.5：宿舍内的深海猎人**均为满心情**（且没有其他人在宿舍外）。

    口径：要求宿舍内至少有 1 名深海猎人（否则"均为满心情"空真，会把额外 +0.5 白送）。
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


# ----------------------------------------------------------------------------
# 阵营 / 标签表：**自动生成**（skills_data.OPERATOR_FACTIONS / FACTION_MEMBERS）。
#
# 来源：scripts/generate_factions.py 读上游 `gamedata_const.json → termDescriptionDict`
#       的 `cc.g.*`（阵营）/ `cc.tag.*`（标签），人工补充见 resources/factions_supplement.txt。
#
# ⚠️ 历史上这里有一张手工表，实测是错的（把「米诺斯」6 人误标为「萨尔贡」、
#    「岁」只收 3/7），故改为上游自动生成。修数据请改上游或 supplement，不要改这里。
# ----------------------------------------------------------------------------
def _factions_of(who):
    """干员的阵营/标签元组。

    who 可以是干员名（str）或 Operator：
      - 自动生成的 `OPERATOR_FACTIONS`（上游权威）为基准；
      - Operator.factions 可额外覆盖/补充（自定义场景用）；
      - 兼容：Operator.trait 也视为一个阵营（历史 API，如手写 trait="岁"）。
    """
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
# 技能库数据（自动生成）：SKILLS / DEFAULT_OPERATORS / SKILL_EQUIPS / TRAITS
# ----------------------------------------------------------------------------
from .skills_data import (  # noqa: E402
    DEFAULT_OPERATORS,
    FACTION_MEMBERS,
    OPERATOR_FACTIONS,
    ROOM2_MAX_GROUP,
    SKILLS,
    SKILL_EQUIPS,
    SPREAD_SKILL_IDS,
    TRAITS,
)


def base_skill_id(skill_id: str) -> str:
    """由 `skill_id#clause` 取回原始 skill_id（扩散白名单比对用）。"""
    return skill_id.split("#", 1)[0]


__all__ = [
    "Skill",
    "SkillEquip",
    "SkillKind",
    "SKILLS",
    "SKILL_EQUIPS",
    "DEFAULT_OPERATORS",
    "TRAITS",
    "OPERATOR_FACTIONS",
    "FACTION_MEMBERS",
    "SPREAD_SKILL_IDS",
    "ROOM2_MAX_GROUP",
    "base_skill_id",
]
