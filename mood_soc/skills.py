"""mood_soc/skills.py —— **兼容门面**：技能类的历史 import 路径。

技能数据本身已经全部搬到 `data/` 包（那是数据的唯一住处）：

| 内容 | 现在在哪 |
|---|---|
| `Skill` / `SkillEquip` / `SkillKind` | `data/skill_model.py` |
| `SKILLS` / `SKILL_EQUIPS` / `DEFAULT_OPERATORS` / `TRAITS` / `OPERATOR_FACTIONS` / `FACTION_MEMBERS` / `SPREAD_SKILL_IDS` / `ROOM2_MAX_GROUP` / `VARIABLE_PRODUCERS` | `data/skills_data.py`（生成物） |
| 条件函数 `_cond_*` / 阵营查询 `_factions_of` | `data/conditions.py` |

本模块只做 re-export，**不含任何逻辑**，历史写法继续可用：

```python
from mood_soc.skills import SKILLS, SkillKind, DEFAULT_OPERATORS, _factions_of   # 仍然可以
from data.skills_data import SKILLS                                              # 新写法（推荐）
```

## 为什么生成物不再反过来 import 本模块

`data/skills_data.py` 以前写 `from .skills import Skill, ...`。那是**数据包反向依赖计算包**：
生成器要在"生成物还不存在"时运行，而 `mood_soc/__init__.py` 会连锁 import
`rules → skills → skills_data` ⇒ 「生成脚本无法重新生成自己」的引导死锁。
现在链条单向：

```
mood_soc/config → data/skill_model → data/skills_data → mood_soc/skills（本文件，只 re-export）
                                              ↑
                          data/conditions（生成物要用它当 Skill.condition 的值）
```
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 技能数据模型（原在本模块，现搬去数据包）
# ---------------------------------------------------------------------------
from data.skill_model import Skill, SkillEquip, SkillKind

# ---------------------------------------------------------------------------
# 条件函数 / 阵营查询（原在本模块，现搬去数据包）
# ---------------------------------------------------------------------------
from data.conditions import (
    _cond_alone_in_facility,
    _cond_dorm_abyssals_full_mood,
    _cond_mood_below_18,
    _cond_mood_below_20,
    _cond_no_abyssal_outside_dorm,
    _cond_self_full_mood,
    _cond_target_in_faction,
    _cond_target_is,
    _cond_with_cc_faction,
    _cond_with_cc_mogui,
    _cond_with_cc_operator,
    _cond_with_cc_xiangzi,
    _cond_with_facility_operator,
    _factions_of,
)

# ---------------------------------------------------------------------------
# 技能库数据（自动生成，勿手改）
# ---------------------------------------------------------------------------
from data.skills_data import (
    DEFAULT_OPERATORS,
    FACTION_MEMBERS,
    OPERATOR_FACTIONS,
    ROOM2_MAX_GROUP,
    SKILLS,
    SKILL_EQUIPS,
    SPREAD_SKILL_IDS,
    TRAITS,
    VARIABLE_PRODUCERS,
)


def base_skill_id(skill_id: str) -> str:
    """由 `skill_id#clause` 取回原始 skill_id（扩散白名单比对用）。"""
    return skill_id.split("#", 1)[0]


__all__ = [
    "Skill",
    "SkillEquip",
    "SkillKind",
    "SKILLS",
    "SKILL_EQUIPS",
    "DEFAULT_OPERATORS",
    "TRAITS",
    "OPERATOR_FACTIONS",
    "FACTION_MEMBERS",
    "SPREAD_SKILL_IDS",
    "ROOM2_MAX_GROUP",
    "VARIABLE_PRODUCERS",
    "base_skill_id",
]
