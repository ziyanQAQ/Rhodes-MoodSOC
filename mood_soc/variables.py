"""mood_soc/variables.py —— **变量账本**：技能之间的中间货币。

## 为什么需要它

上游术语表（`gamedata_const.json → termDescriptionDict` 的 `cc.bd*`）定义了 **26 种变量**，
它们是技能之间传递的中间货币，例如：

    人间烟火：夕「不以物喜」产出 → 重岳「孤光共照」消费（每 20 点 → room2 心情回复 +0.05）
    热情值　：若叶睦「演技的怪物」产出 +20 → 丰川祥子「生活的重压」消费（≥40 → 自身 +0.05 消耗）
    无声共鸣：塑心「无声共鸣」产出 → 塑心「无词颂歌」消费（每 5 点 → 宿舍回复 +0.01）

重构前这 26 种变量**完全没有载体**，导致所有依赖它们的子句只能标 `partial_mode=hold`（不生效），
共 43 条变量相关 buff + 31 条 `hold` 子句。

## 建模口径（重要）

- 变量是**基建级（全局）**的值，不是单干员属性。
- 产出者的条件（如夕「不以物喜」要求自身心情 < 12）读的是**当前快照**的心情，
  因此变量与心情之间存在**单向快照依赖**：先用当前心情算变量，再用变量算速率。
  解析解（`evaluate`）如此；数值解（`simulate`）每步重算，故能跟上变化。
- `basis`（计数基准）决定"每有 N 个什么"：
  | basis | 含义 | 上游例句 |
  |---|---|---|
  | `flat` | 固定值 | 演技的怪物：热情值 +20 |
  | `dorm_operator` | 宿舍内每有 1 名干员 | 偶像光环：宿舍内每有 1 名干员，热情值 +1 |
  | `recruit_slot` | 每个招募位（= 人力办公室等级） | 救援队·灾后普查：每个招募位 +10 点人间烟火 |
  | `sui_non_dorm` | 每个进驻在宿舍/活动室以外设施的「岁」干员（上限 5） | 知我为我 |
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional

from .battery import ZERO, to_decimal
from .config import FacilityType


# ============================================================================
# 26 种变量（官方术语表 cc.bd* / cc.bd_*）—— 完整清单，便于排查"这个变量是什么"
# ============================================================================
VARIABLES = (
    "念力", "意识实体", "徘徊旋律", "怅惘和声", "无词颂歌", "思维链环", "无声共鸣", "巫术结晶",
    "感知信息", "人间烟火", "情报储备", "乌萨斯特饮", "工程机器人", "记忆碎片", "梦境", "小节",
    "心情落差", "木天蓼", "可爱的艾露猫", "可靠的随从们", "魔物料理", "热情值",
    "丰富工作经验", "演技的怪物", "外势", "实地",
)

# 派生变量：不靠技能产出，而是由心情直接推出（24 − 当前心情）
DERIVED_MOOD_DROP = "心情落差"

# 有心情效果的变量（= 真正需要算的）。其余为登记性质，见 resources/skills_registry.txt 的 X09。
MOOD_RELEVANT_VARIABLES = ("人间烟火", "热情值", "无声共鸣", DERIVED_MOOD_DROP)

BASIS_DOC = {
    "flat": "无条件（固定值）",
    "dorm_operator": "宿舍内每有 1 名干员",
    "recruit_slot": "每个招募位（= 人力办公室等级）",
    "sui_non_dorm": "每个进驻在宿舍/活动室以外设施的「岁」干员（上限 5）",
    # —— 以下用于**心情子句**的"可数条件"（P4a）——
    "power_count": "每有 1 间发电站",
    "dorm_level": "当前宿舍每级",
    "dorm_unfull": "该宿舍每有 1 名心情未满干员",
    "dorm_others": "该宿舍内每有 1 名其他干员",
    "abyssal_non_dorm": "每有 1 个进驻在宿舍以外设施的「深海猎人」干员",
}

# 产出者条件（读当前快照）
PRODUCER_CONDITIONS = {
    "": "无条件",
    "mood_below_12": "自身心情 < 12",
    "mood_above_12": "自身心情 > 12",
}


@dataclass
class VariableLedger:
    """基建级变量快照：每个变量当前的值 + 由谁产出（可解释）。"""
    values: Dict[str, Decimal] = field(default_factory=dict)
    sources: Dict[str, List[str]] = field(default_factory=dict)

    # ------------------------------------------------------------------ 记录
    def add(self, name: str, value, source: str = "") -> None:
        if value == 0:
            return
        self.values[name] = self.values.get(name, ZERO) + to_decimal(value)
        if source:
            self.sources.setdefault(name, []).append(source)

    def set(self, name: str, value, source: str = "") -> None:
        self.values[name] = to_decimal(value)
        if source:
            self.sources.setdefault(name, []).append(source)

    # ------------------------------------------------------------------ 查询
    def get(self, name: str) -> Decimal:
        """变量当前值（未记录 = 0）。"""
        return self.values.get(name, ZERO)

    def units(self, name: str, per) -> Decimal:
        """「每有 `per` 点 <变量>」能凑出几份（向下取整）。

        例：人间烟火 = 45，per = 20 → 2 份（重岳「孤光共照」额外 +2×0.05）。
        `per` 为 0/None 时视为 1（即按变量自身的点数计份，用于「每 1 点」类）。
        """
        if not name:
            return ZERO
        step = to_decimal(per) if per else Decimal("1")
        if step <= ZERO:
            return ZERO
        return (self.get(name) / step).to_integral_value(rounding="ROUND_FLOOR")

    def at_least(self, name: str, threshold) -> bool:
        """「<变量> 处于 N 点及以上」类条件。"""
        return self.get(name) >= to_decimal(threshold)

    def explain(self) -> str:
        if not self.values:
            return "变量：（无）"
        lines = ["变量"]
        for name in sorted(self.values):
            src = "；".join(self.sources.get(name, []))
            lines.append(f"   {name} = {self.values[name]}" + (f"　← {src}" if src else ""))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "values": {k: str(v) for k, v in self.values.items()},
            "sources": {k: list(v) for k, v in self.sources.items()},
        }


# ============================================================================
# 计数基准：basis -> 数量
# ============================================================================
def basis_count(world, basis: str, facility=None, op=None) -> Decimal:
    """按计数基准数出"每有 N 个什么"的 N。

    `facility` / `op` 只在"该宿舍…"类基准（`dorm_level`/`dorm_unfull`/`dorm_others`）需要。

    ⚠️ 口径说明（上游只给技能描述、未给公式实现，故以下均在注释中声明取法）：
      - `dorm_operator`：按**全部宿舍的进驻人数合计**计（上游写「宿舍内每有 1 名干员」，
        未指明是提供者所在宿舍还是全部宿舍；此处取全基建口径）。
      - `recruit_slot`：按**人力办公室等级**计（依据 寻同路人 原文
        「每个招募位（不包含初始招募位）= 办公室等级」）。
      - `sui_non_dorm`：进驻在「宿舍与活动室以外设施」的「岁」干员数，上限 5。
      - `dorm_others`：目标所在宿舍内**除目标自己以外**的干员数
        （依据 「独处」原文「如果该宿舍内有其他干员，则每人额外为自身心情每小时恢复+0.05」）。
    """
    if basis == "flat" or not basis:
        return Decimal("1")
    if basis == "dorm_operator":
        return Decimal(sum(len(d.operators) for d in world.all_dormitories()))
    if basis == "recruit_slot":
        office = world.get_facility(FacilityType.OFFICE)
        return Decimal(office.level if office else 0)
    if basis == "sui_non_dorm":
        return Decimal(min(_count_in_facilities(world, "岁"), 5))
    if basis == "abyssal_non_dorm":
        # 排除技能持有者自身：歌蕾蒂娅自己就在控制中枢（宿舍以外），
        # 若把她算进去则「每有」恒 ≥1、潮汐守望的「反之」分支永不可达（见 skills 里的说明）。
        return Decimal(_count_in_facilities(world, "深海猎人", exclude=op))
    # —— 以下需要目标设施 / 目标干员 ——
    if basis == "power_count":
        return Decimal(world.count_of_type(FacilityType.POWER))
    if basis == "dorm_level":
        return Decimal(facility.level if facility is not None else 0)
    if basis == "dorm_unfull":
        if facility is None:
            return Decimal("0")
        from .config import MOOD_MAX
        return Decimal(sum(1 for o in facility.operators if o.mood < MOOD_MAX))
    if basis == "dorm_others":
        if facility is None:
            return Decimal("0")
        return Decimal(sum(1 for o in facility.operators if o is not op))
    return Decimal("1")


def _count_in_facilities(world, faction: str, exclude=None) -> int:
    """「宿舍/活动室以外设施」里某阵营的进驻干员数（上限由调用方处理）。

    `exclude`：排除的干员（通常是技能持有者自身）。
    """
    from .skills import _factions_of
    count = 0
    for f in world.facilities:
        if f.ftype in (FacilityType.DORMITORY, FacilityType.PRIVATE):
            continue
        for o in f.operators:
            if o is exclude:
                continue
            if faction in _factions_of(o):
                count += 1
    return count


def collect_variables(world, producers=None) -> VariableLedger:
    """按产出者表收集基建级变量快照。

    `producers`：[(skill_id, clause, 变量名, 值, 基准, 条件, 技能名, 持有者)]，
    缺省用 `skills_data.VARIABLE_PRODUCERS`。

    产出者必须①在布局里、②已解锁（精英/等级门槛）、③未红脸（`mood > 0`）、
    ④条件成立（如夕「不以物喜」要求自身心情 < 12，读当前快照）。

    注：持有者信息来自 `operators.txt` 全量映射，因此**纯变量技能**（非心情类，
    如祐天寺若麦「勤学苦练」）也能正常产出。
    """
    if producers is None:
        from .skills_data import VARIABLE_PRODUCERS
        producers = VARIABLE_PRODUCERS

    by_name = {op.name: op for op in world.all_operators()}
    lg = VariableLedger()
    for entry in producers:
        skill_id, clause, variable, value, basis, condition, skill_name, holders = entry
        for op_name, unlock_elite, unlock_level in holders:
            op = by_name.get(op_name)
            if op is None:                      # 不在布局里
                continue
            if op.elite < unlock_elite or op.level < unlock_level:
                continue                        # 未解锁
            if op.mood <= ZERO:                 # 红脸：所有技能失效
                continue
            if condition == "mood_below_12" and not (op.mood < Decimal("12")):
                continue
            if condition == "mood_above_12" and not (op.mood > Decimal("12")):
                continue
            count = basis_count(world, basis)
            label = f"{op_name}「{skill_name}」"
            if basis != "flat":
                label += f"（{BASIS_DOC.get(basis, basis)} × {count}）"
            lg.add(variable, to_decimal(value) * count, label)
    return lg


def mood_drop(op) -> Decimal:
    """某干员的「心情落差」= 心情上限 − 当前心情（上游术语 `cc.bd.costdrop`）。"""
    from .config import MOOD_MAX
    return MOOD_MAX - op.mood


__all__ = [
    "VARIABLES", "MOOD_RELEVANT_VARIABLES", "DERIVED_MOOD_DROP", "BASIS_DOC",
    "PRODUCER_CONDITIONS", "VariableLedger", "basis_count", "collect_variables", "mood_drop",
]
