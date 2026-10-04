"""data —— **干员与技能数据**的唯一住处。

本包只装数据与"数据怎么读"，不含任何心情计算：

| 文件 | 是什么 | 谁生成 |
|---|---|---|
| `paths.py` | 全部数据路径的**唯一出口** | 手写 |
| `conditions.py` | 技能条件函数（`_cond_*`）与阵营查询（`_factions_of`） | 手写 |
| `skills_data.py` | SKILLS / DEFAULT_OPERATORS / SKILL_EQUIPS / TRAITS / OPERATOR_FACTIONS / … | `scripts/generate_skills_data.py` |
| `operator_names.py` | 干员名别名表（英文名 / `char_id` → 中文名） | `scripts/generate_operator_names.py` |
| `*.txt` | 人工维护的技能库 / 台账 / 阵营 / 名册（CSV） | 人工或上游生成器 |
| `resources/` | 样例 JSON 与数据字典说明（MAA 排班、4 种导入样例、校验报告） | 工具产物 |

## 依赖方向（别搞反）

```
mood_soc/battery  ←  data/conditions  ←  data/skills_data  ←  mood_soc/skills（只 re-export）
                        ↑                     ↑
                   mood_soc/config      mood_soc/skill_templates（模板字典）
```

`data/*` **不许** import `mood_soc` 的 `rules` / `models`（那是计算层），
也**不许** import `store` / `ui` / `api`。`mood_soc/skills.py` 反向 re-export 本包的符号，
所以历史写法 `from mood_soc.skills import SKILLS` 继续可用。

## 想改数据

```bash
# 1) 技能数值 / 解锁 / 阵营（改 txt，再重跑生成器）
python scripts/classify_skills.py --agd <ArknightsGameData>   # 挂模板 + 生成台账
python scripts/generate_skills_data.py                        # → data/skills_data.py
python scripts/generate_factions.py --agd <ArknightsGameData> # → data/factions.txt
python scripts/generate_operator_names.py --agd <ArknightsGameData>  # → data/operator_names.py
# 2) 自检（零遗漏 + 全量核对）
python scripts/classify_skills.py --check
python scripts/verify_skills.py --check
```

详见 `documents/06-数据来源.md`（数据查找策略，**强制**）与 `documents/02-数值规则.md`。
"""
from __future__ import annotations

# 刻意**不**在包初始化时 import 任何子模块：生成器（`scripts/generate_skills_data.py`）
# 要在还没有 `skills_data.py` 的情况下 import `data.paths`，任何连锁 import 都会变成引导死锁。
__all__: list = []
