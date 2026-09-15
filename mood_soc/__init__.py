"""mood_soc —— 基于 BMS-SOC（安时积分法）的"心情电池"建模工具。

将《明日方舟》基建中的干员"心情"抽象为一块电池：
  - 容量 = 心情上限 24
  - SOC  = 当前心情
  - 放电 = 工作时的净心情消耗
  - 充电 = 宿舍 / 回复技能带来的心情回复

对外暴露的常用入口：
  - build_base_layout(data)      从字典构建基建布局
  - evaluate(world, name, hours) 单个干员测算（single 模式）
  - evaluate_base(world, hours)  整个基建布局测算（base 模式）
  - compute_net_rate / remaining_mood_after / remaining_work_hours  底层查询
  - mood_ledger(world, name)     **心情流水账**：逐条来源 + 轴 F 叠加规则（.explain() 可读）
  - work_rest_ratio(x, y)        工休比指标
  - simulate(...)                时间步进模拟（处理红脸等时变情况）
  - mood_soc.output.*            结果 -> JSON dict / 写入文件
"""
from .battery import INF, MoodBattery, ampere_hour_integration, to_decimal
from .config import FacilityType, MOOD_MAX, parse_facility_type
from .ledger import Bucket, Contribution, MoodLedger
from .models import BaseLayout, BaseResult, Facility, MoodResult, Operator, OperatorResult
from .rules import (
    compute_consumption,
    compute_net_rate,
    compute_recovery,
    consume_ledger,
    entry_event_holders,
    evaluate,
    evaluate_base,
    apply_entry_events,
    entry_target_kind,
    find_entry_target,
    mood_ledger,
    recovery_ledger,
    remaining_mood_after,
    remaining_work_hours,
    reset_entry_events,
    apply_idle_to_dorm,
    time_to_mood,
    work_rest_ratio,
)
from .scenario import build_base_layout
from .simulator import simulate

__all__ = [
    "INF",
    "MoodBattery",
    "ampere_hour_integration",
    "to_decimal",
    "FacilityType",
    "MOOD_MAX",
    "parse_facility_type",
    "BaseLayout",
    "BaseResult",
    "Facility",
    "MoodResult",
    "Operator",
    "OperatorResult",
    "Bucket",
    "Contribution",
    "MoodLedger",
    "build_base_layout",
    "compute_consumption",
    "compute_net_rate",
    "compute_recovery",
    "consume_ledger",
    "evaluate",
    "evaluate_base",
    "apply_entry_events",
    "apply_idle_to_dorm",
    "entry_event_holders",
    "entry_target_kind",
    "find_entry_target",
    "mood_ledger",
    "recovery_ledger",
    "remaining_mood_after",
    "remaining_work_hours",
    "reset_entry_events",
    "simulate",
    "time_to_mood",
    "work_rest_ratio",
]
