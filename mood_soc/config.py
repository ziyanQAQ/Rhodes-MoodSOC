"""mood_soc/config.py —— 全局常量与数据表（纯配置层，无业务逻辑）。

集中管理所有"魔法数字"，其余模块只从这里读取、不自行硬编码，
从而便于统一调整，也符合"低耦合"的设计原则。

数值与规则来源：《resources/心情消耗回复和工休时间.docx》。

精度策略：所有数值常量均为 decimal.Decimal，做十进制精确运算；
本模块导入时统一设置全局 Decimal 上下文（28 位有效数字、四舍五入）。
"""
from decimal import ROUND_HALF_UP, Decimal, getcontext
from enum import Enum

# 全局十进制上下文：28 位有效数字 + 四舍五入，保证除法结果确定且精度足够。
getcontext().prec = 28
getcontext().rounding = ROUND_HALF_UP


# ============================================================================
# 一、心情电池基本参数
# ============================================================================
MOOD_MAX = Decimal("24")          # 心情上限：等价于电池满容量（SOC = 100%）
MOOD_MIN = Decimal("0")           # 心情下限：红脸 / 空电池

# 干员工作时、无任何增减情况下的基础心情消耗速率（点 / 小时）
BASE_CONSUMPTION = Decimal("1")


# ============================================================================
# 二、控制中枢（简称"中枢"）
# ============================================================================
CC_SLOTS = 5                    # 中枢最多可进驻的干员数量
CC_REDUCTION_FULL = Decimal("0.25")  # 中枢放满干员后，全局获得的心情消耗减免


# ============================================================================
# 三、设施类型
# ============================================================================
class FacilityType(str, Enum):
    """设施类型。value 使用 ascii 便于序列化；中文名见 FACILITY_LABELS。"""
    CONTROL_CENTER = "control_center"   # 控制中枢
    MANUFACTURING = "manufacturing"     # 制造站
    TRADING = "trading"                 # 贸易站
    POWER = "power"                     # 发电站
    RECEPTION = "reception"             # 会客室
    OFFICE = "office"                   # 办公室
    TRAINING = "training"               # 训练室
    WORKSHOP = "workshop"               # 加工站
    DORMITORY = "dormitory"             # 宿舍


FACILITY_LABELS = {
    FacilityType.CONTROL_CENTER: "控制中枢",
    FacilityType.MANUFACTURING: "制造站",
    FacilityType.TRADING: "贸易站",
    FacilityType.POWER: "发电站",
    FacilityType.RECEPTION: "会客室",
    FacilityType.OFFICE: "办公室",
    FacilityType.TRAINING: "训练室",
    FacilityType.WORKSHOP: "加工站",
    FacilityType.DORMITORY: "宿舍",
}

# 中文名 -> 类型，用于解析用户输入
_FACILITY_BY_LABEL = {label: t for t, label in FACILITY_LABELS.items()}


def parse_facility_type(name):
    """把字符串（英文枚举值或中文名）解析为 FacilityType，失败返回 None。"""
    if isinstance(name, FacilityType):
        return name
    key = str(name).strip()
    try:
        return FacilityType(key)
    except ValueError:
        return _FACILITY_BY_LABEL.get(key)


# ============================================================================
# 四、制造站 / 贸易站的设施基础心情减免（文档中的"X"，灰色字体部分）
#
# 规则：根据当前制造站进驻人数，
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


def cc_reduction(cc_operator_count):
    """控制中枢全局心情减免：按中枢进驻人数线性折算，满员（5 人）= 0.25。

    文档只给出"放满 -> 0.25"这一锚点；这里采用"按人数线性折算"的建模假设
    （0 人 -> 0，5 人 -> 0.25），并在注释中显式声明。
    """
    count = min(max(int(cc_operator_count), 0), CC_SLOTS)
    ratio = Decimal(count) / Decimal(CC_SLOTS)
    return CC_REDUCTION_FULL * ratio


# ============================================================================
# 五、宿舍（回复）
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
