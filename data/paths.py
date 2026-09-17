"""data/paths.py —— **数据目录的唯一路径出口**。

全项目只有本模块负责回答"某个数据文件在哪"。其它模块（含 `scripts/` 下的生成器与
校验器）一律 `from data.paths import X`，不再各自拼 `Path(__file__).parent.parent / "resources"`。

## 目录分工（`data/` 是数据的唯一住处）

```
data/
├── paths.py                 ← 本文件（路径出口）
├── skills_data.py           生成物：技能效果库 / 干员↔技能 / 解锁绑定 / 阵营 / 变量产出者
├── operator_names.py        生成物：干员名别名（英文名 / char_id → 中文名）
├── <各 .txt>                人工维护或上游生成的 CSV 文本（技能库 / 台账 / 阵营 / 名册…）
└── resources/               工具产物与样例：MAA 排班样例、4 种导入样例、数据字典说明、校验报告
```

**改数据请改 `data/*.txt`（表格类）或 `data/resources/`（样例类），再重跑生成器**；
`*_data.py` / `operator_names.py` 是生成物，勿手改：

```bash
python scripts/classify_skills.py --agd <ArknightsGameData>   # 挂模板 + 台账
python scripts/generate_skills_data.py                        # → data/skills_data.py
python scripts/generate_operator_names.py                     # → data/operator_names.py
```
"""
from __future__ import annotations

from pathlib import Path

#: 仓库根目录（`data/paths.py` 的上两级）——一切相对路径的锚点。
ROOT: Path = Path(__file__).resolve().parent.parent

#: 数据根目录＝本文件所在目录（`data/`）。
DATA: Path = Path(__file__).resolve().parent

#: 工具产物与样例目录（`data/resources/`）。
RES: Path = DATA / "resources"


# ---------------------------------------------------------------------------
# 一、人工维护 / 上游生成的 CSV 文本（改数据改这些）
# ---------------------------------------------------------------------------
SKILLS_TXT = DATA / "moods_skills.txt"              # clause 级心情技能库（含 template_id/params）
REGISTRY_TXT = DATA / "skills_registry.txt"         # 上游 755 条 buff 的覆盖台账
OPERATORS_TXT = DATA / "operators.txt"              # 干员↔技能映射（含解锁精英化/等级）
FACTIONS_TXT = DATA / "factions.txt"                # 干员↔阵营/标签（由上游 cc.g.* 生成）
FACTIONS_SUPPLEMENT_TXT = DATA / "factions_supplement.txt"   # 阵营表的人工补充
VARIABLE_PRODUCERS_TXT = DATA / "variable_producers.txt"     # 变量产出者表


# ---------------------------------------------------------------------------
# 二、代码生成物（**勿手改**，由 `scripts/*.py` 重写）
# ---------------------------------------------------------------------------
SKILLS_DATA = DATA / "skills_data.py"               # ← scripts/generate_skills_data.py
OPERATOR_NAMES_DATA = DATA / "operator_names.py"    # ← scripts/generate_operator_names.py


# ---------------------------------------------------------------------------
# 三、工具产物与样例（`data/resources/`）
# ---------------------------------------------------------------------------
REQUIREMENTS_DOCX = ROOT / "resources" / "心情消耗回复和工休时间.docx"   # 需求文档（不是数据表）
SKILL_VERIFY_REPORT = RES / "skill_verify_report.md"        # ← scripts/verify_skills.py --report
OUTPUT_CONTRACT_MD = RES / "输出JSON结构说明.md"             # v3 输出契约（上游文档）
V4_EXAMPLE_MD = RES / "plan_compute_example_v4_annotated.md"  # v4 蓝图样例与注释
MAA_SAMPLE = RES / "arknights-infra-schedule-maa.json"      # MAA 排班样例
V3_SAMPLE_3SHIFTS = RES / "import_v3_out_3shifts.json"
V3_SAMPLE_NO_MAA = RES / "import_v3_out_no_maa.json"
V3_SAMPLE_36H = RES / "import_v3_out_36h_layout.json"
V4_SAMPLE = RES / "import_v4_input.json"

#: 样例文件全集（测试与文档用一次列全，免得各写一份）。
SAMPLES = (MAA_SAMPLE, V3_SAMPLE_3SHIFTS, V3_SAMPLE_NO_MAA, V3_SAMPLE_36H, V4_SAMPLE)

#: 示例场景目录（`scenarios/`：demo.json + maa_shift1/2/3.json）。
SCENARIOS = ROOT / "scenarios"


__all__ = [
    "ROOT", "DATA", "RES", "SCENARIOS",
    "SKILLS_TXT", "REGISTRY_TXT", "OPERATORS_TXT", "FACTIONS_TXT",
    "FACTIONS_SUPPLEMENT_TXT", "VARIABLE_PRODUCERS_TXT",
    "SKILLS_DATA", "OPERATOR_NAMES_DATA",
    "REQUIREMENTS_DOCX", "SKILL_VERIFY_REPORT", "OUTPUT_CONTRACT_MD", "V4_EXAMPLE_MD",
    "MAA_SAMPLE", "V3_SAMPLE_3SHIFTS", "V3_SAMPLE_NO_MAA", "V3_SAMPLE_36H", "V4_SAMPLE",
    "SAMPLES",
]
