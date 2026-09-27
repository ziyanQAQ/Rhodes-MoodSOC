"""mood_soc/facility_count.py —— **设施数量修正**的求值（纯计算，无 IO）。

上游有两条技能的**唯一效果**是改设施数量（原文与出处见 `data/facility_count.py`）：

    森蚺「我寻思能行」：控制中枢 + Lancet-2 在发电站   ⇒ 发电站 +2（仅影响设施数量）
    承曦格雷伊「晨曦」：发电站 + 其他发电站无作业平台   ⇒ 发电站 +1（仅影响设施数量）

上游措辞是「**仅影响设施数量**」，所以它不是新的心情速率、也不是叠加类修正，而是
**"数设施时用的那个数字"**：凡是走计数基准 `power_count` 的技能，都该看到
`有效间数 = 实际间数 + Σ(条件成立的数量修正)`。

⚠️ **不是**"发电站变多了"：布局、容量、进驻位一个都不变，只改"数出来是几间"。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import List, Tuple

from data.facility_count import MODIFIERS, FacilityCountMod

from .config import FacilityType


def _ctx(world, owner, facility):
    """条件函数要的鸭子类型上下文（与 `rules.SkillContext` 同形，但不引入 rules）。"""
    return SimpleNamespace(world=world, owner=owner, target=None, facility=facility)


def active_modifiers(world, ftype: FacilityType = FacilityType.POWER) -> List[Tuple[FacilityCountMod, bool, str]]:
    """这一刻对 `ftype` 类设施生效（或未生效）的数量修正。

    返回 `[(修正, 是否成立, 说明)]`：**未成立的也返回**（说明里写清为什么），
    这样流水账与调试都能看到"本该有 +2、但 Lancet-2 不在发电站"这类信息。
    """
    out: List[Tuple[FacilityCountMod, bool, str]] = []
    if world is None:
        return out
    for mod in MODIFIERS:
        if mod.target is not ftype:
            continue
        owner = world.get_operator(mod.provider)
        if owner is None:
            continue                                   # 她不在基建里（未排班 / 不在基建名单）
        facility = world.facility_of(mod.provider)
        if facility is None or facility.ftype is not mod.facility:
            continue                                   # 没进驻到该技能要求的设施
        why = mod.condition_doc or "条件成立"
        ok = True
        if mod.condition is not None:
            ok = bool(mod.condition(_ctx(world, owner, facility)))
        out.append((mod, ok, why if ok else f"{why} —— 不满足"))
    return out


def effective_count(world, ftype: FacilityType = FacilityType.POWER) -> Decimal:
    """**有效设施数量** = 实际间数 + Σ（条件成立的数量修正）。

    ⚠️ 只加、不减；上游这两条都是正数（+2 / +1），出现负数修正时同样照加。
    """
    total = Decimal(world.count_of_type(ftype))
    for mod, ok, _why in active_modifiers(world, ftype):
        if ok:
            total += Decimal(mod.delta)
    return total


def explain(world, ftype: FacilityType = FacilityType.POWER) -> str:
    """`power_count` 的折算尾巴：`× 4（实际 2 间 + 森蚺「我寻思能行」+2）`。

    只写**成立**的那些修正；没有修正时返回空串（流水账保持干净）。
    """
    applied = [(mod, why) for mod, ok, why in active_modifiers(world, ftype) if ok]
    if not applied:
        return ""
    base = Decimal(world.count_of_type(ftype))
    parts = " + ".join(f"{mod.provider}「{mod.name}」+{mod.delta}" for mod, _why in applied)
    total = base + sum(Decimal(mod.delta) for mod, _why in applied)
    return f"（实际 {base} 间 + {parts} = {total} 间）"


def report(world, ftype: FacilityType = FacilityType.POWER) -> List[str]:
    """逐条诊断（`--explain` / 自检脚本用）：成立的标 ✓、没成立的写清原因。"""
    lines: List[str] = []
    for mod, ok, why in active_modifiers(world, ftype):
        mark = "✓" if ok else "✗"
        lines.append(f"{mark} {mod.provider}「{mod.name}」（{mod.buff_id}）{mod.delta:+d}"
                     f"：{why}")
    return lines


__all__ = ["active_modifiers", "effective_count", "explain", "report"]
