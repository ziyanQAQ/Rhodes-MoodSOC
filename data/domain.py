"""data/domain.py —— **领域基元**：设施类型 + 心情电池的两个端点 + 上游房间登记表。

## 为什么这些常量住在数据包而不是 `mood_soc/config.py`

`data/conditions.py` 与 `data/skill_model.py` 都要用 `FacilityType` / `MOOD_MAX`，
而 `mood_soc/config.py` 属于计算包 —— 一 import 就会连锁 `mood_soc/__init__`
（`from .rules import ...`），把计算层拖进正在初始化的数据包，形成

```
import data.conditions → mood_soc.config → mood_soc/__init__ → rules → skills → data.conditions ✗
```

的循环导入。把"领域基元"下沉到数据包，链条就变成单向：

```
data/domain ← data/skill_model ← data/skills_data ← mood_soc/skills ← mood_soc/rules
     ↑
mood_soc/config（再 re-export 一次，历史 import 路径照旧）
```

**分工**（`mood_soc/config.py` 里剩的是什么）：本模块只放"游戏给的事实"——
设施有哪些、每种能建几间、每级能站几人、心情上下限是多少；
**"怎么算"仍全部在 `mood_soc/config.py`**：基础消耗/减免公式、宿舍回复公式、
中枢折算、设施集合（room1/room2/room3）、建造位总量。

## 数据来源（**上游权威，勿凭印象改**）

`Kengxxiao/ArknightsGameData → zh_CN/gamedata/excel/building_data.json`
（快照 commit `0ef7f952dfd018392200157a5c79a6511ba69122`，客户端 2.7.71）：

| 表 | 上游字段 |
|---|---|
| `FACILITY_MAX_COUNT` | `rooms[roomType].maxCount` |
| `FACILITY_SLOTS_BY_LEVEL` | `rooms[roomType].phases[等级-1].maxStationedNum` |
| `ACTIVITY_ROOM_FACILITIES` | `roomsWithoutRemoveStaff = ["PRIVATE"]` |

复核方式见 `documents/05-数据来源.md`。
"""
from __future__ import annotations

from enum import Enum


# ============================================================================
# 一、心情电池的两个端点
#
# 心情 ⇔ 电池：容量 = MOOD_MAX，SOC = 当前心情，钳位区间 [MOOD_MIN, MOOD_MAX]。
# 数值本身是游戏事实（心情 0~24），放在数据层；怎么积分见 mood_soc/battery.py。
# ============================================================================
MOOD_MAX = 24          # 心情上限：等价于电池满容量（SOC = 100%）
MOOD_MIN = 0           # 心情下限：红脸 / 空电池


# ============================================================================
# 二、设施类型
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
# 三、设施容量与房间数（**上游权威数据**，勿凭印象改）
#
# 来源：building_data.json
#       · rooms[].maxCount                —— 该类型最多可建几个房间
#       · rooms[].phases[lv].maxStationedNum —— 该等级可进驻人数
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


def facility_max_level(ftype) -> int:
    """该设施的最高等级（上游 `rooms[].phases` 的形态数）。"""
    return len(FACILITY_SLOTS_BY_LEVEL.get(ftype) or ()) or 1


# ============================================================================
# 四、默认练度（导入别的工具的文件时用）
#
# **默认口径＝满练**：精英化 2、等级 30。没练满的干员要显式标注，否则技能会被多算。
# 等级只在"等级 30 解锁"这一处被读（上游 `operators.txt` 的 `unlock` 列有 4 条），
# 默认给 30 才与"默认精英化二"的口径一致（30 = 三星机械满级）；
# 给 1 会让杜林/THRM-EX/Lancet-2 的那几条技能默认失效。
# ============================================================================
DEFAULT_ELITE = 2
DEFAULT_OPERATOR_LEVEL = 30


__all__ = [
    "MOOD_MAX",
    "MOOD_MIN",
    "FacilityType",
    "FACILITY_LABELS",
    "FACILITY_TO_ROOM_TYPE",
    "FACILITY_MAX_COUNT",
    "FACILITY_SLOTS_BY_LEVEL",
    "ACTIVITY_ROOM_FACILITIES",
    "DEFAULT_ELITE",
    "DEFAULT_OPERATOR_LEVEL",
    "facility_max_count",
    "facility_slots",
    "facility_max_level",
    "parse_facility_type",
]
