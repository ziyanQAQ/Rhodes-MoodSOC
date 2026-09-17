"""store/sources.py —— 排班 / 蓝图 JSON 的**自动识别与一键导入**（输入解析层）。

点一次界面上的「导入排班…」，本模块负责自己判断拿到的是哪一种 JSON，并转成
本工具的「班次列表」；调用方不需要先问用户"这是哪种格式"。

## 支持的四类（按判定优先级）

| 格式 | 判定特征 | 里面有什么 | 转成 |
|---|---|---|---|
| `scenario` | 顶层有 `facilities` | 房间 + 人员（本工具自己的场景 JSON） | 一个班次（沿用现有路径） |
| `v3_out` | 顶层有 `result`（信封），或有 `rotation`/`maa`/`training_advice`/`profile` 之一 | **每班时长**（`rotation.shifts[].duration_hours`）、**谁在哪间房**（`assignment.rooms[].operators[].name/elite/level`）、**按设施类型分组的人员名单**（`maa.plans[].rooms`，含空宿舍 `skip:true`） | **多班排班**（见下"怎么拼"） |
| `plan_compute_v4` | `schema_version == 4` 且同时有 `layout` 与 `operbox` | 房间 `kind/level`、宿舍床位/氛围、**干员池**（`elite/level/own`）、`options.rotation`、`fiammetta_enable` | 空蓝图（房间建好、**没有人**）+ 干员池 + 班次时长 |
| `maa` | 顶层有 `plans`（数组） | 按类型分组的干员名单 + 班次名里的时长 | 多班排班（沿用 `maa.py`） |

## v3 输出怎么拼（**以 `maa` 段为主**）

上游 `resources/输出JSON结构说明.md`（ArknightsInfraCalc-v3 的输出契约）说得很清楚：
**输出里没有 `kind`、也没有 `level`**（类型只能回查输入的 `layout.rooms[].kind`）。
而同一份输出里的 `result.maa` 段**是按设施类型分组的**（`trading`/`manufacture`/`power`/
`dormitory`/`control`/`meeting`/`hire`/`processing`），并且连空宿舍都在（`skip:true`）。
所以：

1. **房间类型 / 房间顺序 / 人员** ← `maa.plans[i].rooms`（类型明确、含空房）；
2. **每班时长** ← `rotation.shifts[i].duration_hours`（比 MAA 名字里的 `12h` 更权威）；
3. **练度** ← `assignment.rooms[].operators[]`（`maa` 段只有名字）按**干员名**对齐；
4. **训练挂件** ← `assignment.training_assist` → 放进训练室（挂件位，0 消耗）；
5. **菲亚梅塔** ← `plans[i].Fiammetta.enable` → 本工具的「换心情」开关（M15a 患难之交）。

拿不到 `maa` 段时依次退化：`layout`（同文件里的输入回显，权威 kind/level）+ `assignment`
→ 只有 `assignment` 时按 `room_lines` 的字段组（`trade_*`/`manufacture_*`/`power_*`）与
`room_id` 前缀**推断**类型，等级按"实际放进几个人"反推最低可行等级，并把这些推断
**逐条写进导入报告**（`ImportReport.inferred`）——推断就是推断，不假装是契约。

⚠️ 本模块只做解析，不做任何心情计算（与 `layout.py` / `maa.py` 同类）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from mood_soc.battery import to_decimal
from mood_soc.config import DORM_LEVEL_TABLE, min_level_for_slots, parse_facility_type
from .maa import ROOM_KEY_BY_LABEL, ROOM_MAP, ROOM_ORDER, parse_duration_hint

# ---------------------------------------------------------------------------
# 映射表（只此一份，别在别处再抄一遍）
# ---------------------------------------------------------------------------
# plan_compute_v4 的 `rooms[].kind` → 本工具的设施中文名（9 种，与 FacilityType 一一对应）
KIND_MAP: Dict[str, str] = {
    "control_center": "控制中枢",
    "trade_post": "贸易站",
    "factory": "制造站",
    "power_plant": "发电站",
    "dormitory": "宿舍",
    "office": "办公室",
    "meeting_room": "会客室",
    "training_room": "训练室",
    "workshop": "加工站",
}

# v3 输出 `room_lines` 的字段前缀 → 设施中文名（文档 §4.5 给的"机器可判的替代判据"）
ROOM_LINE_KIND: Tuple[Tuple[str, str], ...] = (
    ("trade_", "贸易站"),
    ("manufacture_", "制造站"),
    ("power_", "发电站"),
)

# 只有 `assignment`（连 room_id 都要猜）时的前缀兜底：**推断**，会写进报告
ROOM_ID_PREFIX: Tuple[Tuple[str, str], ...] = (
    ("trade", "贸易站"), ("manu", "制造站"), ("factory", "制造站"), ("power", "发电站"),
    ("dorm", "宿舍"), ("control", "控制中枢"), ("meet", "会客室"),
    ("hire", "办公室"), ("office", "办公室"), ("train", "训练室"),
    ("work", "加工站"), ("process", "加工站"),
)

# v4 `initial_global` 的资源键 → 本项目的变量名（`variables.VARIABLES`）
# ⚠️ 上游 16 个键里 `virtual_power`/`virtual_gold_lines` 在本项目**没有对应变量**，
#    映射不到的一律进报告（不猜）。
GLOBAL_RESOURCE_MAP: Dict[str, str] = {
    "matatabi": "木天蓼",
    "perception": "感知信息",
    "human_fireworks": "人间烟火",
    "silent_echo": "无声共鸣",
    "monster_cuisine": "魔物料理",
    "dream": "梦境",
    "musical_section": "小节",
    "memory_fragment": "记忆碎片",
    "witchcraft_crystal": "巫术结晶",
    "thought_chain_ring": "思维链环",
    "intelligence_reserve": "情报储备",
    "usaut_drink": "乌萨斯特饮",
    "passion": "热情值",
    "engineering_robot": "工程机器人",
}

# 房间类型在班次内的排列顺序（稳定输出；与 `maa.ROOM_ORDER` 同一套习惯）
LABEL_ORDER: List[str] = [label for _key, (label, _lv) in
                          ((k, ROOM_MAP[k]) for k in ROOM_ORDER)]
LABEL_ORDER += [label for label in KIND_MAP.values() if label not in LABEL_ORDER]


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------
@dataclass
class ImportReport:
    """一次导入"读到了什么、用了什么、忽略了什么、哪些是推断的"。"""
    kind: str = ""
    source: str = ""
    notes: List[str] = field(default_factory=list)      # 正常读到并采用的东西
    ignored: List[str] = field(default_factory=list)    # 本模型不建模、已忽略的字段
    inferred: List[str] = field(default_factory=list)   # 推断项（类型/等级/时长）
    unmatched: List[str] = field(default_factory=list)  # **认得但本项目没有他的心情技能**
    unknown: List[str] = field(default_factory=list)    # 名字完全不认得

    def summary(self) -> str:
        """一行摘要（状态栏用）。"""
        names = {"scenario": "本工具场景", "maa": "MAA 排班",
                 "v3_out": "v3 求解输出", "plan_compute_v4": "v4 蓝图+干员池"}[self.kind] \
            if self.kind in ("scenario", "maa", "v3_out", "plan_compute_v4") else self.kind
        parts = [f"已识别：{names}"]
        parts += self.notes[:3]
        if self.inferred:
            parts.append(f"⚠ {len(self.inferred)} 项靠推断")
        if self.ignored:
            parts.append(f"忽略 {len(self.ignored)} 项未建模字段")
        if self.unknown:
            parts.append(f"⚠ {len(self.unknown)} 个名字不认得")
        if self.unmatched:
            parts.append(f"{len(self.unmatched)} 名没有心情技能（按无技能参与）")
        return "　｜　".join(parts)

    def details(self) -> str:
        """多行明细（日志 / 报告用）。"""
        lines = [f"格式：{self.kind}", f"来源：{self.source}"]
        for title, items in (("读到", self.notes), ("⚠ 推断", self.inferred),
                             ("⚠ 名字不认得", self.unknown),
                             ("没有心情技能（按无技能参与）", self.unmatched),
                             ("忽略（本模型不建模）", self.ignored)):
            if items:
                lines.append(f"{title}：")
                lines += [f"  - {it}" for it in items]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "source": self.source, "notes": list(self.notes),
                "ignored": list(self.ignored), "inferred": list(self.inferred),
                "unmatched": list(self.unmatched), "unknown": list(self.unknown),
                "summary": self.summary()}

    def scan_names(self, names: Sequence[str]) -> None:
        """把一批干员名分成"没有心情技能"与"名字不认得"两档（导入末尾统一调用）。"""
        known_mood = _known_operator_names()
        known_any = _all_operator_names()
        for n in names:
            if not n:
                continue
            if n in known_mood:
                continue
            if n in known_any:
                if n not in self.unmatched:
                    self.unmatched.append(n)
            elif n not in self.unknown:
                self.unknown.append(n)


@dataclass
class ImportedShift:
    """一个班次（facilities 已是本工具的场景结构，可直接喂 `build_base_layout`）。"""
    label: str
    hours: Optional[Decimal] = None
    facilities: List[dict] = field(default_factory=list)
    entry_events: Optional[dict] = None


@dataclass
class ImportResult:
    """一个文件的导入结果。"""
    kind: str = ""
    shifts: List[ImportedShift] = field(default_factory=list)
    pool: List[dict] = field(default_factory=list)          # 干员池 [{name, elite, level, own}]
    initial_global: Dict[str, Decimal] = field(default_factory=dict)   # 变量初始值
    entry_enabled: Optional[bool] = None                    # 菲亚梅塔开关（None = 文件没说）
    entry_events: Optional[dict] = None                     # 场景格式的 `entry_events`（上层直接用）
    scenario: Optional[dict] = None                         # 场景类文件原样透传
    report: ImportReport = field(default_factory=ImportReport)

    @property
    def pool_names(self) -> List[str]:
        return [p["name"] for p in self.pool]


# ---------------------------------------------------------------------------
# 菲亚梅塔（患难之交 M15a）：`Fiammetta.enable / target` → 本工具的「换心情」
# ---------------------------------------------------------------------------
def _entry_from_plans(plans: Sequence, report: ImportReport) -> Optional[dict]:
    """把各班的 `Fiammetta{enable,target}` 收成场景格式的 `entry_events`。

    - `enabled`：任一班开着就开（都不开 → `{"enabled": false}`，与"文件说不要换"等价）；
    - `swap_with`：各班 target 一致时用它（`"pre"` 那种只表示顺序，不参与）；
      各班 target 不同 → 不指定（回到默认「前一位进驻」）并逐班写覆盖，**忠实照搬**；
    - `per_shift`：只写与"基准班"（第 1 个开着的班）不同的那些（与界面同一约定）。
    """
    info: List[Tuple[int, bool, str]] = []
    for i, plan in enumerate(plans or []):
        fi = plan.get("Fiammetta") if isinstance(plan, dict) else None
        if isinstance(fi, dict):
            info.append((i, bool(fi.get("enable")), str(fi.get("target") or "")))
    if not info:
        return None
    if not any(e for _i, e, _t in info):
        report.notes.append("Fiammetta.enable=false → 换心情保持关闭")
        return {"enabled": False}
    base_i, _base_target = next((i, t) for i, e, t in info if e)
    targets = {t for _i, e, t in info if e and t}
    swap_with = next(iter(targets)) if len(targets) == 1 else None
    if len(targets) > 1:
        report.inferred.append(
            "各班 Fiammetta.target 不同（" + "、".join(sorted(targets))
            + "）→ 全局不指定「换谁」，改按**逐班**照搬")
    per_shift: List[dict] = []
    for i, e, t in info:
        if i == base_i and e:
            continue
        if e and (t or None) == swap_with:
            continue
        per_shift.append({"key": i + 1, "enabled": e, "swap_with": (t or "")})
    report.notes.append(
        "Fiammetta → 换心情（M15a 患难之交）：已开启"
        + (f"，换「{swap_with}」" if swap_with else "，换前一位进驻")
        + (f"，另有 {len(per_shift)} 班单独设置" if per_shift else ""))
    out: Dict[str, Any] = {"enabled": True}
    if swap_with:
        out["swap_with"] = swap_with
    if per_shift:
        out["per_shift"] = per_shift
    return out


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------
def _as_result(data: dict) -> Optional[dict]:
    """v3 输出：信封里的 `result`（失败时也可能只有部分字段）。"""
    res = data.get("result")
    return res if isinstance(res, dict) else None


def detect_format(data: Any) -> str:
    """判断这是哪一种 JSON（判不出来就抛 `ValueError`，并说清看到了什么）。

    返回值：`scenario` / `v3_out` / `plan_compute_v4` / `maa`。
    """
    if not isinstance(data, dict):
        raise ValueError("导入的文件顶层应当是一个 JSON 对象")
    if isinstance(data.get("facilities"), list):
        return "scenario"
    if _as_result(data) is not None:
        return "v3_out"
    if isinstance(data.get("layout"), dict) and isinstance(data.get("operbox"), list):
        return "plan_compute_v4"
    if any(k in data for k in ("rotation", "maa", "training_advice", "profile")):
        return "v3_out"
    if isinstance(data.get("plans"), list) and data["plans"]:
        return "maa"
    raise ValueError(
        "无法识别的 JSON：既没有 facilities（本工具场景），也没有 result/rotation/maa"
        "（v3 求解输出），也没有 layout+operbox（v4 蓝图+干员池），也没有 plans（MAA 排班）"
        f"；顶层键：{sorted(data)[:12]}")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _room_name(label: str, idx: int, total: int) -> str:
    """同名多房间的名字（与 MAA 导入一致：只有多间时才带序号）。"""
    return f"{label}#{idx}" if total > 1 else label


def _op_spec(name: str, training: Optional[Dict[str, Tuple]] = None) -> Any:
    """干员写法：有练度信息就写成对象（`elite`/`level`），否则就用纯名字（走默认满练）。

    `name` 先过一遍 `resolve_name`（英文名/干员 id → 中文名），练度仍按**原始写法**查表
    （上游工具用哪个名字，那边的练度表就是哪个名字）。
    """
    resolved = resolve_name(name)
    if training:
        e, lv = training.get(name) or training.get(resolved) or (None, None)
        if e is not None or lv is not None:
            spec: Dict[str, Any] = {"name": resolved}
            if e is not None:
                spec["elite"] = int(e)
            if lv is not None:
                spec["level"] = int(lv)
            return spec
    return resolved


def _hours_from_rotation_token(token: str) -> Optional[List[Decimal]]:
    """`abc_12_6_6` → `[12, 6, 6]`；`fiammetta_8_8_4_4` → `[8, 8, 4, 4]`。

    `rotation` 枚举的**尾部整数段就是各班时长**（模板名在前，如 `main_backup_12_12`）；
    取不到整数段就返回 None（上层按提示/均分兜底）。
    """
    if not token:
        return None
    parts = str(token).split("_")
    tail: List[Decimal] = []
    for p in reversed(parts):
        if p.isdigit():
            tail.append(to_decimal(p))
        else:
            break
    return list(reversed(tail)) or None


def _dorm_extras(room: dict) -> dict:
    """v4 宿舍的床位/氛围 → 本工具的 `slots` / `atmosphere`。"""
    out: Dict[str, Any] = {}
    beds = room.get("dorm_beds")
    if isinstance(beds, int) and beds > 0:
        out["slots"] = beds
    amb = room.get("dorm_ambience_level")
    if isinstance(amb, int) and amb in DORM_LEVEL_TABLE:
        out["atmosphere"] = DORM_LEVEL_TABLE[amb]["atmosphere_max"]
    return out


def _unknown_names(names: Sequence[str], known) -> List[str]:
    """不在技能库里的干员名（导入时提示；这些人按"无技能"参与计算）。"""
    out = []
    for n in names:
        if n and n not in known and n not in out:
            out.append(n)
    return out


def _known_operator_names():
    """本项目技能库里"有心情技能的人" —— 取 `DEFAULT_OPERATORS`（由 `data/operators.txt` 生成）。

    ⚠️ 不要用阵营表当"技能库名单"：阿米娅这类没有阵营的干员会被误判成"不认识"
    （实测：她在 `OPERATOR_FACTIONS` 里没有条目，但技能库里有）。
    """
    from data.skills_data import DEFAULT_OPERATORS
    return set(DEFAULT_OPERATORS)


_ALL_NAMES: Optional[set] = None


def _all_operator_names() -> set:
    """**认得的**干员名（`data/operators.txt` 第 2 列，约 429 名）——含没有心情技能的人。

    用来把"认得但没心情技能"（能天使那种：她的基建技能是产出类）与"根本不认得"
    （英文名没收录、或写了别的工具的内部 id）区分开——报告里这两件事的分量不同。
    """
    global _ALL_NAMES
    if _ALL_NAMES is None:
        import csv
        from data.paths import OPERATORS_TXT
        names = set()
        if OPERATORS_TXT.exists():
            with open(OPERATORS_TXT, encoding="utf-8", newline="") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2 and row[1].strip():
                        names.add(row[1].strip())
        _ALL_NAMES = names
    return _ALL_NAMES


# ---------------------------------------------------------------------------
# 干员名解析：别的工具可能写英文名或干员 id（`Amiya` / `char_002_amiya`）
# ---------------------------------------------------------------------------
def alias_table() -> Dict[str, str]:
    """别名 → 中文名（`data/operator_names.py`，由 `generate_operator_names.py` 生成）。

    生成物不存在时返回空表（此时只有中文名能被认出）——**不让导入因为缺一张派生物而失败**。
    """
    try:
        from data.operator_names import OPERATOR_ALIASES
    except ImportError:            # 派生物还没生成 → 退化为"只认中文名"
        return {}
    return dict(OPERATOR_ALIASES)


def resolve_name(name: str) -> str:
    """把一个干员名规范成**本项目技能库里的名字**（认不出就原样返回）。"""
    if not name:
        return name
    if name in _all_operator_names():
        return name
    return alias_table().get(name, name)


def _layout_levels(layout: Optional[dict]) -> Dict[str, List[int]]:
    """`layout.rooms` → `{设施中文名: [该类型各房间的等级（按出现顺序）]}`。

    ⚠️ 输出契约（`resources/输出JSON结构说明.md` §5）说：**MAA 组内数组顺序 = 布局中该类型
    房间的出现顺序**，所以第 i 间房能按下标对齐到 layout 里的第 i 间。
    """
    out: Dict[str, List[int]] = {}
    if not isinstance(layout, dict):
        return out
    for r in (layout.get("rooms") or []):
        label = KIND_MAP.get(str(r.get("kind")))
        if label:
            out.setdefault(label, []).append(int(r.get("level") or 1))
    return out


# ---------------------------------------------------------------------------
# 一、本工具场景
# ---------------------------------------------------------------------------
def _import_scenario(data: dict, report: ImportReport, source: str = "") -> ImportResult:
    report.kind = "scenario"
    report.source = source or str(data.get("_source_plan") or data.get("title") or "场景 JSON")
    report.notes.append(f"facilities {len(data['facilities'])} 间房")
    if data.get("entry_events"):
        report.notes.append("entry_events（换心情）")
    if data.get("idle_to_dorm"):
        report.notes.append("idle_to_dorm（闲置入宿）")
    if data.get("initial_global"):
        report.notes.append("initial_global（变量初始值）")
    names = [o if isinstance(o, str) else (o or {}).get("name")
             for f in data["facilities"] for o in f.get("operators", [])]
    report.scan_names([n for n in names if n])
    label = str(data.get("_source_plan") or Path(source).stem or "场景")
    entry = data.get("entry_events")
    return ImportResult(
        kind="scenario", scenario=data,
        shifts=[ImportedShift(label=label, hours=None,
                              facilities=list(data["facilities"]),
                              entry_events=entry if isinstance(entry, dict) else None)],
        report=report,
        entry_events=entry if isinstance(entry, dict) else None,
        entry_enabled=(bool(entry.get("enabled")) if isinstance(entry, dict) else None))


# ---------------------------------------------------------------------------
# 二、MAA 排班（顶层 plans）
# ---------------------------------------------------------------------------
def _facilities_from_maa_rooms(rooms: dict, training: Optional[Dict[str, Tuple]] = None,
                               report: Optional[ImportReport] = None,
                               keep_empty: bool = True,
                               levels: Optional[Dict[str, List[int]]] = None) -> List[dict]:
    """MAA 的 `rooms`（按类型分组）→ facilities。

    ⚠️ 与 `maa.convert_plan` 的区别：**空房间保留**（`skip:true` 的空宿舍也建出来）——
    空宿舍是"闲置入宿"能用的地方，丢了就等于少了恢复位；只是没有成员而已。

    `levels`：同文件里带 `layout` 时用它的等级（权威）；否则用 MAA 的默认等级 +
    按实际人数反推的最低等级。
    """
    out: List[dict] = []
    for key in ROOM_ORDER:
        group = rooms.get(key)
        if not isinstance(group, list) or not group:
            continue
        label, default_lv = ROOM_MAP[key]
        ftype = parse_facility_type(label)
        fixed = (levels or {}).get(label) or []
        for idx, room in enumerate(group, start=1):
            if not isinstance(room, dict):
                continue
            ops = [str(o) for o in (room.get("operators") or []) if o]
            if not ops and not keep_empty:
                continue
            if idx <= len(fixed):
                lv = int(fixed[idx - 1])               # layout 权威
            else:
                lv = max(int(default_lv), min_level_for_slots(ftype, len(ops)))
            if room.get("skip") and ops:
                # skip=true 表示"MAA 不要动这间房"；它仍可能有历史人员。这类矛盾写法要报出来。
                if report is not None:
                    report.inferred.append(
                        f"{label}#{idx}：MAA 标了 skip 却带着 {len(ops)} 名干员（按带上人员导入）")
            out.append({"type": label, "level": lv,
                        "name": _room_name(label, idx, len(group)),
                        "operators": [_op_spec(n, training) for n in ops]})
    return out


def _import_maa(data: dict, report: ImportReport, source: str = "") -> ImportResult:
    report.kind = "maa"
    report.source = source or str(data.get("title") or "MAA 排班")
    plans = data.get("plans") or []
    shifts: List[ImportedShift] = []
    for i, plan in enumerate(plans, start=1):
        if not isinstance(plan, dict):
            continue
        rooms = plan.get("rooms") or {}
        label = str(plan.get("name") or f"班次 {i}")
        facs = _facilities_from_maa_rooms(rooms, None, report)
        shifts.append(ImportedShift(label=label, hours=parse_duration_hint(label),
                                    facilities=facs))
    entry = _entry_from_plans(plans, report)
    report.notes.append(f"plans {len(shifts)} 班")
    if data.get("planTimes"):
        report.notes.append(f"planTimes={data['planTimes']}")
    if data.get("title"):
        report.notes.append(f"标题：{data['title']}")
    report.ignored.append("drones（无人机）/ product（产出）/ sort / autofill —— 本模型不建模")
    names = [n for s in shifts for f in s.facilities for n in f.get("operators", [])]
    report.scan_names(names)
    return ImportResult(kind="maa", shifts=shifts, report=report,
                        entry_events=entry,
                        entry_enabled=(entry or {}).get("enabled"))


# ---------------------------------------------------------------------------
# 三、v3 求解输出
# ---------------------------------------------------------------------------
def _training_by_shift(res: dict) -> List[Dict[str, Tuple]]:
    """每班的 `{干员名: (elite, level)}`（来自 `assignment.rooms[].operators[]`）。"""
    out: List[Dict[str, Tuple]] = []
    for s in ((res.get("rotation") or {}).get("shifts") or []):
        assign = s.get("assignment") or {}
        table: Dict[str, Tuple] = {}
        for room in (assign.get("rooms") or []):
            for op in (room.get("operators") or []):
                if isinstance(op, dict) and op.get("name"):
                    table[str(op["name"])] = (op.get("elite"), op.get("level"))
        ta = assign.get("training_assist")
        if isinstance(ta, dict) and ta.get("name"):
            table[str(ta["name"])] = (ta.get("elite"), ta.get("level"))
        out.append(table)
    return out


def _assignments_by_shift(res: dict) -> List[dict]:
    return [((s.get("assignment") or {}) if isinstance(s, dict) else {})
            for s in ((res.get("rotation") or {}).get("shifts") or [])]


def _kind_from_room_lines(efficiencies: dict) -> Dict[str, str]:
    """用 `efficiencies.room_lines[]` 的字段组判定房间类型（文档 §4.5 的机器判据）。"""
    out: Dict[str, str] = {}
    for line in ((efficiencies or {}).get("room_lines") or []):
        rid = line.get("room_id")
        if not rid:
            continue
        for prefix, label in ROOM_LINE_KIND:
            if any(k.startswith(prefix) for k in line):
                out[str(rid)] = label
                break
    return out


def _kind_from_room_id(rid: str) -> Optional[str]:
    low = rid.lower()
    for prefix, label in ROOM_ID_PREFIX:
        if low.startswith(prefix):
            return label
    return None


def _facilities_from_assignment(assignment: dict, layout: Optional[dict],
                                kind_hint: Dict[str, str], report: ImportReport) -> List[dict]:
    """从 `assignment`（+ 可选 `layout`）建 facilities。

    优先级：`layout.rooms[].kind/level`（权威）> `room_lines` 字段组 > `room_id` 前缀推断。
    `layout` 在时**连没有成员的房间也建出来**（蓝图才是完整的基地）。
    """
    rooms: Dict[str, dict] = {}          # room_id → 房间定义
    order: List[str] = []
    has_layout = isinstance(layout, dict)
    if has_layout:
        for r in (layout.get("rooms") or []):
            rid, kind = r.get("id"), r.get("kind")
            label = KIND_MAP.get(str(kind))
            if not rid or not label:
                continue
            fac: Dict[str, Any] = {"type": label, "level": int(r.get("level") or 1),
                                   "operators": []}
            if label == "宿舍":
                fac.update(_dorm_extras(r))
            rooms[str(rid)] = fac
            order.append(str(rid))
    for room in (assignment.get("rooms") or []):
        rid = str(room.get("room_id") or "")
        if not rid:
            continue
        if rid not in rooms:
            label = kind_hint.get(rid) or _kind_from_room_id(rid)
            source = ("room_lines 的字段组" if rid in kind_hint else "room_id 前缀")
            if label is None:
                label, source = "制造站", "没有线索（暂按制造站）"
            report.inferred.append(f"房间 {rid}：类型按{source}推断为「{label}」"
                                   f"（文件里没有 layout 可回查）")
            rooms[rid] = {"type": label, "level": 1, "operators": []}
            order.append(rid)
        for op in (room.get("operators") or []):
            if isinstance(op, dict) and op.get("name"):
                name = str(op["name"])
                rooms[rid]["operators"].append(
                    _op_spec(name, {name: (op.get("elite"), op.get("level"))}))
    # 等级：有 layout 就用 layout 的；没有就按"实际放进几个人"反推最低可行等级
    if not has_layout and order:
        report.inferred.append("房间等级：按各班实际人数反推最低可行等级")
    out: List[dict] = []
    total = {f["type"]: sum(1 for r in order if rooms[r]["type"] == f["type"])
             for f in rooms.values()}
    seen: Dict[str, int] = {}
    for rid in order:
        f = dict(rooms[rid])
        seen[f["type"]] = seen.get(f["type"], 0) + 1
        if not has_layout:
            ftype = parse_facility_type(f["type"])
            n = len(f.get("operators") or [])
            if ftype is not None:
                f["level"] = max(1, int(min_level_for_slots(ftype, n)))
        f["name"] = _room_name(f["type"], seen[f["type"]], total.get(f["type"], 1))
        out.append(f)
    return out


def _import_v3(data: dict, report: ImportReport, source: str = "") -> ImportResult:
    res = _as_result(data) or data
    report.kind = "v3_out"
    report.source = source or "v3 求解输出"
    rotation = res.get("rotation") or {}
    shifts_meta = rotation.get("shifts") or []
    maa = res.get("maa") or {}
    plans = maa.get("plans") if isinstance(maa, dict) else None

    if rotation.get("profile"):
        report.notes.append(f"轮换 {rotation['profile']}")
    daily = rotation.get("daily")
    if isinstance(daily, dict):
        report.ignored.append("rotation.daily（效率/日产出）—— 本工具算心情，不算产出")
    for key in ("training_advice", "profile"):
        if res.get(key):
            report.ignored.append(f"{key}（{'练卡建议' if key == 'training_advice' else '差距报告'}）"
                                  f"—— 与心情无关")
    if not shifts_meta:
        report.ignored.append("rotation.shifts 为空（该次求解没有排班）")
    if maa:
        report.notes.append("result.maa 段：按设施类型分组的人员名单（含空宿舍）")

    trains = _training_by_shift(res)
    assigns = _assignments_by_shift(res)
    layout = res.get("layout") if isinstance(res.get("layout"), dict) else data.get("layout")
    levels = _layout_levels(layout)
    if isinstance(layout, dict):
        report.notes.append("同文件里带 layout（房间类型/等级以它为准）")

    shifts: List[ImportedShift] = []
    n = max(len(shifts_meta), len(plans or []))
    if not n:
        raise ValueError("这份 v3 输出里既没有 rotation.shifts，也没有 maa.plans，导不出排班")
    for i in range(n):
        meta = shifts_meta[i] if i < len(shifts_meta) and isinstance(shifts_meta[i], dict) else {}
        train = trains[i] if i < len(trains) else {}
        assign = assigns[i] if i < len(assigns) else {}
        eff = (meta.get("efficiencies") or {})
        kind_hint = _kind_from_room_lines(eff)
        hours = meta.get("duration_hours")
        teams = meta.get("active_teams") or []
        label = (f"{i + 1}. " + "/".join(str(t) for t in teams)) if teams else f"班次 {i + 1}"
        if hours is not None:
            label += f" · {to_decimal(hours)}h"
        if plans and i < len(plans) and isinstance(plans[i], dict):
            rooms = plans[i].get("rooms") or {}
            facs = _facilities_from_maa_rooms(rooms, train, report, levels=levels)
            if not rooms:
                report.inferred.append(f"第 {i + 1} 班：MAA 段没有 rooms（按空班导入）")
            if meta.get("active_teams"):
                report.notes.append(f"第 {i + 1} 班在班队伍：{'/'.join(map(str, teams))}"
                                    f"（只是标签，不绑定干员）")
        else:
            facs = _facilities_from_assignment(assign, layout, kind_hint, report)
            if not layout:
                report.inferred.append("房间等级：按各班实际人数反推最低可行等级"
                                       "（文件里没有 layout 可回查）")
        # 训练挂件：MAA 段不含训练室，单独从 assignment 补
        ta = assign.get("training_assist") if isinstance(assign, dict) else None
        if isinstance(ta, dict) and ta.get("name"):
            name = str(ta["name"])
            facs = [f for f in facs] + [{"type": "训练室", "level": 3, "name": "训练室",
                                         "operators": [_op_spec(name, {name: (ta.get("elite"),
                                                                              ta.get("level"))})]}]
            report.notes.append(f"第 {i + 1} 班训练挂件：{name}（放进训练室＝挂件位，0 消耗）")
        shifts.append(ImportedShift(
            label=label, hours=to_decimal(hours) if hours is not None else None,
            facilities=facs))

    # 班次时长：rotation 权威；缺了就用 MAA 名字里的提示（兜底并记账）
    if all(s.hours is None for s in shifts) and plans:
        for i, s in enumerate(shifts):
            if i < len(plans) and isinstance(plans[i], dict):
                s.hours = parse_duration_hint(str(plans[i].get("name") or ""))
        report.inferred.append("班次时长：rotation 没给 duration_hours，按 MAA 班次名里的提示解析")
    elif any(s.hours is None for s in shifts):
        report.inferred.append("部分班次缺 duration_hours（上层会按提示/均分兜底）")

    enabled_summary = _entry_from_plans(plans or [], report)
    if enabled_summary is None:
        opt = res.get("options") if isinstance(res.get("options"), dict) else None
        if opt is None and isinstance(data.get("options"), dict):
            opt = data["options"]
        if isinstance(opt, dict) and "fiammetta_enable" in opt:
            enabled_summary = {"enabled": bool(opt["fiammetta_enable"])}
            report.notes.append(f"options.fiammetta_enable={opt['fiammetta_enable']} → 换心情开关")
    report.ignored.append("weighted_*（加权效率）/ efficiencies（分房效率）—— 本工具算心情")
    names = [o if isinstance(o, str) else (o or {}).get("name")
             for s in shifts for f in s.facilities for o in f.get("operators", [])]
    report.scan_names([n for n in names if n])
    return ImportResult(kind="v3_out", shifts=shifts, report=report,
                        entry_events=enabled_summary,
                        entry_enabled=(enabled_summary or {}).get("enabled"))


# ---------------------------------------------------------------------------
# 四、plan_compute_v4 输入（蓝图 + 干员池）
# ---------------------------------------------------------------------------
def _import_v4(data: dict, report: ImportReport, source: str = "") -> ImportResult:
    report.kind = "plan_compute_v4"
    report.source = source or str((data.get("labels") or {}).get("layout") or "v4 蓝图")
    layout = data.get("layout") or {}
    rooms = layout.get("rooms") or []
    facilities: List[dict] = []
    counts: Dict[str, int] = {}
    for r in rooms:
        label = KIND_MAP.get(str(r.get("kind")))
        if label is None:
            report.inferred.append(f"房间 {r.get('id')}：未知 kind={r.get('kind')!r}，已跳过")
            continue
        counts[label] = counts.get(label, 0) + 1
    seen: Dict[str, int] = {}
    for r in rooms:
        label = KIND_MAP.get(str(r.get("kind")))
        if label is None:
            continue
        seen[label] = seen.get(label, 0) + 1
        fac: Dict[str, Any] = {"type": label, "level": int(r.get("level") or 1),
                               "name": _room_name(label, seen[label], counts[label]),
                               "operators": []}
        if label == "宿舍":
            fac.update(_dorm_extras(r))
        if r.get("product"):
            report.ignored.append(f"房间 {r.get('id')} 的 product —— 产出不建模")
        facilities.append(fac)
    report.notes.append(f"rooms {len(facilities)} 间房（kind/level 来自蓝图）")
    report.notes.append("**房间是空的**：该格式只有蓝图与干员池，没有「谁在哪间房」")

    pool: List[dict] = []
    not_owned: List[str] = []
    for e in (data.get("operbox") or []):
        if not isinstance(e, dict) or not e.get("name"):
            continue
        entry = {"name": resolve_name(str(e["name"])), "elite": e.get("elite"),
                 "level": e.get("level"), "own": bool(e.get("own", True)),
                 "raw_name": str(e["name"])}
        if entry["own"]:
            pool.append(entry)
        else:
            not_owned.append(entry["name"])
    report.notes.append(f"operbox {len(pool)} 名可用干员（带练度）")
    if not_owned:
        report.notes.append(f"其中 {len(not_owned)} 名 own=false（未拥有）不进可选名单")
    report.ignored.append("potential / rarity —— 本模型不建模潜能与稀有度")

    options = data.get("options") or {}
    rotation = str(options.get("rotation") or "abc_12_6_6")
    hours = _hours_from_rotation_token(rotation)
    if hours:
        report.notes.append(f"rotation={rotation} → {len(hours)} 班 "
                            + "/".join(str(h) for h in hours) + "h")
    else:
        report.inferred.append(f"rotation={rotation!r} 解析不出班次时长，按 12/6/6 兜底")
        hours = [to_decimal(12), to_decimal(6), to_decimal(6)]
    for key in ("top", "system_preferences", "maa_title", "assert_invariants"):
        if key in options:
            report.ignored.append(f"options.{key} —— 求解器参数，与心情无关")
    if "template" in layout and layout.get("template"):
        report.notes.append(f"模板 {layout['template']}")
    if "drone_cap" in layout:
        report.ignored.append("drone_cap（无人机上限）—— 本模型不建模")

    scen = layout.get("scenario") or {}
    initial: Dict[str, Decimal] = {}
    for key, val in (scen.get("initial_global") or {}).items():
        name = GLOBAL_RESOURCE_MAP.get(str(key))
        if name is None:
            report.inferred.append(f"initial_global.{key} 在本项目没有对应变量，已忽略")
            continue
        initial[name] = to_decimal(val)
    if initial:
        report.notes.append("initial_global → 变量初始值：" + "、".join(
            f"{k}={v}" for k, v in initial.items()))
    for key in ("elite_facility_count", "sui_facility_count", "dorm_occupant_count",
                "base_workforce"):
        if key in scen:
            report.ignored.append(f"layout.scenario.{key} —— 本模型按布局自行推导")

    enabled = options.get("fiammetta_enable")
    entry = ({"enabled": bool(enabled)} if enabled is not None else None)
    if enabled is not None:
        report.notes.append(f"fiammetta_enable={bool(enabled)} → 换心情开关")
    report.scan_names([p["name"] for p in pool])

    # 班次：蓝图没有人员 → 空房间的班次（时长来自 rotation）
    label_base = str((data.get("labels") or {}).get("layout") or "蓝图")
    shifts = [ImportedShift(label=f"{i + 1}. {label_base} · {h}h", hours=h,
                            facilities=[dict(f, operators=list(f.get("operators") or []))
                                        for f in facilities])
              for i, h in enumerate(hours)]
    return ImportResult(kind="plan_compute_v4", shifts=shifts, pool=pool,
                        initial_global=initial, report=report,
                        entry_events=entry, entry_enabled=entry and entry.get("enabled"))


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def import_data(data: Any, source: str = "") -> ImportResult:
    """把一个已解析的 JSON（dict）转成 `ImportResult`。"""
    report = ImportReport(source=source)
    kind = detect_format(data)
    if kind == "scenario":
        return _import_scenario(data, report, source)
    if kind == "maa":
        return _import_maa(data, report, source)
    if kind == "v3_out":
        return _import_v3(data, report, source)
    return _import_v4(data, report, source)


def import_file(path: Union[str, Path]) -> ImportResult:
    """读取一个 JSON 文件并导入（自动识别格式）。"""
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    res = import_data(data, source=p.name)
    if not res.report.source:
        res.report.source = p.name
    return res


__all__ = [
    "DORM_LEVEL_TABLE",
    "Decimal",
    "GLOBAL_RESOURCE_MAP",
    "ImportReport",
    "ImportResult",
    "ImportedShift",
    "KIND_MAP",
    "LABEL_ORDER",
    "Path",
    "ROOM_ID_PREFIX",
    "ROOM_KEY_BY_LABEL",
    "ROOM_LINE_KIND",
    "ROOM_MAP",
    "ROOM_ORDER",
    "alias_table",
    "dataclass",
    "detect_format",
    "field",
    "import_data",
    "import_file",
    "min_level_for_slots",
    "parse_duration_hint",
    "parse_facility_type",
    "resolve_name",
    "to_decimal",
]
