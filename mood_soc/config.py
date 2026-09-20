"""mood_soc/config.py —— **计算规则常量与公式** + 领域基元的字段出口。

## 这一层是什么

上半部分的"领域基元"（设施类型 / 容量 / 心情上下限 / 默认练度）已下沉到
`data/domain.py`（游戏给的事实归数据包），本模块只 re-export 一次，
历史写法 `from mood_soc.config import FacilityType` 继续可用。

本模块**自己负责**的是"怎么算"：

| 内容 | 依据 |
|---|---|
| 基础消耗 `BASE_CONSUMPTION`（1.0/h）与挂件位 0/h | `resources/心情消耗回复和工休时间.docx` 第 4 段 + 用户口径 |
| 制造站/贸易站的按人数减免（−0.05/人，上限 −0.1） | docx（灰色字体部分） |
| 控制中枢全局减免（满员 −0.25，按人数线性折算） | docx + 上游 `controlData.basicCostBuff = -5` |
| 宿舍回复 `1.5 + 0.1×等级 + 0.0004×氛围` | docx |
| 设施集合 room1 / room2 / room3 | 上游 `gamedata_const.json → termDescriptionDict` |
| 建造位总量（OUTPUT = 9） | 上游 `layouts.v0.slots` |

数值与规则的完整口径见 `documents/02-数值规则.md`。

精度策略：本模块的数值常量一律 `decimal.Decimal`，做十进制精确运算；
导入时统一设置全局 Decimal 上下文（28 位有效数字、四舍五入）。
"""
from decimal import ROUND_HALF_UP, Decimal, getcontext

#: 本项目的**规范 Decimal 上下文**：28 位有效数字 + 四舍五入（保证除法结果确定且精度足够）。
DECIMAL_PREC = 28
DECIMAL_ROUNDING = ROUND_HALF_UP


def use_project_decimal_context() -> None:
    """把**当前线程**的 Decimal 上下文设成本项目的规范值（幂等、极便宜）。

    ⚠️ 为什么必须有它（2026-09 踩到）：`decimal` 的上下文是**线程局部**的，模块 import 时
    这行只设到"导入它的那个线程"。引擎一旦在**别的线程**里跑（界面的异步重算 P5），
    新线程拿到的是 Python 默认上下文（`prec=28` 但舍入是 `ROUND_HALF_EVEN`）——
    末位会差 1e-26，再经"跨阈值吸附"连锁改变事件时刻：实测**同一份输入**主线程与工作线程
    算出来的轨迹在 52 名干员上不同。所以引擎的入口都要先调一下它。
    """
    ctx = getcontext()
    ctx.prec = DECIMAL_PREC
    ctx.rounding = DECIMAL_ROUNDING


use_project_decimal_context()

from data.domain import (  # noqa: E402  （放在设置 Decimal 上下文之后）
    ACTIVITY_ROOM_FACILITIES,
    DEFAULT_ELITE,
    DEFAULT_OPERATOR_LEVEL,
    FACILITY_LABELS,
    FACILITY_MAX_COUNT,
    FACILITY_SLOTS_BY_LEVEL,
    FACILITY_TO_ROOM_TYPE,
    MOOD_MAX,
    MOOD_MIN,
    FacilityType,
    facility_max_count,
    facility_max_level,
    facility_slots,
    parse_facility_type,
)


# ============================================================================
# 一、干员工作时的基础心情消耗速率（点 / 小时）
# ============================================================================
BASE_CONSUMPTION = Decimal("1")

# 控制中枢：最多可进驻的人数（与 data.domain.FACILITY_SLOTS_BY_LEVEL 的最高级一致）
CC_SLOTS = 5
# 中枢放满干员后，全局获得的心情消耗减免
CC_REDUCTION_FULL = Decimal("0.25")


# ============================================================================
# 二、min_level_for_slots：塞进 N 个人至少需要几级
# ============================================================================
def min_level_for_slots(ftype, count: int) -> int:
    """要塞进 `count` 个人，**至少**需要几级（放不下就返回最高等级）。

    用途：MAA 排班文件**不带房间等级**，导入后按"实际放了几个人"反推最低可行等级，
    免得出现"Lv1 容量 1 却站着 5 个人"这种自相矛盾的布局。
    """
    want = max(0, int(count))
    for lv in range(1, facility_max_level(ftype) + 1):
        if facility_slots(ftype, lv) >= want:
            return lv
    return facility_max_level(ftype)


# ============================================================================
# 三、建造位总量（上游 `layouts.v0.slots` 的 `category == "OUTPUT"` 槽位）
#
# 上游事实（building_data.json，客户端 2.7.71）：
#   · `rooms.MANUFACTURE.maxCount = 5`、`rooms.TRADING.maxCount = 5`、`rooms.POWER.maxCount = 3`；
#   · `layouts.v0.slots` 里 `category = "OUTPUT"` 的槽位**正好 9 个** ——
#     制造站 / 贸易站 / 发电站**共用这 9 个建造位**，所以三者总数不能超过 9。
# ============================================================================
OUTPUT_ROOM_TYPES = (FacilityType.MANUFACTURING, FacilityType.TRADING, FacilityType.POWER)
OUTPUT_SLOT_TOTAL = 9


# ============================================================================
# 四、各设施的基础心情消耗速率（点 / 小时）
#
# 口径（docx 第 4 段「干员工作时…每小时基础消耗速率 1 点」）：
#   · **常规生产设施 = 1.0/h**：制造站 / 贸易站 / 发电站 / 办公室 / 会客室 / 控制中枢；
#   · **挂件位 = 0/h**：加工站、训练室（下条详述）；
#   · 宿舍 0（只回复）、活动室 0（不视作入住在基建内）。
#   旧实现只排除宿舍、其余一律 1.0，把加工站/训练室算成 0.75/h（已修）。
# ============================================================================
# 「挂件位」：加工站的加工位、训练室的教练位 —— 不计算心情消耗（用户口径）。
#
# 这两个位置的**实际用法都是放挂件**：挂件干员本身没有与所在设施相关的心情技能，
# 被放进来的唯一目的是「**人在基建内**」——好让别人的计数类技能（基建内每有 1 名
# XX 干员，且**不含副手与活动室使用者**，见 `models.BaseLayout.base_operators`）
# 能数到它。挂件不需要休息，所以给它们算心情消耗没有意义。
#
# 推论：那 9 条写「进驻训练室协助位时，心情每小时消耗 +1」的训练室技能**不生效**
# （已从 `data/moods_skills.txt` 撤出，`data/skills_registry.txt` 保留登记与原因）。
#
# 与 docx 第 4 段**不冲突**：那说的是常规生产设施"上岗生产"的稳态消耗，
# 挂件位不属于上岗生产。
TRAINING_BASE_CONSUMPTION = Decimal("0")

BASE_CONSUMPTION_BY_FACILITY = {
    FacilityType.CONTROL_CENTER: BASE_CONSUMPTION,
    FacilityType.MANUFACTURING: BASE_CONSUMPTION,
    FacilityType.TRADING: BASE_CONSUMPTION,
    FacilityType.POWER: BASE_CONSUMPTION,
    FacilityType.RECEPTION: BASE_CONSUMPTION,
    FacilityType.OFFICE: BASE_CONSUMPTION,
    FacilityType.TRAINING: TRAINING_BASE_CONSUMPTION,
    FacilityType.WORKSHOP: Decimal("0"),        # 挂件位；且配方心情消耗是按次
    FacilityType.DORMITORY: Decimal("0"),       # 宿舍只回复
    FacilityType.PRIVATE: Decimal("0"),         # 活动室不视作入住在基建内
}


def base_consumption(ftype) -> Decimal:
    """设施的基础心情消耗速率（点/小时）。未收录类型按通用基础值 1.0。"""
    return BASE_CONSUMPTION_BY_FACILITY.get(ftype, BASE_CONSUMPTION)


# ============================================================================
# 五、制造站 / 贸易站的设施基础心情减免（文档中的"X"，灰色字体部分）
#
# 规则：根据当前进驻人数，
#   1 名 -> 无减免（不显示）
#   2 名 -> 0.05
#   3 名 -> 0.1
# 即每多进驻 1 人，多减 0.05，上限 0.1。
# ============================================================================
FACILITY_REDUCTION_STEP = Decimal("0.05")
FACILITY_REDUCTION_MAX = Decimal("0.1")
# 享受"按进驻人数减免"的设施类型（仅制造站 / 贸易站；
# 发电站、会客、办公、专精等无此减免）
REDUCTION_BY_STAFF_TYPES = (FacilityType.MANUFACTURING, FacilityType.TRADING)


def facility_mood_reduction(ftype, operator_count):
    """设施基础心情减免 X：仅制造站 / 贸易站，按当前进驻人数计算。"""
    if ftype not in REDUCTION_BY_STAFF_TYPES:
        return Decimal("0")
    extra = max(0, int(operator_count) - 1)
    return min(FACILITY_REDUCTION_STEP * extra, FACILITY_REDUCTION_MAX)


# ============================================================================
# 六、设施集合（权威来源：官方术语表 gamedata_const.json → termDescriptionDict）
#   cc.c.room1「部分设施」  = 发电站、人力办公室、会客室
#   cc.c.room2「其他设施」  = room1 + 制造站 + 贸易站
#   cc.c.room3「工作场所」  = room2 + 控制中枢 + 训练室
# 注：scripts/generate_skills_data.py 里有同名的字符串版本（生成器不能 import
#     mood_soc 包，见该脚本的引导死锁注释）；两处必须保持一致。
# ============================================================================
PARTIAL_WORK_FACILITIES = (FacilityType.POWER, FacilityType.OFFICE, FacilityType.RECEPTION)
WORK_FACILITIES = (FacilityType.POWER, FacilityType.MANUFACTURING, FacilityType.TRADING,
                   FacilityType.OFFICE, FacilityType.RECEPTION)
ALL_WORKPLACE_FACILITIES = WORK_FACILITIES + (FacilityType.CONTROL_CENTER, FacilityType.TRAINING)


def cc_reduction(cc_operator_count):
    """控制中枢全局心情减免：按中枢进驻人数线性折算，满员（5 人）= 0.25。

    文档只给出"放满 -> 0.25"这一锚点；这里采用"按人数线性折算"的建模假设
    （0 人 -> 0，5 人 -> 0.25），并在注释中显式声明。
    上游佐证：building_data.json 的 controlData.basicCostBuff = -5（每人 -0.05），
    与 5 人满员 -0.25 完全一致（进驻人数为整数，两种写法等价）。
    """
    count = min(max(int(cc_operator_count), 0), CC_SLOTS)
    ratio = Decimal(count) / Decimal(CC_SLOTS)
    return CC_REDUCTION_FULL * ratio


# ============================================================================
# 七、宿舍（回复）
#
# 宿舍回复 = 白字（宿舍等级）+ 绿字（氛围 + 干员后勤技能）
#   白字 = 1.5 + 0.1 × 宿舍等级
#   绿字氛围部分 = 0.0004 × 实际宿舍氛围
# 等级同时决定基础回复与氛围上限，故"白字 + 绿字中氛围部分"为固定值。
# ============================================================================
DORM_LEVEL_TABLE = {
    1: {"atmosphere_max": 1000, "fixed_recovery": Decimal("2.0")},
    2: {"atmosphere_max": 2000, "fixed_recovery": Decimal("2.5")},
    3: {"atmosphere_max": 3000, "fixed_recovery": Decimal("3.0")},
    4: {"atmosphere_max": 4000, "fixed_recovery": Decimal("3.5")},
    5: {"atmosphere_max": 5000, "fixed_recovery": Decimal("4.0")},
}

DORM_BASE_RECOVERY_BASE = Decimal("1.5")      # 白字基础值
DORM_BASE_RECOVERY_PER_LEVEL = Decimal("0.1")  # 白字随等级的增量
DORM_ATMOSPHERE_COEF = Decimal("0.0004")       # 绿字氛围系数


def dormitory_recovery(level, atmosphere=None):
    """宿舍基础回复（不含干员技能）：1.5 + 0.1×等级 + 0.0004×实际氛围。

    atmosphere 为 None 时，默认按该等级的满氛围（氛围上限）计算。
    """
    info = DORM_LEVEL_TABLE.get(level, DORM_LEVEL_TABLE[1])
    if atmosphere is None:
        atmosphere = info["atmosphere_max"]
    else:
        atmosphere = Decimal(str(atmosphere))
    return (DORM_BASE_RECOVERY_BASE
            + DORM_BASE_RECOVERY_PER_LEVEL * level
            + DORM_ATMOSPHERE_COEF * atmosphere)


__all__ = [
    # —— 领域基元（来自 data.domain，re-export 保持历史 import 路径）——
    "MOOD_MAX", "MOOD_MIN", "FacilityType",
    "FACILITY_LABELS", "FACILITY_TO_ROOM_TYPE",
    "FACILITY_MAX_COUNT", "FACILITY_SLOTS_BY_LEVEL", "ACTIVITY_ROOM_FACILITIES",
    "DEFAULT_ELITE", "DEFAULT_OPERATOR_LEVEL",
    "facility_max_count", "facility_slots", "facility_max_level", "parse_facility_type",
    "min_level_for_slots",
    # —— 计算规则 ——
    "BASE_CONSUMPTION", "CC_SLOTS", "CC_REDUCTION_FULL",
    "OUTPUT_ROOM_TYPES", "OUTPUT_SLOT_TOTAL",
    "TRAINING_BASE_CONSUMPTION", "BASE_CONSUMPTION_BY_FACILITY", "base_consumption",
    "FACILITY_REDUCTION_STEP", "FACILITY_REDUCTION_MAX", "REDUCTION_BY_STAFF_TYPES",
    "facility_mood_reduction",
    "PARTIAL_WORK_FACILITIES", "WORK_FACILITIES", "ALL_WORKPLACE_FACILITIES", "cc_reduction",
    "DORM_LEVEL_TABLE", "DORM_BASE_RECOVERY_BASE", "DORM_BASE_RECOVERY_PER_LEVEL",
    "DORM_ATMOSPHERE_COEF", "dormitory_recovery",
]
