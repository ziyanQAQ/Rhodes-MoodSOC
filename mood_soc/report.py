"""mood_soc/report.py —— 结果格式化输出（展示层）。

把 MoodResult 等数据整理成通俗易懂的中文文本，供命令行或其它界面直接展示。
"""
from __future__ import annotations

from .battery import INF
from .models import MoodResult


def _fmt_hours(h) -> str:
    """把小时数（Decimal）格式化为人类可读文本。"""
    if h == INF:
        return "无限（永续，心情不会耗尽）"
    return f"{h:.2f} 小时"


def _fmt_mood(m) -> str:
    """心情显示：游戏内向下取整（Decimal 用 math.floor 也能得到整数），同时给出真实小数。"""
    import math
    return f"{math.floor(m)}（实际 {m:.3f}）"


def render_result(r: MoodResult, period_hours) -> str:
    """渲染单次测算结果。"""
    lines = [
        "=" * 52,
        f"目标干员：{r.operator_name}",
        f"所在设施：{r.facility_label}",
        f"初始心情：{_fmt_mood(r.initial_mood)}",
        f"净心情速率：{r.net_rate:+.4f} 点/小时（{'下降' if r.net_rate > 0 else ('上升' if r.net_rate < 0 else '不变')}）",
        f"当前状态：{r.state}",
        "-" * 52,
        f"目标时段 {period_hours:.2f} 小时后的剩余心情：{_fmt_mood(r.remaining_mood)}",
        f"目标时段结束后，其余干员心情无限时，该干员还能维持/恢复：{_fmt_hours(r.sustain_hours)}",
        "=" * 52,
    ]
    return "\n".join(lines)


def render_trajectory(trajectory) -> str:
    """渲染心情轨迹（采样显示）。"""
    lines = ["心情轨迹（时间 / 心情）："]
    for t, m in trajectory:
        lines.append(f"  t = {t:6.2f} h  心情 = {m:6.3f}")
    return "\n".join(lines)
