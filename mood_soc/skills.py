"""mood_soc/skills.py —— 心情类技能定义（游戏规则数据层）。

把每条技能抽象成一个 Skill 描述符（数据 + 少量条件函数），
rules 模块用统一的解释器去执行，从而做到"新增技能 = 新增一条数据"，
与计算逻辑解耦（低耦合）。

约定（重要）：技能 value 的符号语义
  - SELF_CONSUME / FACILITY_CONSUME / CC_REDUCE 中的 value 是"心情消耗的增减"，
    负号 = 减少消耗（减免），正号 = 增加消耗（加耗）。
  - CC_RECOVER / DORM_* 中的 value 是"心情回复速率"（恒为正值）。

数值均为 decimal.Decimal，保证十进制精确。
数值与规则来源：《resources/心情消耗回复和工休时间.docx》。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional, Tuple

from .config import FacilityType


class SkillKind(str, Enum):
    """技能作用方式。"""
    SELF_CONSUME = "self_consume"            # 自身心情消耗增减（正=加耗，负=减耗）
    FACILITY_CONSUME = "facility_consume"    # 对同设施全体（含自身）的心情消耗增减
    CC_REDUCE = "cc_reduce"                  # 中枢全局心情消耗减免（同类按干员取最高）
    CC_RECOVER = "cc_recover"                # 中枢提供的心情回复（作用于工作设施）
    DORM_SELF = "dorm_self"                  # 宿舍：自身回复（同种取最高）
    DORM_GROUP = "dorm_group"                # 宿舍：群体回复（同种取最高）
    DORM_SINGLE = "dorm_single"              # 宿舍：单体回复（同种取最高，锁定目标）
    DORM_TARGETED = "dorm_targeted"          # 宿舍：定向回复（对满足条件的干员加成，可叠加）
    ELIMINATE_SELF = "eliminate_self"        # 消除同设施干员"自身心情消耗"的影响


@dataclass(frozen=True)
class Skill:
    """一条心情类技能。"""
    id: str
    name: str
    kind: SkillKind
    value: Decimal = Decimal("0")
    facility_types: Tuple[FacilityType, ...] = ()   # 空 = 任意设施
    target_trait: Optional[str] = None              # 定向技能匹配的阵营（如"岁"）
    condition: Optional[Callable] = None            # 额外触发条件（接收 SkillContext）
    note: str = ""


# ----------------------------------------------------------------------------
# 条件函数：这里只通过鸭子类型读取上下文（ctx.world / ctx.owner / ...），
# 因此本模块无需 import rules，避免循环依赖。
# ----------------------------------------------------------------------------
def _with_mogu(ctx) -> bool:
    """维什戴尔的条件：控制中枢内同时有"魔王"时，额外 +0.1。"""
    cc = ctx.world.control_center()
    return cc is not None and any(o.name == "魔王" for o in cc.operators)


def _mood_below_18(ctx) -> bool:
    """刺玫的条件：目标干员心情低于 18。"""
    return ctx.target.mood < Decimal("18")


# ----------------------------------------------------------------------------
# 技能库（示例 / 可扩展）。数值均取自文档中明确给出的例子；
# 标注"示例值"者表示文档未给出确切数值，仅用于演示机制，实际使用需以游戏数据为准。
# ----------------------------------------------------------------------------
SKILLS = {
    # ---- 自身心情消耗（self_consume）----
    "泡泡-制造减耗": Skill(
        "泡泡-制造减耗", "泡泡：进驻制造站时心情每小时消耗 -0.25",
        SkillKind.SELF_CONSUME, Decimal("-0.25"), (FacilityType.MANUFACTURING,)),
    "斥罪-办公加耗": Skill(
        "斥罪-办公加耗", "斥罪：进驻办公室时心情每小时消耗 +0.5",
        SkillKind.SELF_CONSUME, Decimal("0.5"), (FacilityType.OFFICE,)),
    "阿罗玛-制造加耗": Skill(
        "阿罗玛-制造加耗", "阿罗玛：心情每小时消耗 +0.25",
        SkillKind.SELF_CONSUME, Decimal("0.25"), (FacilityType.MANUFACTURING,)),
    "火神-制造减耗": Skill(
        "火神-制造减耗", "火神：心情每小时消耗 -0.25",
        SkillKind.SELF_CONSUME, Decimal("-0.25"), (FacilityType.MANUFACTURING,)),
    "夕-自身加耗": Skill(
        "夕-自身加耗", "夕：自身心情每小时消耗增加（示例值，需实测）",
        SkillKind.SELF_CONSUME, Decimal("0.3"), (FacilityType.MANUFACTURING,), target_trait="岁"),
    "重岳-自身加耗": Skill(
        "重岳-自身加耗", "重岳：自身心情每小时消耗增加（示例值，需实测）",
        SkillKind.SELF_CONSUME, Decimal("0.3"), (FacilityType.MANUFACTURING,), target_trait="岁"),

    # ---- 设施级心情消耗（facility_consume，作用于同设施全体含自身）----
    "黍-制造全体减耗": Skill(
        "黍-制造全体减耗", "黍：进驻制造站时，站内所有干员心情每小时消耗 -0.1",
        SkillKind.FACILITY_CONSUME, Decimal("-0.1"), (FacilityType.MANUFACTURING,)),
    "火哨-贸易全体减耗": Skill(
        "火哨-贸易全体减耗", "火哨：进驻贸易站时，站内干员心情每小时消耗 -0.1",
        SkillKind.FACILITY_CONSUME, Decimal("-0.1"), (FacilityType.TRADING,)),
    "巫恋-贸易全体加耗": Skill(
        "巫恋-贸易全体加耗", "巫恋：进驻贸易站时，全体心情每小时消耗 +0.25",
        SkillKind.FACILITY_CONSUME, Decimal("0.25"), (FacilityType.TRADING,)),

    # ---- 中枢全局减免（cc_reduce，按干员求和后取最高）----
    "维什戴尔-全局减免": Skill(
        "维什戴尔-全局减免", "维什戴尔：为发电/制造/贸易/办公/会客提供每小时 0.1 心情减免",
        SkillKind.CC_REDUCE, Decimal("0.1"),
        (FacilityType.POWER, FacilityType.MANUFACTURING, FacilityType.TRADING,
         FacilityType.OFFICE, FacilityType.RECEPTION)),
    "维什戴尔-魔王加成": Skill(
        "维什戴尔-魔王加成", "维什戴尔：与魔王同时在中枢上班时，额外提供 0.1（总额 0.2）",
        SkillKind.CC_REDUCE, Decimal("0.1"),
        (FacilityType.POWER, FacilityType.MANUFACTURING, FacilityType.TRADING,
         FacilityType.OFFICE, FacilityType.RECEPTION),
        condition=_with_mogu),
    # 文档示例："玛恩纳 + 4 个笑脸在中枢工作，提供 -0.25 的减免"。
    # 笑脸类技能各自的具体数值文档未逐一给出，这里把合计 0.25 建模为一条示例技能，
    # 便于复现文档的 160h 示例；实际使用时可按具体笑脸技能拆解。
    "玛恩纳-笑脸扩散示例": Skill(
        "玛恩纳-笑脸扩散示例", "示例：玛恩纳 + 4 个笑脸在中枢，合计提供 0.25 心情减免",
        SkillKind.CC_REDUCE, Decimal("0.25"),
        (FacilityType.POWER, FacilityType.MANUFACTURING, FacilityType.TRADING,
         FacilityType.OFFICE, FacilityType.RECEPTION)),

    # ---- 中枢心情回复（cc_recover）----
    "玛恩纳-中枢回复": Skill(
        "玛恩纳-中枢回复", "玛恩纳：进驻中枢时，中枢内所有干员心情每小时恢复 +0.05",
        SkillKind.CC_RECOVER, Decimal("0.05"), (FacilityType.CONTROL_CENTER,)),
    "玛恩纳-设施回复": Skill(
        "玛恩纳-设施回复", "玛恩纳：进驻中枢时，发电/办公/会客工作的干员心情每小时恢复 +0.1",
        SkillKind.CC_RECOVER, Decimal("0.1"),
        (FacilityType.POWER, FacilityType.OFFICE, FacilityType.RECEPTION)),

    # ---- 宿舍回复 ----
    "菲亚梅塔-自身回复": Skill(
        "菲亚梅塔-自身回复", "菲亚梅塔：自身心情每小时恢复 +2，且不接受其它来源的回复",
        SkillKind.DORM_SELF, Decimal("2")),
    "冰酿-分配回复": Skill(
        "冰酿-分配回复", "冰酿：使心情未满的宿舍成员平均分配总计每小时 +0.8 的加成",
        SkillKind.DORM_GROUP, Decimal("0.8")),   # 实际按 0.8/N 分配，由 rules 特殊处理
    "刺玫-群体回复": Skill(
        "刺玫-群体回复", "刺玫：宿舍内所有干员心情每小时恢复 +0.15（同种取最高）",
        SkillKind.DORM_GROUP, Decimal("0.15")),
    "遥-明星效应": Skill(
        "遥-明星效应", "遥：宿舍内所有干员心情每小时恢复 +0.15（同种取最高）",
        SkillKind.DORM_GROUP, Decimal("0.15")),
    "刺玫-低心情加成": Skill(
        "刺玫-低心情加成", "刺玫：宿舍内心情 18 以下的干员恢复效果额外 +0.1（定向回复）",
        SkillKind.DORM_TARGETED, Decimal("0.1"), condition=_mood_below_18),

    # ---- 消除类（eliminate_self）----
    "槐琥-消除自身消耗": Skill(
        "槐琥-消除自身消耗", "槐琥：消除当前制造站内所有干员自身心情消耗的影响",
        SkillKind.ELIMINATE_SELF, Decimal("0"), (FacilityType.MANUFACTURING,)),
    "令-消除岁自身消耗": Skill(
        "令-消除岁自身消耗", "令：消除当前控制中枢内所有岁干员自身心情消耗的影响",
        SkillKind.ELIMINATE_SELF, Decimal("0"), (FacilityType.CONTROL_CENTER,), target_trait="岁"),
}


# ----------------------------------------------------------------------------
# 内置干员（名字 -> 技能 id 列表），方便快速搭建场景。
# ----------------------------------------------------------------------------
DEFAULT_OPERATORS = {
    "泡泡": ["泡泡-制造减耗"],
    "斥罪": ["斥罪-办公加耗"],
    "阿罗玛": ["阿罗玛-制造加耗"],
    "火神": ["火神-制造减耗"],
    "黍": ["黍-制造全体减耗"],
    "火哨": ["火哨-贸易全体减耗"],
    "巫恋": ["巫恋-贸易全体加耗"],
    "槐琥": ["槐琥-消除自身消耗"],
    "令": ["令-消除岁自身消耗"],
    "夕": ["夕-自身加耗"],
    "重岳": ["重岳-自身加耗"],
    "维什戴尔": ["维什戴尔-全局减免", "维什戴尔-魔王加成"],
    "玛恩纳": ["玛恩纳-中枢回复", "玛恩纳-设施回复"],
    "菲亚梅塔": ["菲亚梅塔-自身回复"],
    "冰酿": ["冰酿-分配回复"],
    "刺玫": ["刺玫-群体回复", "刺玫-低心情加成"],
    "遥" : ["遥-明星效应"],
    "魔王": [],                 # 无心情技能，仅用于触发维什戴尔的联动条件
}

# 阵营 / 特性
TRAITS = {
    "令": "岁",
    "夕": "岁",
    "重岳": "岁",
}
