"""mood_soc/models.py —— 领域数据模型（纯数据容器，无业务逻辑）。

只负责描述"干员 / 设施 / 基建布局 / 计算结果"长什么样；
具体怎么算，交给 rules / simulator / battery 等模块，保持高内聚、低耦合。

数值字段统一用 decimal.Decimal，保证十进制精确、可复现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from .config import FacilityType, MOOD_MAX


@dataclass
class Operator:
    """一名干员及其当前状态。

    skill_ids 只存"技能 id"（字符串）而非技能对象，
    这样模型可被深拷贝、可序列化，且技能定义集中在 skills.py。
    """
    name: str                                        # 干员名（唯一标识）
    mood: Decimal = MOOD_MAX                         # 当前心情（SOC，0~24，可含小数）
    skill_ids: List[str] = field(default_factory=list)  # 持有的心情类技能 id
    trait: Optional[str] = None                      # 阵营 / 特性（如"岁"，供定向技能匹配）


@dataclass
class Facility:
    """基建中的一个设施房间。"""
    ftype: FacilityType                              # 设施类型
    level: int = 1                                   # 设施等级
    operators: List[Operator] = field(default_factory=list)  # 进驻的干员
    atmosphere: Optional[Decimal] = None             # 仅宿舍有效：实际氛围；None=按等级满氛围


@dataclass
class BaseLayout:
    """基建布局：整个基建的当前快照（即"世界状态"）。"""
    facilities: List[Facility] = field(default_factory=list)

    # ------------------------------------------------------------------ 查询
    def get_facility(self, ftype) -> Optional[Facility]:
        """按类型取第一个设施（中枢 / 发电 / 会客等通常唯一）。"""
        for f in self.facilities:
            if f.ftype == ftype:
                return f
        return None

    def control_center(self) -> Optional[Facility]:
        """返回控制中枢（可能为 None，若布局里没有）。"""
        return self.get_facility(FacilityType.CONTROL_CENTER)

    def facility_of(self, operator_name: str) -> Optional[Facility]:
        """查找某干员所在的设施。"""
        for f in self.facilities:
            if any(o.name == operator_name for o in f.operators):
                return f
        return None

    def get_operator(self, operator_name: str) -> Optional[Operator]:
        """按名字查找干员。"""
        for f in self.facilities:
            for o in f.operators:
                if o.name == operator_name:
                    return o
        return None

    def all_operators(self) -> List[Operator]:
        """基建内所有干员（扁平化）。"""
        return [o for f in self.facilities for o in f.operators]


@dataclass
class MoodResult:
    """一次测算的汇总结果（single 模式：单个目标干员）。"""
    operator_name: str          # 目标干员名
    facility_label: str         # 所在设施中文名
    initial_mood: Decimal       # 初始心情
    net_rate: Decimal           # 净消耗速率（点 / 时，>0 下降，<0 上升）
    remaining_mood: Decimal     # 目标时段结束后的心情
    sustain_hours: Decimal      # 还能维持/恢复多久（工作=到红脸、宿舍=恢复满心情；Infinity=不变）
    state: str                  # 状态描述（工作中 / 休息中 / 宿舍休息 / 红脸等）


@dataclass
class OperatorResult:
    """基建布局中单个干员的测算结果（base 模式中的一个条目）。"""
    name: str                   # 干员名
    facility_label: str         # 所在设施中文名
    mood: Decimal               # 心情值（0~24，当前或时段推进后）
    sustain_hours: Decimal      # 布局可维持时长（所有干员一致）；Infinity=可持续/无红脸
    mood_at_end: Optional[Decimal] = None   # 到达布局可维持时长那一刻的心情；无结束时刻（无限）为 None


@dataclass
class BaseResult:
    """整个基建布局的测算结果（base 模式）。"""
    operators: List[OperatorResult] = field(default_factory=list)
    layout_sustain_hours: Decimal = Decimal("0")   # 能维持布局（无人红脸）的最长时长；Infinity=可持续
    bottleneck: Optional[str] = None               # 最先红脸的干员名（瓶颈）；None=无瓶颈
