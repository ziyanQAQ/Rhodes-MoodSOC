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
    """设施类型。value 使用 ascii 便于序列化；中文名见 FACILITY_LABELS。

    与上游 `building_data.json → rooms` 的 roomType 一一对应（见 FACILITY_TO_ROOM_TYPE）。
    """
    CONTROL_CENTER = "control_center"   # 控制中枢
    MANUFACTURING = "manufacturing"     # 制造站
    TRADING = "trading"                 # 贸易站
    POWER = "power"                     # 发电站
    RECEPTION = "reception"             # 会客室
    OFFICE = "office"                   # 办公室（上游 roomType = HIRE）
    TRAINING = "training"               # 训练室
    WORKSHOP = "workshop"               # 加工站
    DORMITORY = "dormitory"             # 宿舍
    PRIVATE = "private"                 # 活动室（上游 roomType = PRIVATE，category = CUSTOM_P）


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
    FacilityType.PRIVATE: "活动室",
}

# 本项目设施枚举 ↔ 上游 roomType（用于对照上游 rooms / 技能 roomType 字段）
FACILITY_TO_ROOM_TYPE = {
    FacilityType.CONTROL_CENTER: "CONTROL",
    FacilityType.MANUFACTURING: "MANUFACTURE",
    FacilityType.TRADING: "TRADING",
    FacilityType.POWER: "POWER",
    FacilityType.RECEPTION: "MEETING",
    FacilityType.OFFICE: "HIRE",
    FacilityType.TRAINING: "TRAINING",
    FacilityType.WORKSHOP: "WORKSHOP",
    FacilityType.DORMITORY: "DORMITORY",
    FacilityType.PRIVATE: "PRIVATE",
}

# ============================================================================
# 设施容量（**上游权威数据**，勿凭印象改）
#
# 来源：Kengxxiao/ArknightsGameData → zh_CN/gamedata/excel/building_data.json
#       · rooms[roomType].maxCount                —— 该类型最多可建几个房间
#       · rooms[roomType].phases[等级-1].maxStationedNum —— 该等级可进驻人数
# 快照 commit：0ef7f952dfd018392200157a5c79a6511ba69122（客户端 2.7.71）
#
# 复核方式（AGENTS.md §11）：
#   python -c "import json;d=json.load(open('…/building_data.json',encoding='utf-8'));
#              print({k:(v['maxCount'],[p['maxStationedNum'] for p in v['phases']])
#                     for k,v in d['rooms'].items()})"
# ============================================================================
FACILITY_MAX_COUNT = {
    FacilityType.CONTROL_CENTER: 1,
    FacilityType.MANUFACTURING: 5,
    FacilityType.TRADING: 5,
    FacilityType.POWER: 3,
    FacilityType.RECEPTION: 1,
    FacilityType.OFFICE: 1,
    FacilityType.TRAINING: 1,
    FacilityType.WORKSHOP: 1,
    FacilityType.DORMITORY: 4,
    FacilityType.PRIVATE: 6,          # 活动室：最多 6 间，但不能进驻（maxStationedNum = 0）
}

FACILITY_SLOTS_BY_LEVEL = {
    FacilityType.CONTROL_CENTER: (1, 2, 3, 4, 5),
    FacilityType.MANUFACTURING: (1, 2, 3),
    FacilityType.TRADING: (1, 2, 3),
    FacilityType.POWER: (1, 1, 1),
    FacilityType.RECEPTION: (2, 2, 2),
    FacilityType.OFFICE: (1, 1, 1),
    FacilityType.TRAINING: (2, 2, 2),   # ①训练位 + ②协助位
    FacilityType.WORKSHOP: (1, 1, 1),
    FacilityType.DORMITORY: (5, 5, 5, 5, 5),
    FacilityType.PRIVATE: (0, 0, 0),    # 活动室不可进驻
}

# 上游 `roomsWithoutRemoveStaff = ["PRIVATE"]`：活动室的使用者不会被"撤下干员"逻辑移除，
# 且在多数技能里被**显式排除**（「基建内（不包含副手及活动室使用者）」出现 23 次）。
ACTIVITY_ROOM_FACILITIES = (FacilityType.PRIVATE,)


def facility_max_count(ftype) -> int:
    """该设施类型最多可建几个房间（-1/未收录视为不限）。"""
    return FACILITY_MAX_COUNT.get(ftype, -1)


def facility_slots(ftype, level: int = 1) -> int:
    """该设施在指定等级可进驻的人数（超出等级上限时取最后一个）。"""
    table = FACILITY_SLOTS_BY_LEVEL.get(ftype)
    if not table:
        return 0
    idx = min(max(int(level), 1), len(table)) - 1
    return table[idx]


# ============================================================================
# 各设施的基础心情消耗速率（点 / 小时）
#
# ⚠️ 与 docx 的基本口径一致：工作设施 1.0/h，宿舍 0（宿舍只回复）。
#    但**加工站是例外**：加工站的心情是"按次消耗"（配方心情消耗，见
#    resources/skills_registry.txt 的 A2/X08 共 34 条 buff），不搓材料就是 0/h。
#    旧实现只排除宿舍、其余一律 1.0，导致加工站/训练室被算成 0.75/h（已修）。
#
# ⚠️ 训练室的 1.0 是**待确认口径**（见 TRAINING_BASE_CONSUMPTION）。
# ============================================================================
# 训练室协助位的基础消耗。上游没有公式实现（纯数据 dump），docx 也未写明。
# 那 9 条训练室技能写的是「进驻训练室协助位时，心情每小时消耗+1」——
# 与本项目其它设施一致的解读是「+1 是增量」（如制造站「-0.25」也是增量），
# 故此处取 1.0，技能生效后合计 2.0/h。**待人工确认**。
TRAINING_BASE_CONSUMPTION = Decimal("1")

BASE_CONSUMPTION_BY_FACILITY = {
    FacilityType.CONTROL_CENTER: BASE_CONSUMPTION,
    FacilityType.MANUFACTURING: BASE_CONSUMPTION,
    FacilityType.TRADING: BASE_CONSUMPTION,
    FacilityType.POWER: BASE_CONSUMPTION,
    FacilityType.RECEPTION: BASE_CONSUMPTION,
    FacilityType.OFFICE: BASE_CONSUMPTION,
    FacilityType.TRAINING: TRAINING_BASE_CONSUMPTION,
    FacilityType.WORKSHOP: Decimal("0"),        # 按次消耗，不是每小时
    FacilityType.DORMITORY: Decimal("0"),       # 宿舍只回复
    FacilityType.PRIVATE: Decimal("0"),         # 活动室不消耗
}


def base_consumption(ftype) -> Decimal:
    """设施的基础心情消耗速率（点/小时）。未收录类型按通用基础值 1.0。"""
    return BASE_CONSUMPTION_BY_FACILITY.get(ftype, BASE_CONSUMPTION)


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


# ============================================================================
# 设施集合（权威来源：官方术语表 gamedata_const.json → termDescriptionDict）
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
