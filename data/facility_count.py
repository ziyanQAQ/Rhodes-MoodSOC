"""data/facility_count.py —— **设施数量修正**表（上游「仅影响设施数量」那一类效果）。

## 为什么单列一张表

上游有两条基建技能的**唯一效果**就是改设施数量，它们本身不产生、也不需要任何心情子句：

| buff_id | 技能 | 原文（上游 `building_data.json → buffs[].description`；PRTS 后勤技能一览同文） |
|---|---|---|
| `control_pow_bot[000]` | 森蚺「我寻思能行」 | 进驻控制中枢时，如果 **Lancet-2 进驻在发电站**，**发电站额外 +2（仅影响设施数量）** |
| `power_count[000]` | 承曦格雷伊「晨曦」 | 进驻发电站时，如果**其他发电站内没有进驻作业平台**，则**发电站额外 +1（仅影响设施数量）** |

它们**只**通过计数基准 `power_count` 影响心情 —— 目前全项目唯一的消费方是
流明「柔和微光」的 `+0.05/间发电站` 分句（`data/moods_skills.txt` 的
`dorm_powToRecAll_000/010` clause 2）。所以它们**不进** `moods_skills.txt`
（那里只放心情子句）、也**不进** `data/skills_data.py`，
而是登记在本表，由 `mood_soc/facility_count.py` 在算 `power_count` 时求值。

## 上游字段对照

- `facility`：**提供者**必须进驻的设施类型（森蚺→控制中枢；承曦格雷伊→发电站）；
- `target`：被改数量的设施类型（两条都是发电站）——算 `power_count` 时才看它；
- `delta`：+2 / +1；
- `condition`：`data/conditions.py` 里的条件函数（鸭子类型读 `ctx`）。

⚠️ 与其它数据表同一条纪律：**上游为准**。改这里要同时写清出处与日期；
客户端版本快照见 `data/domain.py` 的说明（本项目快照 2.7.71）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from data.conditions import _cond_lancet2_in_power, _cond_no_platform_in_other_power
from data.domain import FacilityType


@dataclass(frozen=True)
class FacilityCountMod:
    """一条"设施数量修正"：某干员进驻某设施时，把 `target` 类设施的数量 +delta。"""

    buff_id: str                      # 上游 buff_id（可回溯台账 data/skills_registry.txt）
    name: str                         # 技能名
    provider: str                     # 提供者（干员名）
    template: str                     # 分类模板（X10「跨设施/设施计数条件」）
    facility: FacilityType            # 提供者必须进驻的设施
    target: FacilityType              # 被改数量的设施
    delta: int                        # 数量修正（正=增加）
    condition: Optional[Callable]     # 附加条件（鸭子类型 ctx，见 data/conditions.py）
    text: str                         # 上游原文（留档，便于核对）
    condition_doc: str = ""           # 条件的人类可读说明（进流水账）


MODIFIERS: Tuple[FacilityCountMod, ...] = (
    FacilityCountMod(
        buff_id="control_pow_bot[000]",
        name="我寻思能行",
        provider="森蚺",
        template="X10",
        facility=FacilityType.CONTROL_CENTER,
        target=FacilityType.POWER,
        delta=2,
        condition=_cond_lancet2_in_power,
        condition_doc="Lancet-2 进驻在发电站",
        text="进驻控制中枢时，如果Lancet-2进驻在发电站，发电站额外+2（仅影响设施数量）",
    ),
    FacilityCountMod(
        buff_id="power_count[000]",
        name="晨曦",
        provider="承曦格雷伊",
        template="X10",
        facility=FacilityType.POWER,
        target=FacilityType.POWER,
        delta=1,
        condition=_cond_no_platform_in_other_power,
        condition_doc="其他发电站内没有进驻作业平台",
        text="进驻发电站时，如果其他发电站内没有进驻作业平台，则发电站额外+1（仅影响设施数量）",
    ),
)


__all__ = ["FacilityCountMod", "MODIFIERS"]
