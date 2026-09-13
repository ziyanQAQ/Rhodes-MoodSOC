"""mood_soc/output.py —— 结果序列化与文件输出（输出层）。

把 MoodResult（single）/ BaseResult（base）转成可供外部消费的 JSON 结构（dict），
并提供写入文件的能力。中文文本展示见 report.py，JSON 序列化集中在本模块，保持低耦合。

精度策略（输出边界）：
  - 内部计算全程 Decimal；到这里才做一次受控的"固定小数位舍入"，输出为 JSON 数字；
  - 舍入精度 _ROUND = 0.000001（6 位小数），对心情/速率/小时量级足够、且不产生脏尾数；
  - Decimal('Infinity')（永续/不会红脸）统一输出为 null，便于下游解析。
"""
from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal

from .battery import to_decimal
from .models import BaseResult, MoodResult

# 输出边界：统一舍入到 6 位小数
_ROUND = Decimal("0.000001")


def _num(value):
    """把 Decimal 转成 JSON 数字（按 _ROUND 舍入）；inf -> None；其它类型原样返回。

    整数值会退化为 int（如 24.0 -> 24），非整数用 float 输出（如 36.923077）。
    """
    if isinstance(value, Decimal):
        if value.is_infinite():
            return None
        q = value.quantize(_ROUND, rounding=ROUND_HALF_UP)
        if q == q.to_integral_value():
            return int(q)
        return float(q)
    return value


def mood_result_to_dict(r: MoodResult, period_hours) -> dict:
    """单个干员的测算结果（single 模式）-> JSON dict。"""
    return {
        "mode": "single",
        "operator": r.operator_name,
        "facility": r.facility_label,
        "period_hours": _num(to_decimal(period_hours)),
        "net_rate": _num(r.net_rate),
        "state": r.state,
        # 心情值：目标时段结束后的剩余心情
        "mood": _num(r.remaining_mood),
        # 还能维持/恢复多久：工作=到红脸、宿舍=恢复满心情（null=不变/永续）
        "sustain_hours": _num(r.sustain_hours),
    }


def base_result_to_dict(r: BaseResult, period_hours=None) -> dict:
    """整个基建布局的测算结果（base 模式）-> JSON dict。"""
    if period_hours is None:
        period_hours = Decimal("0")
    return {
        "mode": "base",
        "period_hours": _num(to_decimal(period_hours)),
        # 布局能维持（无人红脸）的最长时长；null=可持续
        "layout_sustain_hours": _num(r.layout_sustain_hours),
        # 最先红脸的瓶颈干员；null=无瓶颈
        "bottleneck": r.bottleneck,
        "operators": [
            {
                "name": o.name,
                "facility": o.facility_label,
                "mood": _num(o.mood),
                # 布局可维持时长（所有干员一致）
                "sustain_hours": _num(o.sustain_hours),
                # 到达该时刻时该干员的心情；null=无结束时刻（无限）
                "mood_at_end": _num(o.mood_at_end),
            }
            for o in r.operators
        ],
    }


def to_json_string(data: dict) -> str:
    """把 dict 序列化为可读的 JSON 字符串（中文不转义）。"""
    return json.dumps(data, ensure_ascii=False, indent=2)


def dump_json(data: dict, path: str) -> str:
    """把 dict 以 JSON 写入文件（自动创建父目录），返回绝对路径。"""
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return abs_path
