"""data/skill_model.py —— **技能数据模型**（`Skill` / `SkillEquip` / `SkillKind`）。

这些类描述"一条心情技能长什么样"，是 `data/skills_data.py`（生成物，755 条 buff / 250 条
clause）的**元素类型**，所以住在数据包里：数据包不依赖计算包，生成物只 import 数据包。

```
data/skill_model.py                 ← 本模块（Skill / SkillEquip / SkillKind）
data/conditions.py                  ← 条件函数 + _factions_of
data/skills_data.py                 ← 生成物：SKILLS / SKILL_EQUIPS / DEFAULT_OPERATORS / …
mood_soc/skills.py                  ← 兼容门面：把上面这些再 re-export 一次
```

⚠️ **本模块只允许依赖 `data/domain.py`**（领域基元：设施枚举与心情上下限）。它**不能** import
`mood_soc.*`（那是计算包），否则生成物就会把计算层拖进数据包，重新变成环。

## 约定（重要）：技能 value 的符号语义

- `SELF_CONSUME` / `FACILITY_CONSUME` / `ROOM_OTHERS_CONSUME` 的 value 是"心情消耗的增减"，
  负号 = 减少消耗（减免），正号 = 增加消耗（加耗）；
- `CC_RECOVER` / `DORM_*` 的 value 是"心情回复速率"（恒为正值）。

## 精英化判断（本项目的核心扩展）

- 解锁/提升信息属于「干员↔技能」绑定（`SkillEquip`），而非技能本身（`Skill`），
  因为同一 skill_id 在不同干员身上解锁等级可能不同。
- `SkillEquip.unlock_elite`：解锁所需精英化等级（0/1/2）；
- `SkillEquip.unlock_level`：解锁所需干员等级（1 或 30，三星机械满级用 30）；
- `SkillEquip.enhanced`：True = 精英化"提升"（β 替换 α）；False = 独立技能（解锁）；
- `SkillEquip.replaces`：提升后替换掉的基础技能 id（None 表示无替换）；
- 规则引擎按 `op.elite >= unlock_elite` 且 `op.level >= unlock_level` 判定是否已解锁，
  同一 family 内 enhanced 技能解锁后替换其低版本（β 替换 α，不叠加）。

数值均为 `decimal.Decimal`，保证十进制精确。
数值与规则来源：`data/moods_skills.txt`、`data/operators.txt`。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional, Tuple

from data.domain import FacilityType


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
    target_faction: Optional[str] = None            # 定向技能匹配的阵营/标签（如"岁"，见 conditions._factions_of）
    condition: Optional[Callable] = None            # 额外触发条件（接收 SkillContext）
    note: str = ""
    exclusive: bool = False                          # 独占回复（菲亚梅塔自律：不接受其它来源）
    pool: bool = False                               # 池分配（冰酿：总额平分给未满成员）
    untranslated: bool = False                       # 骨架确定但暂不生效（见 partial_mode="hold"）
    count_faction: Optional[str] = None              # per-count 阵营：value 是"每个该阵营干员"的加成量
    # —— 分类模板（六轴，见 mood_soc/skill_templates.py）——
    template_id: str = ""                            # 模板 ID（M01~M17 / X01~X11）
    max_group: Optional[str] = None                  # 轴 F3：同组内**取最高**而非求和（官方术语 cc.c.sui2_1）
    spread_whitelist: bool = False                   # 轴 M02c：本技能是"扩散"提供者（玛恩纳公事公办）
    partial: bool = False                            # 模板已确定但条件槽为空（保留效果骨架）
    partial_mode: str = ""                           # "" / "apply"（按骨架生效）/ "hold"（保留骨架但不生效）
    # —— 变量（轴 E4/E5 的"中间货币"版，见 mood_soc/variables.py）——
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


__all__ = ["Skill", "SkillEquip", "SkillKind"]
