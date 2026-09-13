"""mood_soc/simulator.py —— 时间步进模拟器。

rules.py 提供的是"某时刻、某布局下"的瞬时速率（假设速率恒定）；
但现实中干员心情可能在中途耗尽（红脸 -> 技能失效），从而改变速率。
本模块按"安时积分法"逐步推进时间，每步重新计算所有干员的速率，
从而正确处理这类时变情况，并给出目标干员的心情轨迹。

与 remaining_mood_after / remaining_work_hours 的区别：
  - 后者是"解析解"（速率恒定假设，其余干员心情无限），快速且够用；
  - 本模拟器是"数值解"，允许所有干员心情联动变化，更接近真实。

数值使用 decimal.Decimal，逐步积分不引入二进制浮点误差。
"""
from __future__ import annotations

import copy
from decimal import Decimal
from typing import List, Tuple

from .battery import ampere_hour_integration, to_decimal
from .config import MOOD_MAX
from .models import BaseLayout
from .rules import compute_net_rate


def simulate(world: BaseLayout, operator_name: str, duration,
             step=Decimal("0.05")) -> List[Tuple[Decimal, Decimal]]:
    """模拟 duration 小时内所有干员的心情变化，返回目标干员的 (时间, 心情) 轨迹。

    参数：
        world         基建布局（会被深拷贝，不修改调用方的对象）
        operator_name 目标干员
        duration      总时长（小时）
        step          步长（小时），越小越精确
    返回：
        [(t0, mood0), (t1, mood1), ...] 轨迹点列表。
    """
    if operator_name not in {o.name for o in world.all_operators()}:
        raise KeyError(f"基建布局中不存在干员：{operator_name}")

    duration = to_decimal(duration)
    step = to_decimal(step)

    world = copy.deepcopy(world)
    steps = max(1, int(round(duration / step)))
    dt = duration / Decimal(steps)
    trajectory: List[Tuple[Decimal, Decimal]] = []

    for i in range(steps + 1):
        t = dt * i
        target = world.get_operator(operator_name)
        trajectory.append((t, target.mood))
        if i == steps:
            break
        # 每步先统一算速率，再统一更新，避免先后顺序影响结果
        rates = {o.name: compute_net_rate(world, o.name) for o in world.all_operators()}
        for o in world.all_operators():
            o.mood = ampere_hour_integration(o.mood, rates[o.name], dt, MOOD_MAX)

    return trajectory
