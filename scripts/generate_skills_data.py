"""scripts/generate_skills_data.py —— 从 resources 的两份 CSV 生成 mood_soc/skills_data.py。

数据来源（均为逗号分隔文本，UTF-8，首行为表头）：

1. resources/moods_skills.txt —— 心情类技能库
   列：skill_id, clause, name, kind, family, target, condition, value_milli, status, control_capability
   - kind：drain（消耗） / recover（回复）
   - family：self / room / room_others / control_room / work_area / immune / swap /
             dorm_group / dorm_self / dorm_single / dorm_pool / complex / no_external
   - value_milli：千分值（250 = 0.25；drain 负=减耗、正=加耗；recover 恒正）
   - clause：同一 skill_id 可有多分句（每个分句是一个独立效果，单独成一条 Skill）

2. resources/operators.txt —— 干员↔技能映射
   列：operator_id, operator_name, skill_index, skill_name, skill_id, function_key,
       enhanced, unlock, elite, level
   - unlock：初始解锁 / 精英 1|2 解锁 / 等级 30 解锁 / 精英 1|2 提升 / 等级 30 提升
   - elite：0/1/2；level：1/30（三星机械满级解锁用 level=30）
   - enhanced：0=独立技能（解锁），1=精英化"提升"（β 替换 α）

生成的 mood_soc/skills_data.py 内容：
   - SKILLS：skill_id#clause -> Skill(...)（共享的技能效果定义，不含解锁信息）
   - DEFAULT_OPERATORS：干员名 -> [skill_id#clause, ...]（仅心情类，含全部 clause）
   - SKILL_EQUIPS：{(干员名, skill_id#clause) -> SkillEquip(unlock_elite, unlock_level, enhanced, replaces)}
   - TRAITS：干员名 -> 阵营（岁等，供定向技能匹配）

本脚本只依赖标准库 csv / pathlib，无第三方依赖。
运行：.venv/Scripts/python.exe scripts/generate_skills_data.py
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "resources"
OUT = ROOT / "mood_soc" / "skills_data.py"


def _load_templates_module():
    """按文件直接加载 skill_templates.py，绕开 mood_soc/__init__.py。

    为什么不能直接 `import mood_soc.skill_templates`：包 __init__ 会连锁
    import rules → skills → skills_data，而 skills_data 正是本脚本的**产物**，
    于是「生成脚本无法重新生成自己」（引导死锁）。按文件加载即无此依赖。
    """
    import importlib.util
    import sys
    path = ROOT / "mood_soc" / "skill_templates.py"
    spec = importlib.util.spec_from_file_location("_mood_soc_skill_templates", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod      # dataclass 需要模块已在 sys.modules 中
    spec.loader.exec_module(mod)
    return mod


_TEMPLATES = _load_templates_module()
SPREAD_SKILL_IDS = _TEMPLATES.SPREAD_SKILL_IDS

SKILLS_TXT = RES / "moods_skills.txt"
OPERATORS_TXT = RES / "operators.txt"


# ----------------------------------------------------------------------------
# 一、映射表
# ----------------------------------------------------------------------------
# (kind, family) -> SkillKind 名称
FAMILY_KIND = {
    ("drain", "self"):           "SELF_CONSUME",
    ("drain", "room"):           "FACILITY_CONSUME",
    ("drain", "room_others"):    "ROOM_OTHERS_CONSUME",
    ("recover", "control_room"): "CC_RECOVER",
    ("recover", "work_area"):    "CC_RECOVER",
    ("recover", "dorm_group"):   "DORM_GROUP",
    ("recover", "dorm_self"):    "DORM_SELF",
    ("recover", "dorm_single"):  "DORM_SINGLE",
    ("recover", "dorm_pool"):    "DORM_GROUP",      # pool=True（冰酿分配）
    ("recover", "complex"):      "DORM_TARGETED",
    ("recover", "immune"):       "ELIMINATE_SELF",
    ("recover", "no_external"):  "DORM_SELF",       # exclusive=True（菲亚梅塔自律）
    # 无法精确建模的机制，映射到相近 kind，后续标"待译"不生效：
    ("recover", "swap"):         "DORM_TARGETED",
    ("recover", "self"):         "DORM_SELF",
}

# skill_id 前缀 -> 干员所在设施类型（owner 必须在对应设施内技能才生效）
FACILITY_BY_PREFIX = {
    "control": "CONTROL_CENTER",
    "dorm":    "DORMITORY",
    "hire":    "OFFICE",
    "manu":    "MANUFACTURING",
    "meet":    "RECEPTION",
    "power":   "POWER",
    "trade":   "TRADING",
}

# work_area：中枢技能作用的"其他设施"（room2 = 全部工作设施）
WORK_FACILITIES = ("POWER", "MANUFACTURING", "TRADING", "OFFICE", "RECEPTION")
# room1 = "部分设施"（对应玛恩纳"公事公办"，作用于发电/办公/会客）
PARTIAL_WORK_FACILITIES = ("POWER", "OFFICE", "RECEPTION")

# 按 (skill_id, clause) 精确挂条件函数——用于**原文被本地 CSV 截断、但上游文本完整**的分句。
# 依据 AGENTS.md §11：逐条取自上游 buffs[].description（见各条注释里的原文）。
CLAUSE_COND = {
    # 会客室 6 条：「如果会客室内只有自身处于工作状态时，…心情每小时消耗 +N」
    ("meet_spd&cost_condChar_000", 1): "_cond_alone_in_facility",   # 双面间谍
    ("meet_spd&cost_condChar_001", 1): "_cond_alone_in_facility",   # “职业操守”·α
    ("meet_spd&cost_condChar_002", 1): "_cond_alone_in_facility",   # 我自己的愿望
    ("meet_spd&cost_condChar_011", 1): "_cond_alone_in_facility",   # “职业操守”·β
    ("meet_spd&cost_condChar_020", 1): "_cond_alone_in_facility",   # 专业经理·α
    ("meet_spd&cost_condChar_021", 1): "_cond_alone_in_facility",   # 专业经理·β
    # 「当与丰川祥子一起进驻控制中枢时，消除自身心情消耗的影响」
    ("control_mp_cost_reset_000", 1): "_cond_with_cc_xiangzi",      # 若叶睦 互为半身
    # 潮汐守望的「反之」与「宿舍内深海猎人满心情」分支
    ("control_mp_aegir1_000", 2): "_cond_no_abyssal_outside_dorm",
    ("control_mp_aegir1_000", 3): "_cond_dorm_abyssals_full_mood",
    # 资深料理人：对莱欧斯小队干员的额外 +0.15
    ("dorm_rec_all&tag_000", 2): '_cond_target_in_faction("莱欧斯小队")',
}

# 纯布尔、可自动映射的条件（在 target 或 condition 文本中命中）
COND_MARKERS = [
    ("该宿舍内心情18以下的干员", "_cond_mood_below_18", "DORM_TARGETED"),
    ("该宿舍内心情20以下的干员", "_cond_mood_below_20", "DORM_TARGETED"),
    ("当魔王进驻控制中枢", "_cond_with_cc_mogui", None),
    ("当与丰川祥子一起进驻控制中枢", "_cond_with_cc_xiangzi", None),
]

# 命中即视为"无法自动建模"的标记（per-count 倍率 / 资源计数器 / 截断条件 / 阵营占位符等）。
# 这类技能被标记 untranslated=True（保守不生效），note 保留原文待人工翻译。
# 注意：不含 "<$cc.c.room1/room2"（那是设施范围描述，由 facilities_of 处理）。
UNTRANSLATED_MARKERS = [
    "<$cc.g.",          # 阵营占位符（每个<阵营>干员）
    "每个", "每有", "每间", "每级", "每名",   # per-count 倍率
    "招募位", "热情值", "人间烟火", "无声共鸣",  # 资源 / 特殊计数器
    "当与", "如果", "反之", "多心情子句",       # 截断 / 分支条件
    "深海猎人", "萨尔贡干员", "满心情",          # 阵营 / 状态条件
    "额外+0.4", "额外+0.45",                    # 定向额外加成
]

# 机制特殊、无法用现有引擎精确建模的 (skill_id, family)，强制标待译
UNTRANSLATED_PREFIX_FAMILY = {
    ("control_mp_cost_reset_000", "immune"),  # 若叶睦 互为半身（消除自身，机制特殊）
}

# 阵营 / 标签表（由 scripts/generate_factions.py 从上游 termDescriptionDict 生成，
# 人工补充见 resources/factions_supplement.txt）。**不再手工维护阵营表。**
FACTIONS_TXT = RES / "factions.txt"
VARIABLE_PRODUCERS_TXT = RES / "variable_producers.txt"

# per-count 阵营计数类技能：skill_id -> 阵营名。
# 语义："进驻控制中枢时，中枢内每个 XX 阵营干员 → 回复 value"，回复量 = value × 中枢内该阵营干员数。
FACTION_COUNT_SKILLS = {
    "control_mp_cost&faction_000": "龙门近卫局",      # 陈 德才兼备
    "control_mp_cost&faction_020": "乌萨斯学生自治团",   # 早露 学生会会长
    "control_mp_cost&faction_030": "谢拉格",           # 灵知 幕后指挥
    "control_mp_cost&faction2_000": "鲤氏侦探事务所",   # 吽 坚毅随和
    "control_mp_cost&faction_900": "异格者",           # 异格者
    "control_mp_cost&faction_990": "彩虹小队",         # 彩虹小队
    "control_dorm_rec_tag_001": "精英干员",            # 电弧 无言的慈爱（作用于宿舍）
}

# 共事类技能：skill_id -> 条件函数调用表达式（精确映射，文本已截断故不能用文本匹配）。
COOP_COND = {
    "control_allCost_condChar_000": '_cond_with_cc_operator("阿")',          # 老鲤 浮生得闲
    "control_mp_cost_double_000": '_cond_with_cc_operator("阿米娅")',        # 魔王 魔王传承
    "control_mp_cost_double_001": '_cond_with_cc_operator("阿米娅")',        # 魔王 未完的故事
    "trade_ord_spd&cost_P_000": '_cond_with_facility_operator("拉普兰德")',  # 德克萨斯 恩怨
    "trade_ord_limit&cost_P_010": '_cond_with_facility_operator("能天使")',  # 德克萨斯 默契
    "trade_ord_limit&cost_P_020": '_cond_with_facility_operator("伺夜")',    # 贝洛内 未偿还的债务
    "trade_ord_limit&cost_P_000": '_cond_with_facility_operator("德克萨斯")',  # 拉普兰德 醉翁之意·α
    "trade_ord_limit&cost_P_001": '_cond_with_facility_operator("德克萨斯")',  # 拉普兰德 醉翁之意·β
    "control_mp&meet_spd_000": '_cond_with_cc_operator("丰川祥子")',         # 祐天寺若麦 成效优先
    "control_meeting&mp_cost_000": '_cond_with_cc_faction("萨尔贡")',        # 摆渡人 英雄的骄傲·α
    "control_meeting&mp_cost_100": '_cond_with_cc_faction("萨尔贡")',        # 摆渡人 英雄的骄傲·β
}

# 阵营表由上游生成，见 load_factions()；TRAITS 由阵营反推（保留 Operator.trait 这一 API）。


def load_variable_producers():
    """读 resources/variable_producers.txt → 产出者元组表。

    返回 [(skill_id, clause, variable, value, basis, condition, skill_name, holders)]，
    其中 holders = ((干员名, unlock_elite, unlock_level), ...)。

    ⚠️ 持有者必须从 `operators.txt`（覆盖**全部 429 名干员 × 755 条 buff**）取，
    不能从 `DEFAULT_OPERATORS`（只含**心情类**技能）取——
    产出者里有不少是纯变量技能（如祐天寺若麦「勤学苦练」、塑心「无声共鸣」），
    它们不在心情技能库里，用 DEFAULT_OPERATORS 会一个持有者都找不到（实测踩过）。
    """
    # 该资源文件带 `#` 注释头，故先滤掉注释与空行，再交给 DictReader
    with open(VARIABLE_PRODUCERS_TXT, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    # 持有者索引：skill_id -> [(干员名, unlock_elite, unlock_level, 技能显示名)]
    holders = defaultdict(list)
    skill_name_of = {}
    with open(OPERATORS_TXT, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            sid = (r.get("skill_id") or "").strip()
            if not sid:
                continue
            skill_name_of.setdefault(sid, (r.get("skill_name") or "").strip())
            holders[sid].append((
                (r.get("operator_name") or "").strip(),
                int(r.get("elite") or 0),
                int(r.get("level") or 1),
            ))

    out = []
    for r in csv.DictReader(lines):
        sid = (r.get("skill_id") or "").strip()
        if not sid or not r.get("variable"):
            continue
        out.append((
            sid,
            int(r["clause"]),
            r["variable"].strip(),
            parse_value_milli(r["value_milli"]),
            (r.get("basis") or "flat").strip(),
            (r.get("condition") or "").strip(),
            skill_name_of.get(sid, sid),
            tuple(holders.get(sid, ())),
        ))
    return out


def load_factions():
    """读 resources/factions.txt → {"by_operator": {...}, "by_faction": {...}}。"""
    by_operator = defaultdict(list)
    by_faction = defaultdict(list)
    with open(FACTIONS_TXT, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            fac, member = (r.get("faction") or "").strip(), (r.get("member") or "").strip()
            if not fac or not member:
                continue
            if fac not in by_operator[member]:
                by_operator[member].append(fac)
            if member not in by_faction[fac]:
                by_faction[fac].append(member)
    return {"by_operator": dict(by_operator), "by_faction": dict(by_faction)}


def derive_traits(factions):
    """由阵营表反推 TRAITS（Operator.trait）：目前只有「岁」被用作定向筛选。"""
    traits = {}
    for name, facs in factions["by_operator"].items():
        for f in facs:
            if f == "岁":
                traits[name] = "岁"
    return traits


def facility_of(skill_id: str) -> str:
    """由 skill_id 前缀得到 owner 所在设施枚举名。"""
    prefix = skill_id.split("_", 1)[0]
    return FACILITY_BY_PREFIX.get(prefix, "")


def parse_value_milli(raw: str) -> Decimal:
    """千分值 -> Decimal 实际值（250 -> 0.25；空 -> 0）。"""
    raw = (raw or "").strip()
    if not raw:
        return Decimal("0")
    return Decimal(raw) / Decimal("1000")


def facilities_of(kind_name: str, family: str, skill_id: str, target: str):
    """返回技能生效的设施范围（FacilityType 枚举名 tuple）。

    - 自身/设施/消除类：owner 所在设施（由前缀决定）。
    - CC_RECOVER：回复作用的目标设施——中枢内 / 宿舍 / 工作设施（room1=部分，room2=全部）。
    - DORM_*：空（宿舍回复函数内部已限定在宿舍，无需再标）。
    """
    if kind_name in ("SELF_CONSUME", "FACILITY_CONSUME", "ROOM_OTHERS_CONSUME",
                     "ELIMINATE_SELF"):
        owner_fac = facility_of(skill_id)
        return (owner_fac,) if owner_fac else ()
    if kind_name == "CC_RECOVER":
        if family == "work_area":
            if "room1" in target:
                return PARTIAL_WORK_FACILITIES
            return WORK_FACILITIES
        if skill_id.startswith("control_dorm_rec"):
            return ("DORMITORY",)
        return ("CONTROL_CENTER",)
    return ()


def classify_condition(target: str, condition: str):
    """由 target+condition 文本判定条件。

    返回 (cond_func_name_or_None, untranslated_bool, kind_override_or_None)。
    """
    text = f"{target} {condition}"
    # 1) 先查可自动映射的条件
    for marker, func, kind_override in COND_MARKERS:
        if marker in text:
            return func, False, kind_override
    # 2) 命中待译标记 -> 保守不生效
    for marker in UNTRANSLATED_MARKERS:
        if marker in text:
            return None, True, None
    return None, False, None


# ----------------------------------------------------------------------------
# 二、读取 moods_skills.txt -> 技能效果（按 skill_id#clause 拆分）
# ----------------------------------------------------------------------------
def parse_params(raw: str) -> dict:
    """解析 `k=v;k=v` 形式的模板参数列。"""
    out = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def load_skills():
    """返回 {skill_id#clause: skill 定义 dict}。"""
    skills = {}
    with open(SKILLS_TXT, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:
        if len(r) < 9:
            continue
        skill_id, clause, name, kind, family = r[0], r[1], r[2], r[3], r[4]
        target, condition, value_milli = r[5], r[6], r[7]
        control_capability = r[9] if len(r) > 9 else ""
        template_id = r[10] if len(r) > 10 else ""
        params = parse_params(r[11] if len(r) > 11 else "")

        key = f"{skill_id}#{clause}"
        kind_name = FAMILY_KIND.get((kind, family), "DORM_TARGETED")
        value = parse_value_milli(value_milli)

        # 条件判定优先级：
        # 1) 精确映射：阵营计数类（per-count）与共事类（condition 文本被截断/含 MAA 占位符）；
        # 2) immune（消除类）的 condition 是描述文本而非真条件；
        # 3) 通用文本匹配（classify_condition）。
        cond_func = None
        untranslated = False
        kind_override = None
        count_faction = None

        if (skill_id, int(clause)) in CLAUSE_COND:
            cond_func = CLAUSE_COND[(skill_id, int(clause))]
        elif skill_id in FACTION_COUNT_SKILLS:
            # 阵营计数类：value 是"每个该阵营干员"的回复量，由 rules 按阵营人数倍增
            count_faction = FACTION_COUNT_SKILLS[skill_id]
        elif skill_id in COOP_COND:
            cond_func = COOP_COND[skill_id]
        elif family == "immune":
            # 槐琥/令生效（target 的 <$cc 是阵营，由 annotate_traits 补 trait）
            untranslated = (skill_id, family) in UNTRANSLATED_PREFIX_FAMILY
        else:
            cond_func, untranslated, kind_override = classify_condition(target, condition)
            if (skill_id, family) in UNTRANSLATED_PREFIX_FAMILY:
                cond_func = None
                untranslated = True
                kind_override = None
        if kind_override:
            kind_name = kind_override

        # —— 模板化后的「partial」语义（只放宽，不放宽到收紧）——
        # legacy_untranslated = 老逻辑认为"条件无法自动翻译"。
        # 模板化后：效果骨架（kind/scope/target/stacking/value）一律保留在数据里；
        #   · 骨架可无条件成立（partial_mode=apply）→ 重新生效；
        #   · value 本身以缺失条件为前提（partial_mode=hold）→ 仍不生效。
        # 老逻辑本来就能翻译的 clause 不受影响（避免误伤 COND_MARKERS/COUNTS/COOP 命中项）。
        partial = params.get("partial") == "true"
        partial_mode = params.get("partial_mode", "")
        if template_id and untranslated:
            untranslated = partial_mode != "apply"

        facilities = facilities_of(kind_name, family, skill_id, target)

        skills[key] = {
            "skill_id": skill_id,
            "clause": int(clause),
            "name": name,
            "kind": kind_name,
            "value": value,
            "family": family,
            "facilities": facilities,
            "condition_func": cond_func,
            "untranslated": untranslated,
            "count_faction": count_faction,
            "exclusive": (kind, family) == ("recover", "no_external"),
            "pool": (kind, family) == ("recover", "dorm_pool"),
            "target_faction": None,
            "template_id": template_id,
            "max_group": params.get("max_group") or None,
            "var_name": params.get("var") or None,
            # 注意：var_per / var_min 是**变量点数**（「每有 20 点人间烟火」→ 20），
            # 不是千分值，故直接用 Decimal(str)，不能走 parse_value_milli。
            "var_per": Decimal(params["var_per"]) if params.get("var_per") else None,
            "var_min": Decimal(params["var_min"]) if params.get("var_min") else None,
            "basis": params.get("basis") or None,
            "self_only": params.get("self_only") == "true",
            "spread_whitelist": params.get("spread") == "cc.c.skill",
            "partial": partial,
            "partial_mode": partial_mode,
            "note": f"来源 skill_id={skill_id} clause={clause}；family={family}；"
                    f"template={template_id or '—'}；"
                    f"control_capability={control_capability or '无'}",
        }
        if target or condition:
            skills[key]["note"] += f"；target={target}；condition={condition}"
        if partial:
            skills[key]["note"] += f"（条件槽空：partial_mode={partial_mode}）"
    return skills


# ----------------------------------------------------------------------------
# 三、读取 operators.txt -> 干员↔技能绑定（仅心情类）
# ----------------------------------------------------------------------------
def parse_unlock(unlock: str, enhanced: str):
    """由 unlock 文本 + enhanced 标记得到 (unlock_elite, unlock_level, enhanced)。"""
    enhanced_bool = enhanced == "1"
    if unlock == "初始解锁":
        return 0, 1, enhanced_bool
    if "等级 30" in unlock:
        return 0, 30, enhanced_bool
    for elite in (1, 2):
        if f"精英 {elite}" in unlock:
            return elite, 1, enhanced_bool
    return 0, 1, enhanced_bool


def load_operators(skills_by_key):
    """返回 (default_operators, equips)。"""
    default_operators = defaultdict(list)
    equips = {}

    with open(OPERATORS_TXT, encoding="utf-8") as f:
        rows = list(csv.reader(f))

    by_operator = defaultdict(list)
    for r in rows[1:]:
        if len(r) < 10:
            continue
        _, op_name, _, _skill_name, skill_id, _, enhanced, unlock, _elite, _level = r
        if not any(k.startswith(skill_id + "#") for k in skills_by_key):
            continue
        e, lv, enh = parse_unlock(unlock, enhanced)
        by_operator[op_name].append((skill_id, e, lv, enh))

    for op_name, bindings in by_operator.items():
        # ⚠️ 替换链的单位是**技能**（skill_id），不是**分句**（skill_id#clause）。
        # 旧实现把每个 clause 当成链上的一环，导致「基础分句 + 每有 N 额外分句」结构的技能里，
        # 额外分句把自己的基础分句"替换"掉了（实测 6 例：响石 0.15、铎铃 万里传书 -0.1、
        # 刺玫 0.15、波卜 0.2、流明 0.1/0.15、隐德来希 0.1 全部丢失）。
        units = []          # (skill_id, [clause keys], family, elite, level, enhanced)
        for skill_id, e, lv, enh in bindings:
            keys = sorted(k for k in skills_by_key if k.startswith(skill_id + "#"))
            if not keys:
                continue
            # 同一技能的分句可能跨 family（如「挣脱」的 dorm_self / dorm_group）：
            # 以**首个分句**的 family 作为该技能的归类，保证链不被分句拆散。
            units.append((skill_id, keys, skills_by_key[keys[0]]["family"], e, lv, enh))

        by_family = defaultdict(list)
        for u in units:
            by_family[u[2]].append(u)

        # 组内按 (elite, level) 升序；enhanced 技能替换同组的前一个技能
        replaces_by_skill = {}
        for family, group in by_family.items():
            ordered = sorted(group, key=lambda x: (x[3], x[4]))
            prev_sid = None
            for skill_id, _keys, _fam, _e, _lv, enh in ordered:
                if enh and prev_sid is not None:
                    replaces_by_skill[skill_id] = prev_sid
                prev_sid = skill_id

        for skill_id, keys, _family, e, lv, enh in units:
            replaced = replaces_by_skill.get(skill_id)      # 记 **skill_id**（不带 #clause）
            for key in keys:
                equips[(op_name, key)] = {
                    "unlock_elite": e,
                    "unlock_level": lv,
                    "enhanced": enh,
                    "replaces": replaced,
                }
                default_operators[op_name].append(key)

    default_operators = {k: list(dict.fromkeys(v)) for k, v in default_operators.items()}
    return default_operators, equips


# ----------------------------------------------------------------------------
# 四、定向技能阵营：令「杯莫停」限定「岁」阵营
# ----------------------------------------------------------------------------
def annotate_factions(skills_by_key):
    """给需要按阵营筛选的技能补 target_faction（比对干员的阵营集合，见 skills._factions_of）。"""
    for key, sk in skills_by_key.items():
        if sk["kind"] == "ELIMINATE_SELF" and sk["skill_id"] == "control_facCostReset_000":
            sk["target_faction"] = "岁"


# ----------------------------------------------------------------------------
# 五、写出 skills_data.py
# ----------------------------------------------------------------------------
def _fmt_value(v: Decimal) -> str:
    return f'Decimal("{v}")'


def _fmt_tuple(items):
    if not items:
        return "()"
    return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"


def render(skills_by_key, default_operators, equips, traits, factions, var_producers):
    lines = []
    lines.append('"""mood_soc/skills_data.py —— 由 scripts/generate_skills_data.py 自动生成。')
    lines.append('')
    lines.append('请勿手工编辑；修改数据请改 resources/moods_skills.txt 与 resources/operators.txt，')
    lines.append('然后重新运行：.venv/Scripts/python.exe scripts/generate_skills_data.py')
    lines.append('"""')
    lines.append('from __future__ import annotations')
    lines.append('')
    lines.append('from decimal import Decimal')
    lines.append('')
    lines.append('from .config import FacilityType')
    lines.append('from .skills import (')
    lines.append('    Skill,')
    lines.append('    SkillEquip,')
    lines.append('    SkillKind,')
    lines.append('    _cond_mood_below_18,')
    lines.append('    _cond_mood_below_20,')
    lines.append('    _cond_with_cc_mogui,')
    lines.append('    _cond_with_cc_xiangzi,')
    lines.append('    _cond_with_cc_operator,')
    lines.append('    _cond_with_facility_operator,')
    lines.append('    _cond_with_cc_faction,')
    lines.append('    _cond_alone_in_facility,')
    lines.append('    _cond_no_abyssal_outside_dorm,')
    lines.append('    _cond_dorm_abyssals_full_mood,')
    lines.append('    _cond_target_in_faction,')
    lines.append(')')
    lines.append('')
    lines.append('')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('# 技能效果库（skill_id#clause -> Skill）。不含解锁信息；解锁见 SKILL_EQUIPS。')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('SKILLS = {')
    for key in sorted(skills_by_key):
        sk = skills_by_key[key]
        facility = _fmt_tuple([f"FacilityType.{f}" for f in sk["facilities"]])
        target_faction = f'"{sk["target_faction"]}"' if sk["target_faction"] else "None"
        cond = sk["condition_func"] or "None"
        count_faction = f'"{sk["count_faction"]}"' if sk["count_faction"] else "None"
        lines.append(f'    {key!r}: Skill(')
        lines.append(f'        {key!r},')
        lines.append(f'        {sk["name"]!r},')
        lines.append(f'        SkillKind.{sk["kind"]},')
        lines.append(f'        {_fmt_value(sk["value"])},')
        lines.append(f'        {facility},')
        lines.append(f'        {target_faction},')
        lines.append(f'        {cond},')
        lines.append(f'        note={sk["note"]!r},')
        lines.append(f'        exclusive={sk["exclusive"]!r},')
        lines.append(f'        pool={sk["pool"]!r},')
        lines.append(f'        untranslated={sk["untranslated"]!r},')
        lines.append(f'        count_faction={count_faction},')
        lines.append(f'        template_id={sk["template_id"]!r},')
        mg = f'"{sk["max_group"]}"' if sk["max_group"] else "None"
        lines.append(f'        max_group={mg},')
        lines.append(f'        spread_whitelist={sk["spread_whitelist"]!r},')
        lines.append(f'        partial={sk["partial"]!r},')
        lines.append(f'        partial_mode={sk["partial_mode"]!r},')
        vn = f'"{sk["var_name"]}"' if sk["var_name"] else "None"
        lines.append(f'        var_name={vn},')
        lines.append(f'        var_per={_fmt_value(sk["var_per"]) if sk["var_per"] is not None else "None"},')
        lines.append(f'        var_min={_fmt_value(sk["var_min"]) if sk["var_min"] is not None else "None"},')
        lines.append(f'        basis={sk["basis"]!r},')
        lines.append(f'        self_only={sk["self_only"]!r},')
        lines.append('    ),')
    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('# 内置干员：名字 -> 持有的心情技能 id 列表（skill_id#clause）。')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('DEFAULT_OPERATORS = {')
    for name in sorted(default_operators):
        ids = ", ".join(repr(k) for k in default_operators[name])
        lines.append(f'    {name!r}: [{ids}],')
    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('# 技能装备绑定：{(干员名, skill_id#clause) -> SkillEquip}。')
    lines.append('# 记录每个干员身上某技能的解锁等级 / 等级门槛 / 是否精英化"提升" / 替换目标。')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('SKILL_EQUIPS = {')
    for (name, key) in sorted(equips):
        eq = equips[(name, key)]
        repl = f'"{eq["replaces"]}"' if eq["replaces"] else "None"
        lines.append(f'    ({name!r}, {key!r}): SkillEquip(')
        lines.append(f'        {eq["unlock_elite"]}, {eq["unlock_level"]}, {eq["enhanced"]!r}, {repl}),')
    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('# 特性（定向技能匹配）：由阵营表反推，保留 Operator.trait 这一 API。')
    lines.append('TRAITS = {')
    for k in sorted(traits):
        lines.append(f'    {k!r}: {traits[k]!r},')
    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('# 干员 ↔ 阵营/标签（由 scripts/generate_factions.py 从上游 termDescriptionDict 生成）。')
    lines.append('# 上游权威键：cc.g.*（阵营）/ cc.tag.*（标签）；人工补充见 resources/factions_supplement.txt。')
    lines.append('# 用法：skills._factions_of(op) —— 支持"中枢内每有 1 名 XX 干员"这类 per-count 技能。')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('OPERATOR_FACTIONS = {')
    for name in sorted(factions["by_operator"]):
        fs = ", ".join(repr(f) for f in factions["by_operator"][name])
        lines.append(f'    {name!r}: ({fs},),')
    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('# 阵营/标签 → 成员（反向索引，便于排查"某技能为什么命中/没命中"）')
    lines.append('FACTION_MEMBERS = {')
    for fac in sorted(factions["by_faction"]):
        ms = ", ".join(repr(m) for m in factions["by_faction"][fac])
        lines.append(f'    {fac!r}: ({ms},),')
    lines.append('}')
    lines.append('')
    lines.append('')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('# 官方术语 cc.c.skill「部分技能」白名单 —— 玛恩纳「公事公办」的扩散对象（15 条）。')
    lines.append('# 来源：zh_CN/gamedata/excel/gamedata_const.json → termDescriptionDict.cc.c.skill')
    lines.append('# 语义：玛恩纳进驻控制中枢时，这 15 条「只回中枢内干员心情」的技能，')
    lines.append('#       其回复效果同时作用到 room2（其他设施 = 发电/制造/贸易/办公/会客）内工作状态的干员。')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('SPREAD_SKILL_IDS = (')
    for sid in SPREAD_SKILL_IDS:
        lines.append(f'    {sid!r},')
    lines.append(')')
    lines.append('')
    lines.append('# 官方术语 cc.c.sui2_1「特殊比较规则」：room2 回复在同一组内**取最高**（不求和）。')
    lines.append('ROOM2_MAX_GROUP = "room2_recover"')
    lines.append('')
    lines.append('')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('# 变量产出者：(skill_id, clause, 变量名, 值, 计数基准, 条件, 技能名, 持有者)')
    lines.append('# 来源 resources/variable_producers.txt（逐条注明上游 buff 描述出处）。')
    lines.append('# 用于 mood_soc/variables.collect_variables()：技能间的"中间货币"（人间烟火/热情值/无声共鸣）。')
    lines.append('# ---------------------------------------------------------------------------')
    lines.append('VARIABLE_PRODUCERS = (')
    for sid, clause, var, value, basis, cond, sname, holders in var_producers:
        hs = ", ".join(f"({n!r}, {e}, {lv})" for n, e, lv in holders)
        lines.append(f'    ({sid!r}, {clause}, {var!r}, {_fmt_value(value)}, {basis!r}, {cond!r},')
        lines.append(f'     {sname!r}, ({hs}{"," if len(holders) == 1 else ""})),')
    lines.append(')')
    lines.append('')
    return "\n".join(lines)


def check_faction_refs(skills_by_key, factions) -> list[str]:
    """校验：技能引用的阵营名必须存在于生成表里。

    这道守卫本该早就存在——旧手工表把「米诺斯」误标为「萨尔贡」、
    把早露用的名字写成「乌萨斯」（上游叫「乌萨斯学生自治团」），
    两者都因为"没人对了名字"而长期潜伏。现在一旦对不上就**直接报错**。
    """
    known = set(factions["by_faction"])
    used = {}
    for key, sk in skills_by_key.items():
        for fac in (sk.get("count_faction"), sk.get("target_faction")):
            if fac:
                used.setdefault(fac, []).append(key)
    for sid, expr in COOP_COND.items():
        m = re.search(r'_cond_with_cc_faction\("([^"]+)"\)', expr)
        if m:
            used.setdefault(m.group(1), []).append(sid)
    unknown = {f: v for f, v in used.items() if f not in known}
    if unknown:
        raise SystemExit(
            "❌ 技能引用了阵营表里不存在的阵营（改名或补 resources/factions_supplement.txt）：\n"
            + "\n".join(f"   {f!r} ← {', '.join(v[:4])}" for f, v in unknown.items()))
    return sorted(used)


def main():
    skills_by_key = load_skills()
    annotate_factions(skills_by_key)
    default_operators, equips = load_operators(skills_by_key)
    factions = load_factions()
    used_factions = check_faction_refs(skills_by_key, factions)
    var_producers = load_variable_producers()
    traits = derive_traits(factions)
    out_text = render(skills_by_key, default_operators, equips, traits, factions, var_producers)
    OUT.write_text(out_text, encoding="utf-8")
    print(f"[OK] 生成 {OUT}")
    print(f"     技能效果 {len(skills_by_key)} 条；干员 {len(default_operators)} 名；绑定 {len(equips)} 条")
    untranslated = sum(1 for sk in skills_by_key.values() if sk["untranslated"])
    print(f"     其中待译（不生效）{untranslated} 条")
    print(f"     变量产出者 {len(var_producers)} 条（人间烟火/热情值/无声共鸣）")
    print(f"     阵营表 {len(factions['by_faction'])} 组 / {len(factions['by_operator'])} 名干员；"
          f"技能引用到 {len(used_factions)} 组：{'、'.join(used_factions)}")


if __name__ == "__main__":
    main()
