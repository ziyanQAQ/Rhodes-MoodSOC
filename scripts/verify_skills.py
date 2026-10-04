"""scripts/verify_skills.py —— 技能**全量核对**（三层）＋报告。

目的：回答"这套心情模型对干员技能的效果**是不是全对**"——不是抽查几条，而是
**把上游每一条 buff / 本仓库每一条 clause 都过一遍**，逐条判定
「建模了吗 → 挂在哪个模板 → 数值/方向对不对」，并把结论落成一份可复查的报告。

三层（与 `documents/04-技能分类大纲.md` 的六轴模板对应）：

| 层 | 对象 | 核对什么 |
|---|---|---|
| **L1 模板级** | `skill_templates.TEMPLATES`（M01~M17 / X01~X11） | 模板与数据是否自洽：每个 clause 的模板都存在、**已建模**的 M 模板都有使用者、X 族（登记不建模）**不产生**心情贡献 |
| **L2 clause 级** | `skills.SKILLS` 全部 clause | 逐条**自动造一个满足它条件的场景**，核对它在流水账里出现、落在正确的桶、数值 == 模板口径（原值 × 变量份数 × 计数基准 × 阵营人数） |
| **L3 描述对照** | 上游 `building_data.json` 的 buff 描述原文 | 抽描述里的数字与方向词，与数据表的 `value` 对照；对不上就是**真差异**（红线） |

怎么读结论：

    # 只看结论（CI 用；有硬伤时退出码 1）
    .venv/Scripts/python.exe scripts/verify_skills.py --check

    # 生成报告（默认写 resources/skill_verify_report.md）
    .venv/Scripts/python.exe scripts/verify_skills.py --report

    # 指上游仓库（L3 需要；不指就自动探测常见路径，探测不到则 L3 跳过）
    .venv/Scripts/python.exe scripts/verify_skills.py --agd <ArknightsGameData> --report

⚠️ 三层都**只读**：本脚本不改任何数据。核出真差异时，在报告里列出并由人拍板后再改
（改数据要走 `data/*.txt` → 重新生成 `data/skills_data.py` 的管道）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mood_soc import build_base_layout                                  # noqa: E402
from mood_soc.battery import to_decimal                                 # noqa: E402
from mood_soc.config import FACILITY_LABELS, FacilityType                # noqa: E402
from mood_soc.ledger import Bucket                                       # noqa: E402
from mood_soc.rules import compute_net_rate, mood_ledger                # noqa: E402
from mood_soc.skills import SKILLS, SkillKind, base_skill_id, SPREAD_SKILL_IDS  # noqa: E402
from mood_soc.skill_templates import ModelTier, TEMPLATES                # noqa: E402
from mood_soc.variables import basis_count, collect_variables            # noqa: E402

from data.paths import (  # noqa: E402  （路径的唯一出口）
    REGISTRY_TXT,
    SKILL_VERIFY_REPORT as REPORT_MD,
)

# 合成干员名：不在 `SKILL_EQUIPS` 里，因此**不受精英化门槛约束**（要核对的是技能本身）
SYNTH = "校验员"
MATE = "陪练甲"          # 同设施/同宿舍的一般对象（消耗类与宿舍类的目标）
FILLER = "占位乙"
SPREADER = "扩散者"      # 扩散核对用：中枢里那位「公事公办」（M02c）

# 核对用的骨架布局：把"跨设施"技能（M01/M02a/M02b/M02c）的目标房间都摆出来
# （同类型多房间用下标区分：`宿舍` 两条 = 两间宿舍，`房间序号` 只在内部用作字典键）
SKELETON: Tuple[Tuple[str, int], ...] = (
    ("控制中枢", 5), ("制造站", 3), ("贸易站", 3), ("发电站", 1),
    ("办公室", 3), ("会客室", 2), ("宿舍", 5), ("宿舍", 5),
)
# 房间名 → 骨架里的等级（同名取第一条）
SKELETON_LEVEL = {}
FILLER_INDEX = {}
for _i, (_n, _lv) in enumerate(SKELETON, start=1):
    SKELETON_LEVEL.setdefault(_n, _lv)
    FILLER_INDEX.setdefault(_n, _i)
DORM_KINDS = (SkillKind.DORM_SELF, SkillKind.DORM_GROUP, SkillKind.DORM_SINGLE,
              SkillKind.DORM_TARGETED, SkillKind.DORM_META)


# ============================================================================
# L2 提示表：这些 clause 的"满足条件的场景"要人为安排（其余自动兜底）
# ============================================================================
# 键 = clause key（`skill_id#clause`）；值 = 场景提示：
#   target_mood      同行/同宿舍对象的心情（默认 12 = 未满，宿舍单体类必须未满）
#   target_factions  同行/同宿舍对象的阵营（条件按阵营筛选时用）
#   owner_factions   技能持有者的阵营
#   producers        为变量门控 clause 补的"变量产出者"真人名单（默认按变量自动补）
#   unreachable      True = 按文档口径**本模型不可达**（期望"不产生贡献"）
HINTS: Dict[str, dict] = {
    # —— 共事类（条件要看中枢/同设施里有没有某人）——
    "control_allCost_condChar_000#1": {"cc_mate": "阿"},
    "control_mp_cost_double_000#1": {"cc_mate": "阿米娅"},
    "control_mp_cost_double_001#1": {"cc_mate": "阿米娅"},
    "control_mp_expand_double_000#2": {"cc_mate": "魔王"},
    "control_mp_cost_reset_000#1": {"cc_mate": "丰川祥子"},
    "control_dorm_rec2_000#2": {"cc_mate": "丰川祥子"},
    "control_mp&meet_spd_000#1": {"cc_mate": "丰川祥子"},
    "control_meeting&mp_cost_000#1": {"cc_mate": "异客"},          # 萨尔贡
    "control_meeting&mp_cost_100#1": {"cc_mate": "异客"},
    "trade_ord_limit&cost_P_000#1": {"mate": "德克萨斯"},
    "trade_ord_limit&cost_P_001#1": {"mate": "德克萨斯"},
    "trade_ord_limit&cost_P_010#1": {"mate": "能天使"},
    "trade_ord_limit&cost_P_020#1": {"mate": "伺夜"},
    "trade_ord_spd&cost_P_000#1": {"mate": "拉普兰德"},
    # —— 独自一人（会客室 6 条：只有自身时才加耗）——
    "meet_spd&cost_condChar_000#1": {"alone": True},
    "meet_spd&cost_condChar_001#1": {"alone": True},
    "meet_spd&cost_condChar_002#1": {"alone": True},
    "meet_spd&cost_condChar_011#1": {"alone": True},
    "meet_spd&cost_condChar_020#1": {"alone": True},
    "meet_spd&cost_condChar_021#1": {"alone": True},
    # —— 定向加成（目标需满足阵营/具体干员/心情门槛）——
    "dorm_rec_all&tag_000#2": {"target_factions": ["莱欧斯小队"]},
    "dorm_rec_single&tag_000#2": {"target_factions": ["怪物猎人小队"]},
    "dorm_rec_single_P_000#2": {"target_name": "锡兰", "target_factions": ["维多利亚"]},
    "dorm_rec_single_P_001#2": {"target_name": "嘉维尔", "target_factions": ["萨尔贡"]},
    "dorm_rec_single_P_002#2": {"target_name": "蓝毒", "target_factions": ["伊比利亚"]},
    "dorm_rec_single_power_000#2": {"target_factions": ["萨米"]},
    "dorm_rec_single_power_001#2": {"target_factions": ["拉特兰"]},
    "dorm_rec_toone_000#1": {"boost": True},
    "dorm_rec_all&tired_000#2": {"target_mood": 10},
    "dorm_rec_all&tired_100#1": {"target_mood": 10},
    "dorm_exchangeAp_000#1": {"event": True},
    # —— 消除类（目标要满足阵营；否则归零不生效）——
    "control_facCostReset_000#1": {"target_factions": ["岁"]},
    # —— 潮汐守望三条：`abyssal_non_dorm` 口径**含持有者自身**，所以场景里必须给她
    #    加「深海猎人」阵营（合成干员不带阵营的话，她自己就不算"宿舍以外的深海猎人"，
    #    条件会假成立、核对会误报）；#1 因此得到 0.5×1，#2/#3 按文档**不可达**——
    "control_mp_aegir1_000#1": {"owner_factions": ["深海猎人"]},
    "control_mp_aegir1_000#2": {"owner_factions": ["深海猎人"],
                                "unreachable": "03-特殊机制 第 23 条：含持有者自身口径下「反之」恒不成立"},
    "control_mp_aegir1_000#3": {"owner_factions": ["深海猎人"],
                                "unreachable": "03-特殊机制 第 23 条：依赖「反之」分支，同样不可达"},
}

# 目标名字（`_cond_target_is` 按名字点名的 clause）：要起**真名**才能命中条件，
# 但真人会带自己的技能进场景（多一条同 skill_id 的贡献）→ 用 factions 兜住其它条件。
NAME_TARGETS = {"dorm_rec_single_P_000#2", "dorm_rec_single_P_001#2", "dorm_rec_single_P_002#2"}

# L3：描述**本来就没有速率数字**的 clause（消除类/事件类 value=0）→ 不参与数字对照
L3_SKIP_ZERO = True

# L3 已确认的差异：clause key -> 原因（**必须还在差异状态**，修好后要删掉本行）
# ⚠️ 这里是"暂时容忍、待拍板"的豁免名单，不是垃圾桶：
#    · 空表 = 数据表与上游描述**逐条一致**（当前状态）；
#    · 往里加条目要写清"为什么先不改"，并且 `tests/test_skill_coverage.py` 会断言
#      该条目**仍然处于差异状态**——修好之后忘了删，测试会红。
#    · 历史条目：`control_mp_cost&bd1_000#1`（夕「不以物喜」原先按 M05 消耗侧建模，
#      上游写的是"中枢内所有干员的心情每小时**恢复**+0.05"）→ 已按上游改为 M03 回复侧，
#      见 `documents/04-技能分类大纲.md` §5.12。
L3_KNOWN_DIFFS: Dict[str, str] = {}


# ============================================================================
# 公共工具
# ============================================================================
STRIP_TAGS = re.compile(r"</?(?:@cc\.\w+|\$cc\.\w+)>")
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def strip_tags(text: str) -> str:
    """去掉上游描述里的富文本标记（`<@cc.vup>` / `</>` / `<$cc.bd_b1>`…）。"""
    return STRIP_TAGS.sub("", text or "")


def buff_id_of(skill_id: str) -> str:
    """`control_mp_cost_000` → `control_mp_cost[000]`（上游 buff 的 id 形式）。"""
    base, num = skill_id.rsplit("_", 1)
    return f"{base}[{num}]"


def find_upstream(explicit: Optional[str] = None) -> Optional[Path]:
    """找上游 `building_data.json`：显式路径 → 环境变量 → 常见位置（找不到返回 None）。"""
    import os
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("ARKNIGHTS_GAMEDATA"):
        cands.append(Path(os.environ["ARKNIGHTS_GAMEDATA"]))
    cands += [Path(r"E:\code_h\python\_agd"), Path.home() / "ArknightsGameData"]
    for c in cands:
        p = c / "zh_CN" / "gamedata" / "excel" / "building_data.json"
        if p.exists():
            return p
        if c.name == "building_data.json" and c.exists():
            return c
    return None


def load_upstream(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ============================================================================
# L1：模板级（模板 ↔ 数据的自洽性）
# ============================================================================
def check_templates() -> List[str]:
    """返回问题清单（空 = 全部自洽）。"""
    issues: List[str] = []
    used = Counter(s.template_id for s in SKILLS.values())
    for key, s in SKILLS.items():
        if not s.template_id:
            issues.append(f"{key} 没有 template_id")
        elif s.template_id not in TEMPLATES:
            issues.append(f"{key} 的模板 {s.template_id} 不在 TEMPLATES 里")
    for tid, t in TEMPLATES.items():
        if t.tier == ModelTier.A4_NONE or not t.modeled:
            if used.get(tid) and t.tier == ModelTier.A4_NONE:
                issues.append(f"{tid} 标了「非心情/不建模」，却有 {used[tid]} 条 clause 用它")
        elif tid.startswith("M") and not used.get(tid):
            if tid not in NO_USER_TEMPLATES:
                issues.append(f"已建模模板 {tid} 在数据里没有任何使用者")
    return issues


# 「已建模但当前无使用者」的模板：**文档已登记**，不是漏挂。
#   - M02a：中枢→room1 回复，公事公办同时带扩散 → 归入 M02c（`04-技能分类大纲.md` §5 表：0（并入 M02c））
#   - M06：同设施「其他」干员消耗增减，现代数据已无使用者（低语改判 M05 含自身）
#   - M15b：加工站触发式恢复一次心情（棘刺「爆炸艺术」），不进每小时速率
NO_USER_TEMPLATES = {"M02a", "M06", "M15b"}


def template_summary() -> List[dict]:
    """每个模板的使用统计（报告用）。"""
    used = Counter(s.template_id for s in SKILLS.values())
    out = []
    for tid in sorted(TEMPLATES):
        t = TEMPLATES[tid]
        out.append({"id": tid, "name": t.name, "tier": t.tier.value, "modeled": t.modeled,
                    "stacking": t.stacking.value, "clauses": used.get(tid, 0)})
    return out


# ============================================================================
# L2：clause 级（自动造场景 → 核对流水账）
# ============================================================================
class ClauseCheck:
    """一条 clause 的核对结果。"""

    def __init__(self, key: str, skill, status: str, detail: str = "",
                 expected: Optional[Decimal] = None, got: Optional[Decimal] = None):
        self.key = key
        self.skill = skill
        self.status = status          # ok / bad / unreachable / event / boost ...
        self.detail = detail
        self.expected = expected
        self.got = got

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "unreachable", "event", "boost", "eliminate", "pool")


def _facility_specs(skill, hint: dict, producers: List[dict],
                    owner_name: str = SYNTH,
                    owner_skills: Optional[List[str]] = None) -> Tuple[list, str, str]:
    """按技能类型拼出场景的设施列表。

    规则（自动兜底）：
      - `CC_RECOVER` / `CC_REDUCE`：持有者住**控制中枢**（技能住中枢，作用于别处）；
      - `DORM_*`：持有者住**宿舍**；
      - 其余（消耗/消除类）：持有者住 `facility_types[0]`（缺省制造站）。
    每间房间都放一名占位干员——M02a/b/c 这类"中枢 → 其他设施"的贡献要落在
    **目标设施里那位**的流水账上，房间空了就永远核对不到。

    `owner_name` / `owner_skills`：默认用合成干员 + 单条技能（L2 口径，只核技能本身）；
    L4 传**真名 + 她真实的技能槽**，用来核"这个干员在基建里真的按她的技能结算"。

    返回 `(facilities, 持有者房间名, 目标干员名)`。
    """
    kind = skill.kind
    owner = {"name": owner_name,
             "skill_ids": list(owner_skills) if owner_skills is not None else [skill.id],
             "elite": 2, "level": 30, "mood": hint.get("owner_mood", 24)}
    if hint.get("owner_factions"):
        owner["factions"] = list(hint["owner_factions"])

    target_name = hint.get("target_name") or MATE
    target = {"name": target_name, "elite": 2, "level": 30,
              "mood": hint.get("target_mood", 12)}
    if hint.get("target_factions"):
        target["factions"] = list(hint["target_factions"])
    if hint.get("target_skill_ids"):
        target["skill_ids"] = list(hint["target_skill_ids"])

    if kind in (SkillKind.CC_RECOVER, SkillKind.CC_REDUCE):
        home = "控制中枢"
    elif skill.template_id == "M07b":
        # M07b = 「自身回复（**非宿舍**/加工站）」：持有者在工作设施里给自己回复
        # （歌蕾缇娅「潮汐守望」的「反之」分支）。⚠️ 放进宿舍就永远核不对。
        home = "控制中枢"
    elif kind in DORM_KINDS:
        home = "宿舍"
    elif skill.facility_types:
        home = FACILITY_LABELS[skill.facility_types[0]]
    else:
        home = "制造站"

    # 目标（陪练甲）要放进**技能真正作用的那个房间**，否则等于什么都没核：
    #   · 中枢→宿舍类（M01）：目标在宿舍；· 中枢→工作设施类（M02a/b/c）：目标在那些房间；
    #   · 中枢内全体（M03/M04）：目标也在中枢；· 同设施类：目标与持有者同房间。
    types = set(skill.facility_types)
    if kind == SkillKind.CC_RECOVER:
        if FacilityType.DORMITORY in types:
            target_home = "宿舍"
        elif types:
            target_home = FACILITY_LABELS[sorted(types, key=lambda t: t.value)[0]]
        else:
            target_home = "控制中枢"
    elif kind in DORM_KINDS:
        target_home = "宿舍"
    else:
        target_home = home

    scene: List[dict] = []
    placed: Dict[str, bool] = {}
    for idx, (name, lv) in enumerate(SKELETON, start=1):
        ops: List[dict] = []
        first_of_name = not placed.get(name)
        placed[name] = True
        if name == home and first_of_name:
            ops.append(owner)
        if name == target_home and first_of_name and not hint.get("alone"):
            ops.append(target)
        if not ops:
            if hint.get("alone") and name == home:      # 「独自一人」：这间房不能有别人
                pass
            else:
                ops.append({"name": f"占位{idx}", "elite": 2, "level": 30, "mood": 12})
        scene.append({"type": name, "level": lv if name not in (home, target_home)
                      else SKELETON_LEVEL.get(name, lv), "operators": ops})

    # 条件要"中枢里有某人" / "同设施里有某人"
    # ⚠️ 搭档**只提供"在场"这一件事**：给他 `skill_ids=["__none__"]` 把他的真技能摘掉，
    #    否则"撤掉搭档"会连他的技能一起撤掉，A/B 之差就不再只反映**这条组合条件**了
    #    （实测：浮生得闲的场景里带上「阿」自己的技能，差值从 0.25 变成 1.2）。
    for f in scene:
        if hint.get("cc_mate") and f["type"] == "控制中枢":
            f["operators"].append({"name": hint["cc_mate"], "elite": 2, "level": 30,
                                   "skill_ids": ["__none__"]})
        if hint.get("mate") and f["type"] == home:
            f["operators"].append({"name": hint["mate"], "elite": 2, "level": 30,
                                   "skill_ids": ["__none__"]})

    if producers:                              # 变量产出者单独一间房，避免挤掉目标
        scene.append({"type": "宿舍", "level": 5, "operators": producers})
    # 阵营计数类（per-count）：**故意放 2 名**该阵营干员——期望值因此是 value×2，
    # 如果引擎把"每个该阵营干员"数错（数成 0 或 1），数值立刻对不上。
    if skill.count_faction:
        members = [{"name": "阵营甲", "factions": [skill.count_faction], "elite": 2, "level": 30},
                   {"name": "阵营乙", "factions": [skill.count_faction], "elite": 2, "level": 30}]
        for f in scene:                        # 加进**目标所在**那个房间（数的是它）
            if f["type"] == target_home and any(o.get("name") == target_name
                                                for o in f["operators"]):
                f["operators"] += members
                break
    # 扩散（M02c）：官方术语 `cc.c.skill` 的 15 条「中枢内全体回复」在**中枢里有
    # 玛恩纳「公事公办」**时，额外作用到 room2（其他工作设施）。核对这 15 条时要
    # 把提供者摆进中枢，否则永远走不到扩散那条路径。
    if base_skill_id(skill.id) in SPREAD_SKILL_IDS:
        for f in scene:
            if f["type"] == "控制中枢":
                f["operators"].append({"name": SPREADER, "elite": 2, "level": 30,
                                       "skill_ids": ["control_mp_lonely_000#1"]})
                break
    return scene, home, target_name


def _producers_for(skill, exclude: str = SYNTH) -> List[dict]:
    """给变量门控的 clause 自动补"变量产出者"（真名，按上游产出规则）。

    `exclude`：不要把这些人当产出者（默认排除合成干员；L4 传真名，避免"自己给自己产出"
    把同一人塞进两个房间）。
    """
    if not skill.var_name:
        return []
    from data.skills_data import VARIABLE_PRODUCERS
    picks: List[dict] = []
    seen = set()
    for sid, clause, var, value, basis, cond, sname, holders in VARIABLE_PRODUCERS:
        if var != skill.var_name:
            continue
        for name, _el, _lv in holders:
            if name in seen or name == exclude:
                continue
            seen.add(name)
            # 心情：产出条件读的是**持有者自己的心情**（mood_below_12 / mood_above_12）
            mood = 10 if cond == "mood_below_12" else (24 if cond == "mood_above_12" else 21)
            # ⚠️ 产出者**只当变量产出者**：`skill_ids` 给一个不存在的 id，避免他们自己的
            # 心情技能混进流水账（否则"同一条 clause 的实例条数"会被别人重复计数）。
            # `collect_variables` 只按名字/精英/等级/心情判定，不读 skill_ids。
            picks.append({"name": name, "elite": 2, "level": 30, "mood": mood,
                          "skill_ids": ["__none__"]})
            break
    return picks[:4]


def _independent_expected(skill, variables, world, facility, op) -> Tuple[bool, Decimal]:
    """**独立**按文档口径折算期望值（不调用 rules 的折算函数，避免自证）。

    顺序与 `documents/02-数值规则.md` 一致：
      变量门槛（`var_min`）不满足 → **不成立**（不进流水账）；
      `var_per` → 值 × floor(变量/N)（凑不够 1 份就是 0）；
      计数基准（`basis`）→ 值 × 计数；阵营计数（`count_faction`）→ 值 × 目标设施内该阵营人数。
    """
    if skill.var_name and variables is not None:
        if skill.var_min is not None and not variables.at_least(skill.var_name, skill.var_min):
            return False, Decimal("0")
        if skill.var_per is not None:
            cur = variables.get(skill.var_name)
            units = int(cur // skill.var_per)
            value = skill.value * units
            if skill.basis:
                value = value * basis_count(world, skill.basis, facility, op)
            if skill.count_faction:
                value = value * sum(1 for o in facility.operators
                                    if skill.count_faction in _factions_of(o))
            return True, value
    value = skill.value
    if skill.basis:
        value = value * basis_count(world, skill.basis, facility, op)
    if skill.count_faction:
        n = sum(1 for o in facility.operators if skill.count_faction in _factions_of(o))
        value = value * n
    return True, value


def _factions_of(op):
    from data.conditions import _factions_of as f
    return f(op)


ROOM1_TYPES = (FacilityType.POWER, FacilityType.OFFICE, FacilityType.RECEPTION)
WORK_TYPES = (FacilityType.POWER, FacilityType.MANUFACTURING, FacilityType.TRADING,
              FacilityType.OFFICE, FacilityType.RECEPTION)


def expected_scopes(skill, world, owner: str = SYNTH) -> Tuple[Optional[int], str]:
    """期望"这条 clause 会落在**几个**干员的流水账上"（核对"作用范围"，不只是数值）。

    返回 `(期望条数, 说明)`；`None` = 范围由条件筛选，不做硬性条数核对。

    为什么值得核对：作用范围错了（比如"同设施全体"退化成"只作用自己"、中枢→宿舍漏了
    某间宿舍、扩散白名单没生效）**数值仍然是"对的"**，只核对某一条实例的数值发现不了。

    `owner`：这条 clause 的持有者名（默认合成干员；L4 传真名）。
    """
    kind = skill.kind
    if kind == SkillKind.SELF_CONSUME:
        return 1, "只作用自身"
    if kind == SkillKind.FACILITY_CONSUME:
        fac = world.facility_of(owner)
        return (len(fac.operators) if fac else 0), "同设施全体（含自身）"
    if kind == SkillKind.ROOM_OTHERS_CONSUME:
        fac = world.facility_of(owner)
        return (max(0, len(fac.operators) - 1) if fac else 0), "同设施其他（不含自身）"
    if kind == SkillKind.ELIMINATE_SELF:
        fac = world.facility_of(owner)
        if fac is None:
            return 0, ""
        n = 0
        for o in fac.operators:
            if skill.self_only and o.name != owner:
                continue
            if skill.target_faction and skill.target_faction not in _factions_of(o):
                continue
            n += 1
        return n, "同设施里符合条件的每个干员各一条"
    if kind == SkillKind.CC_RECOVER:
        ftypes = set(skill.facility_types)
        spread = base_skill_id(skill.id) in SPREAD_SKILL_IDS   # 这 15 条会被公事公办扩散
        if ftypes == {FacilityType.CONTROL_CENTER}:
            cc = world.control_center()
            n = len(cc.operators) if cc else 0
            if spread:
                n += sum(len(f.operators) for f in world.facilities
                         if f.ftype in WORK_TYPES)
                return n, "中枢内全体 + 被公事公办扩散到其他设施，各一条"
            return n, "中枢内全体，各一条"
        if FacilityType.DORMITORY in ftypes:
            return sum(len(d.operators) for d in world.all_dormitories()), "每间宿舍的每个成员各一条"
        # room1 类（公事公办本尊）：扩散只作用于 `cc.c.skill` 白名单里的 15 条技能，
        # **不包括它自己**，所以它本条的落点仍然只有 room1 那几间房。
        return sum(len(f.operators) for f in world.facilities
                   if f.ftype in ftypes), "作用设施内的每个干员各一条"
    if kind == SkillKind.DORM_GROUP:
        if skill.condition is not None:
            return None, ""                    # 按目标筛选（如「如果目标是莱欧斯小队」），不硬核条数
        dorm = world.facility_of(owner)
        return (len(dorm.operators) if dorm else 0), "同宿舍每个成员各一条"
    if kind in (SkillKind.DORM_SELF, SkillKind.DORM_SINGLE):
        return 1, "只落在 1 名干员账上（自身 / 锁定的那名受益人）"
    return None, ""


def _contribution_of(world, key: str) -> List[Tuple[str, object]]:
    """扫**每个干员**的流水账，找出 `skill_id == key` 的贡献：[(干员名, Contribution)]。"""
    out = []
    for op in world.all_operators():
        lg = mood_ledger(world, op.name)
        for c in lg.items:
            if c.skill_id == key:
                out.append((op.name, c))
    return out


def check_clause(key: str, skill, owner_name: Optional[str] = None,
                 owner_skills: Optional[List[str]] = None) -> ClauseCheck:
    """造场景 → 核对一条 clause。

    `owner_name` 给了就是 **L4 口径**（真名路径）：把"合成干员 + 单条技能"换成
    **真名 + 她真实的技能槽**，场景其余部分完全一样——核的不再是"这条技能定义对不对"，
    而是"**这位干员在基建里真的按这条技能结算**"（名称解析 → 技能挂载 → 进流水账）。
    """
    hint = HINTS.get(key, {})
    owner = owner_name or SYNTH
    if hint.get("unreachable"):
        # 期望"**不**产生非零速率贡献"：构造一个本该触发它的场景，看它是否真的不出现
        facilities, _home, _t = _facility_specs(skill, hint, [])
        world = build_base_layout({"facilities": facilities})
        found = [c for _n, c in _contribution_of(world, key)
                 if c.bucket != Bucket.EVENT and c.value != 0]
        if found:
            return ClauseCheck(key, skill, "bad",
                               f"文档说不可达，却出现了 {found[0].value} 的速率贡献")
        return ClauseCheck(key, skill, "unreachable", hint["unreachable"])

    producers = _producers_for(skill, exclude=owner)
    if key == "dorm_rec_toone_000#1":            # M17 元修正：要靠"被强化者"在场
        producers = []
    facilities, home, target_name = _facility_specs(skill, hint, producers,
                                                   owner_name=owner,
                                                   owner_skills=owner_skills)
    if key == "dorm_rec_toone_000#1":
        # 被点名提供者（推进之王）必须在同一间宿舍里，且他要真有宿舍群体回复技能
        for f in facilities:
            if f["type"] == "宿舍" and any(o.get("name") == owner for o in f["operators"]):
                f["operators"].append({"name": "推进之王", "elite": 2, "level": 30, "mood": 12})
                break
    world = build_base_layout({"facilities": facilities})

    if hint.get("event"):
        # M15a「患难之交」：是**进驻事件**（心情互换），不进速率 → 期望速率侧**不产生非零**贡献
        # （数据里这条 clause 是 value=0 的骨架，会留一条 0 值记录，属噪音不属错误），
        # 事件侧由 `rules.apply_entry_events` 处理（见 tests 里的机制用例）。
        nonzero = [c for _n, c in _contribution_of(world, key)
                   if c.bucket != Bucket.EVENT and c.value != 0]
        if nonzero:
            return ClauseCheck(key, skill, "bad",
                               f"事件类却产生了非零速率贡献：{nonzero[0].label} {nonzero[0].value}")
        return ClauseCheck(key, skill, "event", "进驻事件（M15a）：走 apply_entry_events，不进速率")

    found = _contribution_of(world, key)
    if key == "dorm_rec_toone_000#1":
        # 元修正：增量并入**被强化者**的贡献（同 group 同 skill_id），故按 detail 找
        boosted = [(n, c) for n, c in
                   [(o.name, c) for o in world.all_operators()
                    for c in mood_ledger(world, o.name).items]
                   if "头号陪练" in (c.detail or "")]
        if not boosted:
            return ClauseCheck(key, skill, "bad", "没有找到被强化的那条贡献（+0.3 未生效）")
        return ClauseCheck(key, skill, "boost",
                           f"并入「推进之王」的宿舍群体回复小计（{len(boosted)} 条增量）",
                           expected=skill.value, got=boosted[0][1].value)

    if not found:
        # 变量门槛没满足（场景里凑不出那么多变量）→ 只核对了"它不生效"这一半
        variables = collect_variables(world)
        if skill.var_min is not None and skill.var_name and \
                not variables.at_least(skill.var_name, skill.var_min):
            return ClauseCheck(key, skill, "var",
                               f"变量门槛未满足（{skill.var_name} 需 ≥ {skill.var_min}，"
                               f"场景里只有 {variables.get(skill.var_name)}）→ "
                               f"已核对「不生效」，生效路径未覆盖")
        if hint.get("alone"):
            return ClauseCheck(key, skill, "bad", "「独自一人」场景下没有出现（条件或范围不对）")
        return ClauseCheck(key, skill, "bad", "构造出满足条件的场景后，流水账里仍然没有它")

    # —— 值 / 桶核对 ——
    bucket_want = Bucket.RECOVER if skill.kind in (
        SkillKind.CC_RECOVER, *DORM_KINDS) else Bucket.CONSUME
    owner_ones = [(n, c) for n, c in found if c.owner == owner]
    pick = owner_ones[0][1] if owner_ones else found[0][1]

    if skill.kind == SkillKind.ELIMINATE_SELF:
        if not pick.zeroes_group:
            return ClauseCheck(key, skill, "bad", "消除类没有设置 zeroes_group（不会归零）")
        return ClauseCheck(key, skill, "eliminate", f"归零组 {pick.zeroes_group}")

    if skill.pool:
        if not pick.pool_share:
            return ClauseCheck(key, skill, "bad", "池分配没有记录 pool_share")
        dorm = world.facility_of(pick.target)
        recipients = len([o for o in dorm.operators if o.mood < 24]) if dorm else 0
        want = skill.value / recipients if recipients else Decimal("0")
        if pick.value != want:
            return ClauseCheck(key, skill, "bad",
                               f"池分配每份 {pick.value}，按 {recipients} 人应得 {want}",
                               expected=want, got=pick.value)
        return ClauseCheck(key, skill, "pool", f"总额 {skill.value} 由 {recipients} 人平分",
                           expected=want, got=pick.value)

    if pick.bucket != bucket_want:
        return ClauseCheck(key, skill, "bad",
                           f"落在 {pick.bucket.value} 桶，按 {skill.kind.value} 应在 {bucket_want.value} 桶")

    # 期望值：**逐条实例**独立折算（谁身上的这条贡献，就按谁所在设施重算一遍）
    # —— 这样才顺带核对了"作用范围"：同设施全体 / 中枢 → 工作设施 / 中枢 → 宿舍 /
    #    扩散到 room2 的每一条路径，都要落在它该落的那个人的账上。
    variables = collect_variables(world)
    for holder, c in found:
        if c.bucket != bucket_want:
            return ClauseCheck(key, skill, "bad",
                               f"{holder} 身上的这条落在 {c.bucket.value} 桶，"
                               f"按 {skill.kind.value} 应在 {bucket_want.value} 桶")
        op = world.get_operator(c.target) or world.get_operator(holder)
        facility = world.facility_of(c.target) or world.facility_of(holder)
        ok, want = _independent_expected(skill, variables, world, facility, op)
        if not ok:
            return ClauseCheck(key, skill, "bad",
                               f"{holder} 身上出现了贡献，但变量门槛并不满足（{c.value}）")
        if c.value != want:
            return ClauseCheck(key, skill, "bad",
                               f"{holder} 身上数值不符：流水账 {c.value}，按模板口径应为 {want}",
                               expected=want, got=c.value)
    want_n, why = expected_scopes(skill, world, owner)
    if want_n is not None and len(found) != want_n:
        return ClauseCheck(key, skill, "bad",
                           f"作用范围不符：按「{why}」应落在 {want_n} 名干员账上，实际 {len(found)} 条")
    return ClauseCheck(key, skill, "ok",
                       f"{pick.label} → {pick.value}（{len(found)} 条实例"
                       + (f"：{why}" if why else "") + "）",
                       expected=pick.value, got=pick.value)


def check_clauses() -> List[ClauseCheck]:
    """L2：把全部 clause 过一遍。"""
    return [check_clause(k, s) for k, s in SKILLS.items()]


# ============================================================================
# L4：干员级生效性（真名路径）＋ 组合型技能双向对照
# ============================================================================
# 为什么还要 L4：L2 用的是**合成干员**（`SYNTH`）**手工注入单条 skill_id**——
# 它证明了"这条技能定义对不对"，但没走「真名 → `DEFAULT_OPERATORS` → 技能槽 → 进流水账」
# 这条路；而且 L2 只核了"条件满足时数值对不对"，没核"**条件不满足时不该生效**"。
# 组合型技能（要第二名干员 / 阵营成员 / 变量产出者才成立）最容易错的就是后一半。
@dataclass
class OperatorCheck:
    """一名干员的 L4 结论（L4-a 真名挂载 / L4-b 真名结算 各一条）。"""
    name: str
    status: str          # ok / bad
    detail: str = ""
    clauses: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass
class ComboCheck:
    """一条**组合型** clause 的「有搭档 / 没搭档」双向对照结论。"""
    key: str
    name: str
    combo: str           # 搭档类型
    status: str          # ok / bad / var
    detail: str = ""
    rate_with: Optional[Decimal] = None      # A 场景（搭档在场）里那个人的净速率
    rate_without: Optional[Decimal] = None   # B 场景（搭档不在/不匹配）里同一个人的净速率

    @property
    def delta(self) -> Optional[Decimal]:
        if self.rate_with is None or self.rate_without is None:
            return None
        return self.rate_with - self.rate_without

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "var")


_CLAUSE_HOLDERS: Optional[Dict[str, List[str]]] = None


def clause_holders() -> Dict[str, List[str]]:
    """`clause key → [持有者, ...]`（来自 `DEFAULT_OPERATORS` 的干员↔技能映射，算一次）。"""
    global _CLAUSE_HOLDERS
    if _CLAUSE_HOLDERS is None:
        from data.skills_data import DEFAULT_OPERATORS
        acc: Dict[str, List[str]] = defaultdict(list)
        for name, keys in DEFAULT_OPERATORS.items():
            for k in keys:
                acc[k].append(name)
        _CLAUSE_HOLDERS = dict(acc)
    return _CLAUSE_HOLDERS


def _hits_of(world, key: str) -> List[Tuple[str, object]]:
    """`key` 在世界里的贡献实例 `[(账主, Contribution), ...]`。

    ⚠️ **元修正（M17「头号陪练」）是特例**：它自己不产生一条独立贡献，而是把 +0.3
    并入**被强化者**（推进之王）那条宿舍群体回复里（同 group 同 skill_id），
    所以只能按 `detail` 里的技能名找——L2 的 M17 分支也是这么找的。
    """
    if HINTS.get(key, {}).get("boost"):
        return [(o.name, c) for o in world.all_operators()
                for c in mood_ledger(world, o.name).items
                if (SKILLS[key].name or "") in (c.detail or "")]
    return [(n, c) for n, c in _contribution_of(world, key)]


def check_operator_attachment() -> List[OperatorCheck]:
    """**L4-a 真名挂载**：178 名干员逐个用真名建一遍，技能槽必须与技能库逐条一致。

    盯的是"名称解析 → 技能挂载"这条路：名字认不出（英文名/id 没收录）、
    `DEFAULT_OPERATORS` 与 `operators.txt` 脱节、精英化门槛把技能全挡掉——
    这些都能让一位干员在基建里**一条技能都不生效**，而 L2 用合成干员时看不出来。
    """
    from data.skills_data import DEFAULT_OPERATORS
    from store.layout import build_operator

    out: List[OperatorCheck] = []
    for name in sorted(DEFAULT_OPERATORS):
        want = set(DEFAULT_OPERATORS[name])
        op = build_operator({"name": name})
        got = set(op.skill_ids or ())
        if not got:
            out.append(OperatorCheck(name, "bad", "真名建出来一条技能都没有（名称解析断了）",
                                     len(want)))
        elif got != want:
            out.append(OperatorCheck(
                name, "bad",
                f"技能槽与技能库不一致：少 {sorted(want - got)[:3]}，多 {sorted(got - want)[:3]}",
                len(want)))
        else:
            out.append(OperatorCheck(name, "ok", f"{len(want)} 条 clause 全部挂上", len(want)))
    return out


def _real_name_world(key: str, skill, name: str):
    """按 L2 的场景口径，用**真名 + 她真实的技能槽**造一个世界（L4-b 用）。"""
    from data.skills_data import DEFAULT_OPERATORS

    hint = HINTS.get(key, {})
    producers = _producers_for(skill, exclude=name)
    facilities, _home, _target = _facility_specs(
        skill, hint, producers, owner_name=name,
        owner_skills=list(DEFAULT_OPERATORS[name]))
    if key == "dorm_rec_toone_000#1":
        for f in facilities:
            if f["type"] == "宿舍" and any(o.get("name") == name for o in f["operators"]):
                f["operators"].append({"name": "推进之王", "elite": 2, "level": 30, "mood": 12})
                break
    return build_base_layout({"facilities": facilities})


def check_operator_settlement(status_by_key: Dict[str, str]) -> List[OperatorCheck]:
    """**L4-b 真名结算**：真名 + 她真实技能槽造场景，断言她自己的技能**真的进了流水账**。

    L2 把 `skill_ids` 手工塞给合成干员，绕过了"这个人身上到底挂了什么"；
    L4-b 走完整条路，并把命中的那条 clause 的**数值**按模板口径再核一遍。
    """
    from data.skills_data import DEFAULT_OPERATORS

    out: List[OperatorCheck] = []
    for name in sorted(DEFAULT_OPERATORS):
        keys = [k for k in DEFAULT_OPERATORS[name] if k in SKILLS]
        pick = next((k for k in keys
                     if not HINTS.get(k, {}).get("unreachable")
                     and not HINTS.get(k, {}).get("event")), None)
        if pick is None:
            out.append(OperatorCheck(name, "ok",
                                     "只有事件 / 不可达类 clause（不产生速率），L2 已覆盖",
                                     len(keys)))
            continue
        world = _real_name_world(pick, SKILLS[pick], name)
        hit = [k for k in keys if k in SKILLS and _hits_of(world, k)]
        if not hit:
            out.append(OperatorCheck(
                name, "bad",
                f"真名场景（{pick}）里她的 {len(keys)} 条技能一条都没进流水账", len(keys)))
            continue
        # 数值核对：挑一条 L2 判为 ok 的命中 clause，逐条实例按模板口径独立折算
        plain = next((k for k in hit if status_by_key.get(k) == "ok"), None)
        wrong = ""
        if plain is not None:
            variables = collect_variables(world)
            for holder, c in _contribution_of(world, plain):
                if c.bucket == Bucket.EVENT:
                    continue
                op = world.get_operator(c.target) or world.get_operator(holder)
                fac = world.facility_of(c.target) or world.facility_of(holder)
                ok, want = _independent_expected(SKILLS[plain], variables, world, fac, op)
                if ok and c.value != want:
                    wrong = f"{plain}：{holder} 身上 {c.value} ≠ 模板口径 {want}"
                    break
        if wrong:
            out.append(OperatorCheck(name, "bad", wrong, len(keys)))
        else:
            out.append(OperatorCheck(
                name, "ok",
                f"{len(hit)}/{len(keys)} 条进了流水账（{plain or pick} 的值已核）", len(keys)))
    return out


#: 可以双向对照的组合类型（"撤掉搭档后必须不再生效"）。
COMBO_KINDS = ("共事", "定向", "阵营计数", "自身阵营", "变量", "元修正", "独自一人",
               "宿舍未满计数", "宿舍他人在场")


def _combo_kind(skill, hint: dict) -> Optional[str]:
    """这条 clause 属于哪一类"组合型"（要第二名干员 / 阵营 / 变量才成立）；不适用返回 None。"""
    if hint.get("unreachable") or hint.get("event"):
        return None
    if hint.get("boost"):
        return "元修正"
    if hint.get("alone"):
        return "独自一人"
    if hint.get("cc_mate") or hint.get("mate"):
        return "共事"
    if hint.get("target_name") or hint.get("target_factions") \
            or hint.get("target_mood") is not None:
        return "定向"
    if hint.get("owner_factions"):
        return "自身阵营"
    if skill.count_faction:
        return "阵营计数"
    if skill.var_name:
        return "变量"
    if skill.basis == "dorm_others":
        return "宿舍他人在场"
    if skill.basis in ("dorm_unfull", "dorm_operator"):
        return "宿舍未满计数"
    return None


def _drop(scene: List[dict], name: str) -> None:
    for f in scene:
        f["operators"] = [o for o in f["operators"] if o.get("name") != name]


def _misname(scene: List[dict], old: str, new: str = "路人丙") -> str:
    """把某人的**名字与阵营都换掉**（定向加成要"目标不匹配"）；返回改后的名字。"""
    for f in scene:
        for o in f["operators"]:
            if o.get("name") == old:
                o["name"] = new
                o.pop("factions", None)
                o.pop("trait", None)
    return new


def _strip_faction(scene: List[dict], name: str) -> None:
    """只摘掉某人的阵营/标签，**人仍留在原房间**（保持在场人数不变）。"""
    for f in scene:
        for o in f["operators"]:
            if o.get("name") == name:
                o.pop("factions", None)
                o.pop("trait", None)


#: 元修正（M17）的核对落点：宿舍里必须真有"格拉斯哥帮"成员，"头号陪练"的 +0.3 才有去处。
M17_TARGET = "格拉斯哥帮甲"
#: 把搭档"变成路人"时用的名字（认不出 → 一条技能都不带）。
NOBODY = "路人丁"


def _combo_scenes(key: str, skill, hint: dict
                  ) -> Optional[Tuple[List[dict], List[dict], Dict[str, str]]]:
    """造一对场景：**A = 搭档在场**，**B = 搭档不在 / 不匹配**（B 由 A 变形而来）。

    B 一定从 A 复制后只改"搭档那一件事"，这样净速率之差就**只反映这条组合条件**。

    ⚠️ **"撤掉搭档"一律改成"改名 / 摘阵营，人留在原房间"**：控制中枢有一条
    「中枢 N 人，每人 −0.05」的全局减免——把搭档**搬走**会连这份减免一起改掉，
    差值就不再只反映这条组合条件了（实测：德才兼备的差值从 −0.10 变成 −0.20，
    看起来像"引擎多算了一倍"，其实是中枢人数少了一个）。
    只有「独自一人」「宿舍他人在场」这两类**条件本身就是在场人数**，才真去增删人。

    返回 `(A, B, 改名表)`：改名表 = `{A 里的名字: B 里的名字}`——"定向"要换掉目标的名字，
    不带上它，后面拿 A 里的名字去 B 里查人就会 `KeyError`。
    `None` = 这一类没有可靠的"撤掉"手法（不硬凑）。
    """
    kind = _combo_kind(skill, hint)
    if kind is None:
        return None
    producers = [] if key == "dorm_rec_toone_000#1" else _producers_for(skill)
    a_scene, _home, target = _facility_specs(skill, hint, producers)
    if key == "dorm_rec_toone_000#1":
        for f in a_scene:
            if f["type"] == "宿舍" and any(o.get("name") == SYNTH for o in f["operators"]):
                f["operators"].append({"name": "推进之王", "elite": 2, "level": 30, "mood": 12})
                f["operators"].append({"name": M17_TARGET, "factions": ["格拉斯哥帮"],
                                       "elite": 2, "level": 30, "mood": 12})
                break
    b_scene = [dict(f, operators=[dict(o) for o in f["operators"]]) for f in a_scene]
    rename: Dict[str, str] = {}

    if kind == "共事":
        # 搭档**改名留在原地**（认不出的名字 ⇒ 条件不成立），在场人数不变
        mate = hint.get("cc_mate") or hint.get("mate")
        rename[mate] = _misname(b_scene, mate, NOBODY)
    elif kind == "定向":
        if hint.get("target_name"):
            rename[target] = _misname(b_scene, target)
        elif hint.get("target_factions"):
            for f in b_scene:
                for o in f["operators"]:
                    if o.get("name") == target:
                        o.pop("factions", None)
                        o.pop("trait", None)
        elif hint.get("target_mood") is not None:
            for f in b_scene:
                for o in f["operators"]:
                    if o.get("name") == target:
                        o["mood"] = 24          # 心情门槛不满足
        else:
            return None
    elif kind == "自身阵营":
        for f in b_scene:                        # 撤掉持有者自己的阵营 → 计数归 0
            for o in f["operators"]:
                if o.get("name") == SYNTH:
                    o.pop("factions", None)
                    o.pop("trait", None)
    elif kind == "阵营计数":
        _strip_faction(b_scene, "阵营甲")         # 只摘阵营，人留在中枢（人数不变）
        _strip_faction(b_scene, "阵营乙")
    elif kind == "变量":
        if not producers:
            return None
        names = {p["name"] for p in producers}
        b_scene = [f for f in b_scene            # 产出者那间房整间去掉 → 变量归 0
                   if not (f["type"] == "宿舍"
                           and {o.get("name") for o in f["operators"]} == names)]
    elif kind == "元修正":
        # 把"强化者"变成没有技能的人（**仍留在宿舍里**，人数不变）：合成干员的技能是
        # 显式注入的，改名字摘不掉，必须直接清空 `skill_ids`。
        for f in b_scene:
            for o in f["operators"]:
                if o.get("name") == SYNTH:
                    o["skill_ids"] = ["__none__"]
    elif kind == "独自一人":
        for f in b_scene:                        # 反过来：给她加一名同事 → 不再是"独自"
            if any(o.get("name") == SYNTH for o in f["operators"]):
                f["operators"].append({"name": MATE, "elite": 2, "level": 30, "mood": 24})
                break
    elif kind == "宿舍他人在场":
        for f in b_scene:                        # 撤掉同宿舍的其他人
            if any(o.get("name") == SYNTH for o in f["operators"]):
                f["operators"] = [o for o in f["operators"] if o.get("name") == SYNTH]
                break
    elif kind == "宿舍未满计数":
        for f in b_scene:                        # 其他人心情全设满 → "未满人数"归 0
            for o in f["operators"]:
                if o.get("name") != SYNTH:
                    o["mood"] = 24
    else:
        return None
    return a_scene, b_scene, rename


def check_combos(status_by_key: Dict[str, str]) -> List[ComboCheck]:
    """**L4-c 组合技能双向对照**：搭档在场 vs 不在（或不匹配），净速率必须真的变。

    只核"条件满足时数值对不对"是不够的——条件漏判（比如"同宿舍有 X 才加成"写成无条件）
    在满足条件的场景里**数值完全正确**，只有把搭档撤掉才会暴露。

    `status`：`ok` 双向都对 / `var` A 场景就凑不出变量（L2 同样没覆盖那一半）/
    `bad` 不符。
    """
    out: List[ComboCheck] = []
    for key, skill in sorted(SKILLS.items()):
        hint = HINTS.get(key, {})
        combo = _combo_kind(skill, hint)
        if combo is None:
            continue
        if status_by_key.get(key) == "var":
            out.append(ComboCheck(key, skill.name, combo, "var",
                                  "A 场景凑不出变量门槛（L2 同样只覆盖了「不生效」那一半）"))
            continue
        scenes = _combo_scenes(key, skill, hint)
        if scenes is None:
            out.append(ComboCheck(key, skill.name, combo, "bad", "没有可靠的撤掉搭档手法"))
            continue
        world_a = build_base_layout({"facilities": scenes[0]})
        world_b = build_base_layout({"facilities": scenes[1]})
        rename = scenes[2]

        if combo == "元修正":
            person = M17_TARGET
            hit = [(o.name, c) for o in world_a.all_operators()
                   for c in mood_ledger(world_a, o.name).items
                   if (skill.name or "") in (c.detail or "")]
        else:
            hit = [(n, c) for n, c in _contribution_of(world_a, key)
                   if c.bucket != Bucket.EVENT]
            person = (hit[0][1].target or hit[0][0]) if hit else ""
        if not hit:
            out.append(ComboCheck(key, skill.name, combo, "bad",
                                  "A 场景（搭档在场）里这条 clause 没有出现"))
            continue

        person_b = rename.get(person, person)     # "定向"把目标改名了 → B 里查新名字
        rate_a = compute_net_rate(world_a, person)
        rate_b = compute_net_rate(world_b, person_b)
        moved = rate_a - rate_b                   # 正 = A 的净消耗更大（消耗类）/ 更小（回复类）

        if combo == "元修正":
            # 「头号陪练」把 +0.3 并入**被强化者**的宿舍群体回复：撤掉强化者后该回复少 0.3
            if moved != -skill.value:
                out.append(ComboCheck(key, skill.name, combo, "bad",
                                      f"撤掉强化者后 {person} 少恢复 {moved}，"
                                      f"应少 {skill.value}", rate_a, rate_b))
            else:
                out.append(ComboCheck(key, skill.name, combo, "ok",
                                      f"{person}：{rate_a} → {rate_b}（少恢复 {skill.value}）",
                                      rate_a, rate_b))
            continue

        if skill.kind == SkillKind.ELIMINATE_SELF:
            # 消除类**不产生速率**：它只把目标"自身技能"那一组归零。目标本身没有心情技能时，
            # 净速率根本不会变——所以这里核**结构性**事实：A 有消除标记、B 没有。
            if _contribution_of(world_b, key):
                out.append(ComboCheck(key, skill.name, combo, "bad",
                                      "搭档不匹配时消除标记仍然存在", rate_a, rate_b))
            else:
                out.append(ComboCheck(key, skill.name, combo, "ok",
                                      "A 里有消除标记、不匹配时消失（消除类不改净速率）",
                                      rate_a, rate_b))
            continue

        c0 = hit[0][1]
        # 账本口径：`I = 消耗 − 回复` ⇒ 消耗条目 +value、回复条目 −value
        effect = c0.value if c0.bucket == Bucket.CONSUME else -c0.value
        if combo == "宿舍他人在场":
            # 这一类的"组合条件"**就是同宿舍的人数**，改人数必然连**宿舍基础回复**
            # （1.5 + 0.1×人数 + 氛围）一起改，差值不可能只剩这条技能 —— 所以只核
            # 方向与"确实变了"，不硬求等于技能值。
            if moved == 0 or (moved > 0) != (effect > 0):
                out.append(ComboCheck(key, skill.name, combo, "bad",
                                      f"少一个人之后 {person} 的净速率差 {moved}，"
                                      f"方向应随 {effect}", rate_a, rate_b))
            else:
                out.append(ComboCheck(key, skill.name, combo, "ok",
                                      f"{person}：{rate_a} → {rate_b}（差 {moved}，"
                                      f"含宿舍基础回复变化）", rate_a, rate_b))
            continue
        if moved != effect:
            out.append(ComboCheck(key, skill.name, combo, "bad",
                                  f"撤掉搭档后 {person} 的净速率差 {moved}，"
                                  f"账本上这条是 {effect}", rate_a, rate_b))
            continue
        out.append(ComboCheck(key, skill.name, combo, "ok",
                              f"{person}：{rate_a} → {rate_b}（差 {moved}）", rate_a, rate_b))
    return out


# ============================================================================
# L3：上游描述对照
# ============================================================================
class DescCheck:
    def __init__(self, key: str, skill, status: str, detail: str = ""):
        self.key = key
        self.skill = skill
        self.status = status      # ok / skip / known / bad
        self.detail = detail

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "skip", "known")


def _numbers_near(text: str, value: Decimal, window: int = 14) -> List[str]:
    """描述里 |value| 每次出现处的**上下文词**（用来判方向：消耗 / 恢复）。"""
    want = {str(abs(value)), str(abs(value) * 1000).split(".")[0]}
    out = []
    for m in NUM_RE.finditer(text):
        if m.group(0).lstrip("-") not in want:
            continue
        out.append(text[max(0, m.start() - window):m.end() + window])
    return out


def check_description(key: str, skill, desc: str) -> DescCheck:
    """一条 clause 的"数据 value ↔ 上游描述"核对。"""
    text = strip_tags(desc)
    if skill.value == 0 and L3_SKIP_ZERO:
        return DescCheck(key, skill, "skip", "value=0（消除/事件类），描述里本就没有速率数字")
    ctxs = _numbers_near(text, skill.value)
    if not ctxs:
        return DescCheck(key, skill, "bad",
                         f"描述里找不到 {abs(skill.value)} 这个数（数据表 value={skill.value}）")
    # 方向：消耗侧类技能是"消耗增减"，若描述把它写成"恢复"，方向口径就不一致
    consume_side = skill.kind in (SkillKind.SELF_CONSUME, SkillKind.FACILITY_CONSUME,
                                  SkillKind.ROOM_OTHERS_CONSUME)
    if consume_side:
        for ctx in ctxs:
            if "消耗" in ctx:
                return DescCheck(key, skill, "ok", f"描述用「消耗」（{ctx.strip()[:28]}）")
        for ctx in ctxs:
            if "恢复" in ctx:
                if key in L3_KNOWN_DIFFS:
                    return DescCheck(key, skill, "known", L3_KNOWN_DIFFS[key])
                return DescCheck(key, skill, "bad",
                                 f"数据按「消耗 {skill.value}」建模，描述却写「恢复」：{ctx.strip()[:30]}")
    return DescCheck(key, skill, "ok", f"数字命中（{ctxs[0].strip()[:28]}）")


def check_descriptions(upstream: dict) -> List[DescCheck]:
    """L3：全部 clause 与上游 buff 描述对照。"""
    buffs = upstream.get("buffs", {})
    out = []
    for key, s in SKILLS.items():
        bid = buff_id_of(base_skill_id(key))
        b = buffs.get(bid)
        if b is None:
            out.append(DescCheck(key, s, "bad", f"上游没有这条 buff（{bid}）"))
            continue
        out.append(check_description(key, s, b.get("description", "")))
    return out


# ============================================================================
# 台账（buff 级 755 行）：把 clause 级结论汇总回 buff
# ============================================================================
def read_registry() -> List[dict]:
    import csv
    with open(REGISTRY_TXT, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def check_registry() -> Tuple[List[str], dict]:
    """台账 ↔ clause 的双向核对：**上游 755 条 buff 一条都不能漏**。

    不变式：
      - `modeled=yes` 的 buff 必须至少对应 1 条 clause（否则"标了建模却没实现"）；
      - `modeled=no` 的 buff 必须一条 clause 也没有（否则"没标建模却实现了"）。
    返回 `(问题清单, 统计)`。
    """
    rows = read_registry()
    clause_count = Counter()
    for key in SKILLS:
        clause_count[buff_id_of(base_skill_id(key))] += 1
    issues: List[str] = []
    for r in rows:
        n = clause_count.get(r["buff_id"], 0)
        if r["modeled"] == "yes" and n == 0:
            issues.append(f"{r['buff_id']}（{r['name']}）标了已建模，却没有对应 clause")
        if r["modeled"] == "no" and n > 0:
            issues.append(f"{r['buff_id']}（{r['name']}）标了不建模，却有 {n} 条 clause")
    stats = {
        "rows": len(rows),
        "modeled": sum(1 for r in rows if r["modeled"] == "yes"),
        "unmodeled": sum(1 for r in rows if r["modeled"] == "no"),
        "tiers": dict(Counter(r["tier"] for r in rows)),
        "buff_with_clauses": len(clause_count),
    }
    return issues, stats


# ============================================================================
# 报告
# ============================================================================
def build_report(clauses: List[ClauseCheck], descs: Optional[List[DescCheck]],
                 tpl_issues: List[str], upstream_path: Optional[Path],
                 attach: Optional[List[OperatorCheck]] = None,
                 settle: Optional[List[OperatorCheck]] = None,
                 combos: Optional[List[ComboCheck]] = None) -> str:
    by_buff: Dict[str, List[ClauseCheck]] = defaultdict(list)
    for c in clauses:
        by_buff[buff_id_of(base_skill_id(c.key))].append(c)
    desc_by_key = {d.key: d for d in (descs or [])}
    reg_issues, reg = check_registry()

    status = Counter(c.status for c in clauses)
    lines = []
    lines.append("# 技能核对报告（全量）")
    lines.append("")
    lines.append("> **生成物**：由 `scripts/verify_skills.py --report` 自动生成，不要手改。")
    lines.append("> 四层核对 = 模板级（L1）/ clause 级（L2）/ 上游描述对照（L3）/ "
                 "**干员级生效性与组合技能双向对照（L4）**；"
                 "口径与模板字典见 `documents/04-技能分类大纲.md`。")
    lines.append("")
    lines.append("## 一、总览")
    lines.append("")
    lines.append(f"- 心情 clause（`skills.SKILLS`）：**{len(clauses)}** 条")
    lines.append(f"- 上游 buff 台账：**{reg['rows']}** 条（`data/skills_registry.txt`）"
                 f"——已建模 {reg['modeled']} / 登记不建模 {reg['unmodeled']}")
    lines.append(f"  - 轴 A 分布："
                 + "、".join(f"{k} {v} 条" for k, v in sorted(reg["tiers"].items())))
    lines.append(f"  - 台账双向核对："
                 f"{'✅ 755 条 buff 全部对上（modeled=yes 都有 clause，modeled=no 都没有）' if not reg_issues else '❌ ' + str(len(reg_issues)) + ' 处不一致'}")
    lines.append(f"- 模板：**{len(TEMPLATES)}** 个"
                 f"（已建模 {sum(1 for t in TEMPLATES.values() if t.modeled)} / "
                 f"登记不建模 {sum(1 for t in TEMPLATES.values() if not t.modeled)}）")
    lines.append(f"- L1 模板自洽：{'✅ 通过' if not tpl_issues else '❌ ' + str(len(tpl_issues)) + ' 处问题'}")
    lines.append(f"- L2 clause 级：✅ {status['ok']} 条正常 / "
                 f"⚪ {status.get('unreachable', 0)} 条按文档不可达 / "
                 f"⚪ {status.get('event', 0)} 条事件类 / "
                 f"⚪ {status.get('eliminate', 0)} 条消除类 / "
                 f"⚪ {status.get('pool', 0)} 条池分配 / "
                 f"⚪ {status.get('boost', 0)} 条元修正 / "
                 f"❌ {status.get('bad', 0)} 条不符")
    if descs is None:
        lines.append("- L3 描述对照：⚪ **未运行**（没找到上游仓库，用 `--agd <路径>` 指定）")
    else:
        ds = Counter(d.status for d in descs)
        lines.append(f"- L3 描述对照：✅ {ds['ok']} 条一致 / "
                     f"⚪ {ds.get('skip', 0)} 条无数字可对（消除/事件类）/ "
                     f"⚪ {ds.get('known', 0)} 条已登记差异 / "
                     f"❌ {ds.get('bad', 0)} 条不符")
    if attach is not None and settle is not None:
        lines.append(f"- L4 真名挂载：✅ {sum(1 for c in attach if c.ok)}/{len(attach)} 名干员的"
                     f"技能槽与技能库逐条一致 / ❌ {sum(1 for c in attach if not c.ok)} 名不符")
        lines.append(f"- L4 真名结算：✅ {sum(1 for c in settle if c.ok)}/{len(settle)} 名干员"
                     f"在基建里真的按自己的技能结算 / ❌ {sum(1 for c in settle if not c.ok)} 名不符")
    if combos is not None:
        cstat = Counter(c.status for c in combos)
        kinds = Counter(c.combo for c in combos)
        lines.append(f"- L4 组合技能双向对照：✅ {cstat.get('ok', 0)} 条（撤掉搭档/阵营/变量后"
                     f"确实不再生效）/ ⚪ {cstat.get('var', 0)} 条变量凑不出 / "
                     f"❌ {cstat.get('bad', 0)} 条不符")
        lines.append("  - 覆盖类型：" + "、".join(f"{k} {v} 条"
                                                for k, v in sorted(kinds.items())))
    lines.append("")

    if tpl_issues:
        lines.append("### 模板自洽问题（L1）")
        lines.append("")
        for i in tpl_issues:
            lines.append(f"- ❌ {i}")
        lines.append("")

    bad = [c for c in clauses if not c.ok]
    lines.append("### 需要处理的差异")
    lines.append("")
    if not bad and not (descs and [d for d in descs if not d.ok]) and not reg_issues:
        lines.append("没有。250 条 clause 都能在**满足它条件的场景**里按模板口径生效，"
                     "上游描述里的数字与数据表一致，755 条 buff 也全部有归宿。")
    for i in reg_issues:
        lines.append(f"- ❌ 台账：{i}")
    for c in bad:
        lines.append(f"- ❌ **{c.key}**（{c.skill.name} / {c.skill.template_id}）：{c.detail}")
    for d in (descs or []):
        if not d.ok:
            lines.append(f"- ❌ **{d.key}**（{d.skill.name}）描述对照：{d.detail}")
    for key, why in L3_KNOWN_DIFFS.items():
        d = desc_by_key.get(key)
        if d is not None and d.status == "known":
            lines.append(f"- ⚠️ **{key}**（已知差异，待拍板）：{why}")
    lines.append("")

    lines.append("## 二、模板使用（L1）")
    lines.append("")
    lines.append("| 模板 | 名称 | 轴 A | 叠加 | clause 数 |")
    lines.append("|---|---|---|---|---|")
    for t in template_summary():
        lines.append(f"| {t['id']} | {t['name']} | {t['tier']} | {t['stacking']} | {t['clauses']} |")
    lines.append("")

    lines.append("## 三、逐条 clause（L2 / L3）")
    lines.append("")
    lines.append("`状态` 列：`ok` 已核对生效 / `unreachable` 按文档不可达 / `event` 事件类 / "
                 "`eliminate` 消除类 / `pool` 池分配 / `boost` 元修正 / `bad` 不符")
    lines.append("")
    lines.append("| clause | 技能 | 类型 | 模板 | value | 状态 | 说明 | 描述对照 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in sorted(clauses, key=lambda x: x.key):
        d = desc_by_key.get(c.key)
        dcell = "—" if d is None else ("✅" if d.status == "ok" else
                                       ("⚪" if d.status in ("skip", "known") else "❌"))
        mark = "✅" if c.status in ("ok", "unreachable", "event") else (
            "⚪" if c.status in ("eliminate", "pool", "boost") else "❌")
        lines.append(f"| `{c.key}` | {c.skill.name} | {c.skill.kind.value} | {c.skill.template_id} "
                     f"| {c.skill.value} | {mark} {c.status} | {c.detail} | {dcell} |")
    lines.append("")

    lines.append("## 四、逐条 buff（上游 755 条台账）")
    lines.append("")
    lines.append("| buff | 名称 | 房间 | 轴 A | 模板 | 已建模 | 心情 clause | 核对 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in read_registry():
        cs = by_buff.get(row["buff_id"], [])
        if not cs:
            cell = "—（非心情，登记不建模）" if row["modeled"] == "no" else "❌ 标了已建模却没有 clause"
        else:
            flags = [c.status for c in cs]
            cell = "✅ 全部核对" if all(f in ("ok", "unreachable", "event", "eliminate",
                                              "pool", "boost") for f in flags) \
                else "❌ " + "、".join(f for f in flags if f == "bad")
        lines.append(f"| `{row['buff_id']}` | {row['name']} | {row['room_type']} | {row['tier']} "
                     f"| {row['template_ids']} | {row['modeled']} | {len(cs)} | {cell} |")
    lines.append("")

    lines.append("## 五、干员级生效性与组合技能（L4）")
    lines.append("")
    lines.append("L2 用的是**合成干员 + 手工注入单条 skill_id**（只证明「这条技能定义对不对」），"
                 "L4 补的是另外两件事：")
    lines.append("")
    lines.append("1. **真名路径**：178 名干员逐个走「名字 → `DEFAULT_OPERATORS` → 技能槽 → 进流水账」，"
                 "确认**这个人**在基建里真的按自己的技能结算（而不是名字没认出来 / 技能没挂上）；")
    lines.append("2. **组合技能双向对照**：把搭档 / 阵营成员 / 变量产出者**撤掉或变成不匹配**，"
                 "数值必须真的变回去——条件漏判（写成无条件）在「满足条件」的场景里数值完全正确，"
                 "只有反向对照才抓得住。")
    lines.append("")
    if attach is not None:
        bad_a = [c for c in attach if not c.ok]
        lines.append(f"**L4-a 真名挂载**：{len(attach)} 名干员，"
                     + ("✅ 全部与技能库逐条一致。" if not bad_a
                        else f"❌ {len(bad_a)} 名不符："))
        for c in bad_a:
            lines.append(f"- ❌ **{c.name}**：{c.detail}")
        lines.append("")
    if settle is not None:
        bad_s = [c for c in settle if not c.ok]
        lines.append(f"**L4-b 真名结算**：{len(settle)} 名干员，"
                     + ("✅ 全部在自己的场景里按技能结算。" if not bad_s
                        else f"❌ {len(bad_s)} 名不符："))
        for c in bad_s:
            lines.append(f"- ❌ **{c.name}**：{c.detail}")
        lines.append("")
    if combos is not None:
        cstat = Counter(c.status for c in combos)
        lines.append(f"**L4-c 组合技能双向对照**：{len(combos)} 条"
                     f"（✅ {cstat.get('ok', 0)} / ⚪ {cstat.get('var', 0)} / ❌ {cstat.get('bad', 0)}）。"
                     f"对照手法：把搭档**改名或摘阵营但留在原房间**（保持在场人数不变——"
                     f"控制中枢有「中枢 N 人，每人 −0.05」的全局减免，搬走人会污染差值）；"
                     f"「独自一人」「宿舍他人在场」这两类条件本身就是人数，才真去增删人。")
        lines.append("")
        lines.append("| clause | 技能 | 组合类型 | A 净速率 | B 净速率 | 差 | 状态 | 说明 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for c in sorted(combos, key=lambda x: (x.combo, x.key)):
            mark = {"ok": "✅", "var": "⚪"}.get(c.status, "❌")
            lines.append(f"| `{c.key}` | {c.name} | {c.combo} | {c.rate_with} | {c.rate_without} "
                         f"| {c.delta if c.delta is not None else '—'} "
                         f"| {mark} {c.status} | {c.detail} |")
        lines.append("")
    lines.append("## 六、已知差异与「待拍板」清单")
    lines.append("")
    if L3_KNOWN_DIFFS:
        for key, why in L3_KNOWN_DIFFS.items():
            lines.append(f"- `{key}`：{why}")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 七、怎么复现")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/Scripts/python.exe scripts/verify_skills.py --check    # 只看结论")
    lines.append(".venv/Scripts/python.exe scripts/verify_skills.py --report   # 重写本报告")
    lines.append(".venv/Scripts/python.exe -m unittest tests.test_skill_coverage -v   # 四层断言")
    lines.append("```")
    lines.append("")
    if upstream_path:
        lines.append(f"本次 L3 用的上游：`{upstream_path}`")
        lines.append("")
    return "\n".join(lines)


# ============================================================================
# 入口
# ============================================================================
def run_check(upstream_path: Optional[Path], verbose: bool = True) -> dict:
    """跑四层核对，返回结构化结果。

    L1 模板自洽 / L2 clause 级 / L3 上游描述对照 / **L4 干员级生效性与组合技能双向对照**。
    """
    tpl_issues = check_templates()
    reg_issues, reg_stats = check_registry()
    clauses = check_clauses()
    status_by_key = {c.key: c.status for c in clauses}
    attach = check_operator_attachment()
    settle = check_operator_settlement(status_by_key)
    combos = check_combos(status_by_key)
    descs = None
    if upstream_path is not None:
        descs = check_descriptions(load_upstream(upstream_path))

    if verbose:
        print(f"[L1] 模板自洽：{'通过' if not tpl_issues else str(len(tpl_issues)) + ' 处问题'}")
        for i in tpl_issues:
            print(f"     - {i}")
        print(f"[L1] 台账 755 条双向核对："
              f"{'通过' if not reg_issues else str(len(reg_issues)) + ' 处不一致'}"
              f"（已建模 {reg_stats['modeled']} / 不建模 {reg_stats['unmodeled']}）")
        for i in reg_issues:
            print(f"     - {i}")
        print(f"[L2] clause {len(clauses)} 条："
              + "，".join(f"{k}={v}" for k, v in sorted(Counter(c.status for c in clauses).items())))
        for c in clauses:
            if not c.ok:
                print(f"     ❌ {c.key}（{c.skill.name}）：{c.detail}")
        bad_attach = [c for c in attach if not c.ok]
        bad_settle = [c for c in settle if not c.ok]
        print(f"[L4] 真名挂载 {len(attach)} 名干员："
              f"{'全部通过' if not bad_attach else str(len(bad_attach)) + ' 名不符'}")
        for c in bad_attach:
            print(f"     ❌ {c.name}：{c.detail}")
        print(f"[L4] 真名结算 {len(settle)} 名干员："
              f"{'全部通过' if not bad_settle else str(len(bad_settle)) + ' 名不符'}")
        for c in bad_settle:
            print(f"     ❌ {c.name}：{c.detail}")
        cs = Counter(c.status for c in combos)
        print(f"[L4] 组合技能双向对照 {len(combos)} 条："
              + "，".join(f"{k}={v}" for k, v in sorted(cs.items())))
        for c in combos:
            if not c.ok:
                print(f"     ❌ {c.key}（{c.name} / {c.combo}）：{c.detail}")
        if descs is None:
            print("[L3] 未运行（没找到上游仓库）")
        else:
            ds = Counter(d.status for d in descs)
            print(f"[L3] 描述对照 {len(descs)} 条：" + "，".join(f"{k}={v}" for k, v in sorted(ds.items())))
            for d in descs:
                if not d.ok:
                    print(f"     ❌ {d.key}（{d.skill.name}）：{d.detail}")
    return {"tpl_issues": tpl_issues, "reg_issues": reg_issues, "reg_stats": reg_stats,
            "clauses": clauses, "descs": descs,
            "attach": attach, "settle": settle, "combos": combos}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="技能全量核对（模板级 / clause 级 / 描述对照 / 干员级与组合技能）")
    ap.add_argument("--agd", help="上游仓库路径（含 zh_CN/gamedata/excel/building_data.json）")
    ap.add_argument("--report", action="store_true", help=f"写报告到 {REPORT_MD}")
    ap.add_argument("--check", action="store_true", help="只核对（有硬伤时退出码 1）")
    args = ap.parse_args(argv)

    upstream = find_upstream(args.agd)
    res = run_check(upstream)
    if args.report:
        REPORT_MD.write_text(
            build_report(res["clauses"], res["descs"], res["tpl_issues"], upstream,
                         res["attach"], res["settle"], res["combos"]),
            encoding="utf-8")
        print(f"\n报告已写入 {REPORT_MD}")
    bad = res["tpl_issues"] or res["reg_issues"] or \
        [c for c in res["clauses"] if not c.ok] or \
        [c for c in res["attach"] if not c.ok] or \
        [c for c in res["settle"] if not c.ok] or \
        [c for c in res["combos"] if not c.ok] or \
        [d for d in (res["descs"] or []) if not d.ok]
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
