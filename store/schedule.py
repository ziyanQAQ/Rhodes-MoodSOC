"""store/schedule.py —— 多班排班模型 + 「整周期心情轨迹」（**不依赖任何 GUI**）。

> 本模块原在 `ui/schedule.py`。它虽然只被图形界面使用，却**不依赖 tkinter**，
> 而是"这个排班能不能永动"的真计算引擎 —— 所以搬到 `store/`（数据与状态层），
> GUI 与程序接口（`api/`，供 Rust 调用）共用同一份。历史 import
> `from ui.schedule import ...` 由 `ui/schedule.py` 的转发保持可用。

## 这一层解决什么

`mood_soc` 回答的是"**一个固定布局**下某人还能撑多久"；而排班是**多班轮换**：
一个 24h 周期切成若干班（如 12h + 6h + 6h），每班有**自己的布局**，干员在班次切换时
换房间（工作 ↔ 宿舍）。心情跨班连续，于是"这个排班能不能永动"才是个真问题。

## 为什么是「事件驱动精确积分」而不是固定步长采样

在**一段区间内**（没有事件发生），所有干员的净速率都不变 ⇒ 心情是**时间的线性函数**，
可以一步解析地推到区间末尾。会让速率改变的事只有四类，全部可以**精确求出时刻**：

| 事件 | 为什么它改变速率 |
|---|---|
| **班次边界** | 布局换了，房间/同设施他人都变了 |
| **心情触到 0（红脸）** | 红脸后该干员所有心情类技能失效（§4.1） |
| **心情触到阈值** `{24, 20, 18, 12}` | 现有条件函数与变量产出者读的就是这些值：<18/<20（刺玫/净化呼吸）、==24（满心情才有 M15a 触发、未满才进池分配/单体候选）、12（「心情落差>12」与 `mood_below_12/above_12` 产出条件） |
| **同设施两人心情交叉** | 单体回复的受益者按"心情最低且未满"锁定、池分配按"未满成员"均分 ⇒ 排序变化会让受益者换人 |

再加一条**安全上限**（每段最多 `MAX_SEGMENT_HOURS`，默认 0.25h）兜底：万一将来加了
「读心情的新条件」而忘了登记到 `EVENT_THRESHOLDS`，误差也被限制在一个小段内（而不是无声放大）。

**好处**：曲线是折线（节点即事件时刻）⇒ 画出来就是**精确**的，滑块处按线性插值取值也是
**精确**的，且不需要按 0.05h 跑上万步（当前实现约 100 次全布局重算/周期）。

## 口径（写清楚免得被当 bug）

- 心情在周期起点取值 = `initial_moods`（默认 24）；跨班连续，**不在班次边界重置**。
- **未排班**（某班次里没这个干员）⇒ 视为"不在基建内"：净速率 0（不消耗也不回复），心情不变。
- 周期可重复：`cycles=2` 表示把同一排班连跑两周（心情接着上周期的末尾继续），用来判断是否收敛。
- 进驻事件（M15a 患难之交）默认**不结算**；`entry_events=True` 时在**每班开始时**结算一次
  （它会就地改心情，属"事件"而非速率，见 `mood_soc/rules.apply_entry_events`）。
"""

from __future__ import annotations

import bisect
import copy
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from mood_soc import (apply_entry_events, apply_idle_to_dorm, compute_net_rate,
                      entry_event_holders, reset_entry_events)
from mood_soc.battery import ZERO, to_decimal
from mood_soc.config import MOOD_MAX, MOOD_MIN, use_project_decimal_context
from mood_soc.models import (BaseLayout, EntryEventConfig, EntryShiftOverride,
                             IdleToDormConfig, IdleToDormEntry,
                             build_entry_event_config, normalize_entry_when,
                             resolve_entry_config)
from data.skills_data import DEFAULT_OPERATORS

from .layout import build_base_layout
from .maa import read_maa
from .sources import detect_format, import_data, import_file

# 周期默认 24h；班次时长之和必须等于周期时长
DEFAULT_CYCLE_HOURS = Decimal("24")

# 会让"净速率"发生变化的心情阈值（含红脸 0 与满心情 24）
EVENT_THRESHOLDS: Tuple[Decimal, ...] = (
    MOOD_MIN,                      # 0  ：红脸 → 技能失效
    Decimal("12"),                 # 12 ：心情落差 >12 / mood_below_12 产出条件
    Decimal("18"),                 # 18 ：刺玫「芬芳疗养」
    Decimal("20"),                 # 20 ：净化呼吸
    MOOD_MAX,                      # 24 ：满心情（M15a 触发、未满才进池分配/单体候选）
)

# 单段最长时长（兜底安全上限，见模块 docstring）
MAX_SEGMENT_HOURS = Decimal("0.25")

# 两个事件之间的最小间隔：比它更近的事件视为"同一个"（防止 dt 非终止小数导致的抖动/节点风暴）
MIN_EVENT_GAP = Decimal("0.000001")     # 1e-6 小时 ≈ 3.6 毫秒


# ============================================================================
# 班次与排班
# ============================================================================
@dataclass
class Shift:
    """一个班次：一段时长 + 一份布局（+ 可选的「不在基建」名单）。"""
    label: str
    hours: Decimal
    facilities: List[dict]
    source: str = ""
    # 进驻事件配置（models.EntryEventConfig）：来自场景 JSON 顶层，随班次一起搬运
    entry_events: Optional[object] = None
    # 变量初始值（场景 JSON 的 `initial_global`；导入 v4 蓝图的 `scenario.initial_global`）
    initial_global: Dict[str, Decimal] = field(default_factory=dict)
    # 「不在基建」名单：**既不在工作设施、也不在宿舍**的干员（场景 JSON 顶层 `detached`）。
    # 语义：整段轨迹里心情恒定（净速率 0），且**不参与任何技能计数**。
    detached: List[str] = field(default_factory=list)
    world: BaseLayout = field(init=False, repr=False)

    def __post_init__(self):
        self.hours = to_decimal(self.hours)
        if self.hours < ZERO:
            raise ValueError(f"班次「{self.label}」时长不能为负，收到 {self.hours}")
        # 注：hours == 0 表示"时长未知"（MAA 班次名里没带 h 时），
        # 由 `_hours_from_hints` 补齐、并由 `Schedule` 校验必须为正。
        self.detached = [str(n) for n in (self.detached or [])]
        self.world = build_base_layout({"facilities": self.facilities,
                                        "initial_global": dict(self.initial_global or {}),
                                        # 「不在基建」名单与 facilities 同层（场景 JSON 顶层）
                                        "detached": list(self.detached)})
        if self.entry_events is not None:      # 顶层配置透传给 world
            self.world.entry_events = self.entry_events
        self._names = [o.name for o in self.world.all_operators()]

    @property
    def operators(self) -> List[str]:
        """本班次进驻的干员（按房间顺序，去重保序）。"""
        return list(self._names)

    def facility_groups(self) -> List[Tuple[str, List[str]]]:
        """[(房间显示名, [干员名...]), ...]（只含进驻者，副手不计）。"""
        return [(f.display_name, [o.name for o in f.operators]) for f in self.world.facilities]


def shift_from_facilities(label: str, hours, facilities: List[dict], source: str = "",
                          entry_events=None, initial_global=None, detached=None) -> Shift:
    """由场景格式的 facilities 构建一个班次（`entry_events` 为可选进驻事件配置）。"""
    return Shift(label=label, hours=to_decimal(hours), facilities=list(facilities),
                 source=source, entry_events=entry_events,
                 initial_global=dict(initial_global or {}),
                 detached=list(detached or []))


def shifts_from_import(imp, source: str = "") -> List[Shift]:
    """把 `importer.ImportResult` 的一个文件转成班次列表。

    - 每班的 `entry_events`（换心情）：沿用该文件解析出来的配置（挂在每个班次上）；
    - `initial_global`（变量初始值）：挂到每个班次（同一份排班共用）；
    - `detached`（不在基建的人）：同样挂到每个班次（同一份排班共用）。
    """
    cfg = build_entry_event_config(imp.entry_events) if imp.entry_events else None
    return [Shift(label=s.label, hours=s.hours if s.hours is not None else ZERO,
                  facilities=list(s.facilities), source=source or imp.report.source,
                  entry_events=cfg, initial_global=dict(imp.initial_global or {}),
                  detached=list(s.detached or []))
            for s in imp.shifts]


#: 班次名**结尾**那个内嵌时长（`Shift 1 · 12h` / `12小时` / `6 h`）——与 `store/maa.py`
#: 解析时长提示用的是同一套写法；只在"改时长"时用来改名，见 `relabel_hours`。
_DURATION_AT_END = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>h|H|小时|時)(?=\s*$)")


def _plain_hours(value) -> str:
    """小时数的纯文本写法（`8` / `7.5` / `0.25`）—— `ui.theme.fmt_hours` 去掉 `h` 后缀同义。"""
    d = to_decimal(value).normalize()
    text = format(d, "f")
    return text if text else "0"


def relabel_hours(label: str, old_hours, new_hours) -> str:
    """班次名里的**内嵌时长**跟着"改时长"一起改（用户口径）。

    | 名字 | 旧 → 新 | 结果 |
    |---|---|---|
    | `Shift 1 · 12h` | 12h → 8h | `Shift 1 · 8h` |
    | `Shift 2 · 6h` | 12h → 8h | `Shift 2 · 6h`（写的不是旧时长 → **不动**） |
    | `第一个班` / `A` | 任意 | 原样（不含时长 → **不动**） |

    ⚠️ 只认**结尾**那个时长，且必须**等于旧时长**才替换：名字是导入时按当时的时长拼的
    （`store/sources.py` 会拼 `· 12h`），改了时长不改名，看板头部 / 导出 / 设置里会一直
    写着旧时长；但"用户自己起的名字"里的数字不能乱动。
    ⚠️ 单位与空格原样保留（`· 12h` → `· 8h`、`12小时` → `8小时`、`6 h` → `8 h` 的写法各自保持）。
    """
    m = _DURATION_AT_END.search(label or "")
    if m is None:
        return label
    try:
        written = to_decimal(m.group("num"))
    except (ArithmeticError, ValueError):
        return label
    if written != to_decimal(old_hours):
        return label
    return f"{label[:m.start('num')]}{_plain_hours(new_hours)}{label[m.end('num'):]}"


def _hours_from_hints(shifts: List[Shift], cycle_hours: Optional[Decimal]) -> Decimal:
    """给缺时长的班次补时长，返回本排班的周期时长。

    规则：优先用班次名里的提示（`Shift 1 · 12h`）；提示缺失的班次均分**剩余**时长；
    若一个提示都没有，则按班次数均分 `cycle_hours`（默认 24）。
    """
    known = [s.hours for s in shifts if s.hours > ZERO]
    missing = [s for s in shifts if s.hours <= ZERO]
    if not missing:
        return sum(known, ZERO)
    base = cycle_hours if cycle_hours is not None else DEFAULT_CYCLE_HOURS
    remaining = base - sum(known, ZERO)
    share = remaining / len(missing)
    if share <= ZERO:
        raise ValueError(f"班次时长之和（{sum(known, ZERO)}）已达到周期 {base}，"
                         f"没有余量分给 {len(missing)} 个未标时长的班次")
    for s in missing:
        s.hours = share
    return sum((s.hours for s in shifts), ZERO)


def shifts_from_maa_file(path: Union[str, Path], hours: Optional[Sequence] = None) -> List[Shift]:
    """读一个 MAA 排班文件 → 班次列表（一个文件里的每个 plan 都是一个班次）。"""
    plans = read_maa(path)
    name = Path(path).name
    out: List[Shift] = []
    for i, plan in enumerate(plans, start=1):
        h = to_decimal(hours[i - 1]) if hours and i <= len(hours) else (
            plan.hours_hint if plan.hours_hint is not None else ZERO)
        label = plan.name or f"班次 {i}"
        out.append(Shift(label=label, hours=h if h > ZERO else ZERO,
                         facilities=plan.facilities, source=name))
    return out


def shifts_from_scenario_file(path: Union[str, Path], hours: Optional[Sequence] = None) -> List[Shift]:
    """读一个本工具的场景 JSON（`{"facilities": [...]}`）→ 一个班次。"""
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "facilities" not in data:
        raise ValueError(f"{p.name} 既不是 MAA 排班（无 plans）也不是场景文件（无 facilities）")
    label = str(data.get("_source_plan") or p.stem)
    h = to_decimal(hours[0]) if hours else ZERO
    return [Shift(label=label, hours=h, facilities=data["facilities"], source=p.name,
                  entry_events=build_entry_event_config(data.get("entry_events")))]


def shift_file_kind(path: Union[str, Path]) -> str:
    """判断文件类型：`maa` / `scenario` / `v3_out` / `plan_compute_v4`（自动识别）。"""
    from .sources import detect_format
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return detect_format(data)


@dataclass
class Schedule:
    """一个周期内的若干班次（不变式：**各班长之和 == 周期时长**）。

    `start_clock`：**周期起点对应的钟点**（0 ~ 24，默认 0 = 00:00）。
    它**只是显示口径**——「从 1 点开始到第二天 1 点为一个周期」就是把 `start_clock` 设成 1，
    所有时刻标签（滑块 / 看板头部 / 曲线刻度 / 逐次表组头）按 `周期内时刻 + start_clock` 渲染；
    引擎的数值、判定、积分**一字不变**（模型本来就是相对时间）。

    `detached`：**「不在基建」名单**（场景 JSON 顶层 `detached`）——既不在工作设施、
    也不在宿舍的干员。语义见 `simulate_schedule`：整段轨迹**心情恒定**（净速率 0），
    且**不参与任何技能计数**（他们不在 `world` 里）。
    它属于**整份排班**（不是逐班），`__post_init__` 会把各班的并集归一化后写回每一班。
    """
    shifts: List[Shift]
    cycle_hours: Decimal = DEFAULT_CYCLE_HOURS
    start_clock: Decimal = ZERO
    detached: List[str] = field(default_factory=list)
    #: 内部用：`True` = `detached` 是**权威值**，别把各班 Shift 上的旧名单并回来。
    #: （`with_detached()` 用它实现"清空名单"；外部构造时不要传。）
    _detached_authoritative: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self):
        if not self.shifts:
            raise ValueError("排班至少要有一个班次")
        self.cycle_hours = to_decimal(self.cycle_hours)
        # 起点钟点取模到 [0, 24)：12:00 与 36:00 是同一件事，别让调用方自己去归一
        self.start_clock = to_decimal(self.start_clock) % Decimal("24")
        for s in self.shifts:
            if s.hours <= ZERO:
                raise ValueError(f"班次「{s.label}」的时长还没确定（为 0）")
        total = self.total_hours
        if total != self.cycle_hours:
            raise ValueError(f"各班长之和 {total}h ≠ 周期 {self.cycle_hours}h"
                             f"（改班次时长或改周期，两者必须相等）")
        # ——「不在基建」名单归一化：整份排班共用一份 ——
        # 权威值（`with_detached` 给的，含"清空"）直接用；否则把各班的旧名单并进来
        # （导入路径：名单可能挂在任何一个班次上）。
        merged: List[str] = [str(n) for n in (self.detached or [])]
        if not self._detached_authoritative:
            for s in self.shifts:
                for n in (s.detached or []):
                    if n not in merged:
                        merged.append(n)
        self.detached = merged
        for s in self.shifts:
            s.detached = list(merged)
            s.world.detached = list(merged)
        self._starts: List[Decimal] = []
        t = ZERO
        for s in self.shifts:
            self._starts.append(t)
            t += s.hours

    # ------------------------------------------------------------------ 查询
    @property
    def total_hours(self) -> Decimal:
        return sum((s.hours for s in self.shifts), ZERO)

    @property
    def starts(self) -> List[Decimal]:
        """各班次的起始时刻（相对周期起点）。"""
        return list(self._starts)

    def index_at(self, t) -> int:
        """时刻 t（可超出周期，按取模循环）落在第几个班次。"""
        t = to_decimal(t) % self.cycle_hours
        for i in range(len(self.shifts) - 1, -1, -1):
            if t >= self._starts[i]:
                return i
        return 0

    def shift_at(self, t) -> Shift:
        return self.shifts[self.index_at(t)]

    def offset_in_shift(self, t) -> Decimal:
        t = to_decimal(t) % self.cycle_hours
        return t - self._starts[self.index_at(t)]

    def operator_names(self) -> List[str]:
        """周期内出现过的全部干员（按班次顺序去重保序）**含「不在基建」的人**。

        为什么把不在基建的人也算进来：他们要能设心情、要能在曲线/全员一览里看到
        （一天一条平线），也要能被程序接口读到。数值上他们**不参与任何技能**
        ——见 `simulate_schedule` 的说明。
        """
        seen, out = set(), []
        for n in (self.detached or []):
            if n not in seen:
                seen.add(n)
                out.append(n)
        for s in self.shifts:
            for n in s.operators:
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        return out

    def stationed_names(self) -> List[str]:
        """**进驻在某个设施里**的干员（副手不算；跨班次去重保序）= `all_operators()` 的并集。"""
        seen, out = set(), []
        for s in self.shifts:
            for n in s.operators:
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        return out

    def bench_names(self) -> List[str]:
        """**「不在基建」的干员**：名单里明确点名的 + 整个排班都没排到位置的人（去重保序）。

        = `detached` ∪ (`operator_names() − stationed_names()`)
        """
        stationed = set(self.stationed_names())
        return [n for n in self.operator_names() if n in set(self.detached or ()) or n not in stationed]

    def hour_labels(self) -> List[str]:
        """各班次的"起始小时"标签（如 ['0:00', '12:00', '18:00']）。"""
        return [f"{int(t)}:{int((t - int(t)) * 60):02d}" for t in self._starts]

    # ------------------------------------------------------------------ 编辑
    def with_hours(self, hours: Sequence) -> "Schedule":
        """改班次时长（返回新的 Schedule；周期同步为新旧之和，故必然自洽）。

        ⚠️ **班次名里内嵌的旧时长会一起改**（用户口径）：`Shift 1 · 12h` 改成 8h 后
        名字变 `Shift 1 · 8h`（`relabel_hours`；不含时长或写的不是旧时长的名字一字不动）。
        同时把 `entry_events.per_shift` 里**按班次名写的键**重映射成新名 ——
        否则"按班次覆盖"会**静默失效**（`EntryShiftOverride.matches` 是拿键跟新班次名比的）。
        """
        if len(hours) != len(self.shifts):
            raise ValueError(f"需要 {len(self.shifts)} 个时长，收到 {len(hours)} 个")
        new_hours = [to_decimal(h) for h in hours]
        names = [relabel_hours(s.label, s.hours, h)
                 for s, h in zip(self.shifts, new_hours)]
        # 按班次名写的覆盖键要跟着改名（按序号写的键不受影响）
        has_cfg = any(s.entry_events is not None for s in self.shifts)
        cfg = None
        if has_cfg:
            cfg = copy.deepcopy(self.entry_config())
            old_names = [s.label for s in self.shifts]
            for ov in getattr(cfg, "per_shift", None) or ():
                key = getattr(ov, "key", None)
                if isinstance(key, str):
                    text = key.strip()
                    for old, new in zip(old_names, names):
                        if text == old and old != new:
                            ov.key = new
                            break
        new = [Shift(label=name, hours=h,
                     facilities=copy.deepcopy(s.facilities), source=s.source,
                     entry_events=cfg if has_cfg else None,
                     initial_global=dict(getattr(s, "initial_global", {}) or {}),
                     detached=list(getattr(s, "detached", []) or []))
               for s, h, name in zip(self.shifts, new_hours, names)]
        return Schedule(new, sum((s.hours for s in new), ZERO), self.start_clock,
                        list(self.detached))

    def with_start_clock(self, clock) -> "Schedule":
        """改「周期起点钟点」（纯显示口径，返回新的 Schedule）。"""
        return Schedule(copy.deepcopy(self.shifts), self.cycle_hours, to_decimal(clock),
                        list(self.detached))

    def replaced_shift(self, index: int, facilities: List[dict]) -> "Schedule":
        """替换某个班次的布局（返回新的 Schedule）。"""
        new = [Shift(label=s.label, hours=s.hours,
                     facilities=(list(facilities) if i == index else copy.deepcopy(s.facilities)),
                     source=s.source, entry_events=s.entry_events,
                     initial_global=dict(getattr(s, "initial_global", {}) or {}),
                     detached=list(getattr(s, "detached", []) or []))
               for i, s in enumerate(self.shifts)]
        return Schedule(new, self.cycle_hours, self.start_clock, list(self.detached))

    def with_detached(self, names: Sequence[str]) -> "Schedule":
        """改「不在基建」名单（返回新的 Schedule；各班的副本同步更新）。

        ⚠️ 传进去的名单是**权威值**（`[]` 就是清空）：`__post_init__` 默认会把各班
        Shift 上的旧名单并回来，所以这里要 `_detached_authoritative=True` ——
        否则"清空名单"会被无声地撤销（踩过）。
        """
        new = [Shift(label=s.label, hours=s.hours, facilities=copy.deepcopy(s.facilities),
                     source=s.source, entry_events=s.entry_events,
                     initial_global=dict(getattr(s, "initial_global", {}) or {}),
                     detached=[])
               for s in self.shifts]
        return Schedule(new, self.cycle_hours, self.start_clock,
                        [str(n) for n in (names or [])], _detached_authoritative=True)

    def entry_config(self):
        """本排班的进驻事件配置（取第一个班次的；MAA 排班没有则为默认值）。"""
        for s in self.shifts:
            cfg = getattr(s.world, "entry_events", None)
            if cfg is not None:
                return cfg
        return EntryEventConfig()

    def entry_config_for_shift(self, index: int, overrides=None) -> EntryEventConfig:
        """第 `index` 班（0 基）的**有效**进驻事件配置（已合并按班次覆盖）。"""
        shift = self.shifts[index]
        return resolve_entry_config(self.entry_config(), index, shift.label, overrides)

    def shift_labels(self) -> List[str]:
        """各班次名（界面按班次配置时用）。"""
        return [s.label for s in self.shifts]


# ============================================================================
# 装配：从若干文件到一个排班
# ============================================================================
@dataclass
class LoadedSchedule:
    """一次「导入排班」的结果：排班本体 + 从文件里读到的附赠信息。

    - `pool`：干员池（只有 v4 蓝图+干员池那类文件才有；`[{"name","elite","level","own"}]`）
    - `initial_global`：变量初始值（v4 的 `layout.scenario.initial_global`）
    - `reports`：每个文件的导入报告（识别到什么格式、忽略/推断了什么）
    """
    schedule: Schedule
    pool: List[dict] = field(default_factory=list)
    initial_global: Dict[str, Decimal] = field(default_factory=dict)
    reports: List[object] = field(default_factory=list)

    def summary(self) -> str:
        """一行导入摘要（状态栏用）。"""
        return "；".join(r.summary() for r in self.reports)


def load_schedule_from_imports(imports: Sequence[Any],
                               cycle_hours: Optional[Decimal] = None,
                               hours: Optional[Sequence] = None) -> LoadedSchedule:
    """把若干个**已解析**的导入结果（`store.sources.ImportResult`）装配成 `Schedule`。

    `load_schedule_ex`（按文件）只是"先 `import_file` 再把结果交进来"，两条入口**共用这里**——
    所以 API 的 `load_file` 与内联 `load_json` 的数值必然同源，不会各算一套。

    - 每个导入结果可能含多个班次（MAA 的多个 plan、v3 输出的多班 `shifts`）；
    - 每班时长：**显式 `hours`（按班次顺序，可少于班次数）> 文件里的时长 > 班次名提示 > 均分**；
    - 周期：默认 = 各班时长之和（自洽）；显式给 `cycle_hours` 时必须与之和相等。
    """
    shifts: List[Shift] = []
    pool: List[dict] = []
    initial: Dict[str, Decimal] = {}
    reports: List[object] = []
    given = [to_decimal(h) for h in hours] if hours else []
    for imp in imports:
        reports.append(imp.report)
        for entry in imp.pool:                      # 多文件的池取并集（先到先得）
            if entry["name"] not in [x["name"] for x in pool]:
                pool.append(entry)
        initial.update(imp.initial_global or {})
        new = shifts_from_import(imp)
        for s in new:                                # 显式 hours 按顺序覆盖
            if given:
                s.hours = given.pop(0)
        shifts.extend(new)
    if not shifts:
        raise ValueError("没有解析出任何班次")
    total = _hours_from_hints(shifts, cycle_hours)
    if cycle_hours is not None and to_decimal(cycle_hours) != total:
        raise ValueError(f"各班时长之和 {total}h ≠ 指定周期 {to_decimal(cycle_hours)}h")
    for s in shifts:                                 # 变量初始值挂到每个班次
        if initial and not s.initial_global:
            s.initial_global = dict(initial)
    return LoadedSchedule(schedule=Schedule(shifts, total), pool=pool,
                          initial_global=initial, reports=reports)


def load_schedule_ex(paths: Sequence[Union[str, Path]],
                     cycle_hours: Optional[Decimal] = None,
                     hours: Optional[Sequence] = None) -> LoadedSchedule:
    """从若干排班文件装配 `Schedule`（每份文件**自动识别格式**，见 `store/sources.py`）。

    装配规则（时长 / 周期 / 池 / 变量初始值）见 `load_schedule_from_imports`。
    """
    return load_schedule_from_imports([import_file(p) for p in paths],
                                      cycle_hours=cycle_hours, hours=hours)


def load_schedule(paths: Sequence[Union[str, Path]],
                  cycle_hours: Optional[Decimal] = None,
                  hours: Optional[Sequence] = None) -> Schedule:
    """同 `load_schedule_ex`，只返回排班本体（旧调用点用）。"""
    return load_schedule_ex(paths, cycle_hours=cycle_hours, hours=hours).schedule


# ============================================================================
# 轨迹：事件驱动的分段线性解
# ============================================================================
@dataclass
class Mark:
    """时间轴上的一个标记（画图/提示用）。"""
    t: Decimal
    kind: str          # 'shift' | 'entry' | 'redface'
    label: str = ""


@dataclass
class Trajectory:
    """整周期的分段线性心情轨迹（节点＝事件时刻，节点之间线性）。

    `segments`：每个班次段的 `(起, 止, 那一份**布局副本**)` —— 副本是模拟真正用的那一份
    （入驻事件的位置互换、闲置入宿的换人都只改它，**不改排班快照**）。
    界面要显示"引擎这一刻怎么排的"就读它（`world_at`），别去读 `schedule.shifts[i].world`
    —— 那是"你导入的排班"，两者在换过人之后会不一样。

    `idle_states`：**闲置入宿逐位候选**的那一份宿舍态（`{段起点: {候选名: dorm_state}}`），
    供界面「闲置入宿」逐次表逐行取用（`idle_state_at`）—— 候选是依次处理的，排在后面的人
    看到的宿舍已经不是班初那份了（前面的人会被换出去）。
    """
    names: List[str]
    times: List[Decimal]
    moods: Dict[str, List[Decimal]]
    schedule: Schedule
    cycles: int = 1
    marks: List[Mark] = field(default_factory=list)
    segments: List[Tuple[Decimal, Decimal, "BaseLayout"]] = field(default_factory=list)
    idle_states: Dict[Decimal, Dict[str, dict]] = field(default_factory=dict)

    # ------------------------------------------------------------------ 查询
    @property
    def total_hours(self) -> Decimal:
        return self.times[-1]

    def _segment(self, t) -> Optional[Tuple[Decimal, Decimal, "BaseLayout"]]:
        """`t` 落在哪一段（`None` = 这个轨迹没记段信息）；边界取**右侧**。"""
        if not self.segments:
            return None
        t = to_decimal(t)
        i = bisect.bisect_right([s[0] for s in self.segments], t) - 1
        if i < 0:
            i = 0
        return self.segments[i]

    def world_at(self, t) -> Optional["BaseLayout"]:
        """时刻 `t` 生效的那份**模拟布局副本**（`None` = 这个轨迹没记段信息）。

        ⚠️ 落在班次边界上取**右侧**（那一刻开始生效的班次），与 `mood_at` / `rate_at`
        的"同刻取跳变后"一致：`t = 12` 给出"12:00 起上班的那一班"的布局。
        """
        seg = self._segment(t)
        return None if seg is None else seg[2]

    def idle_state_at(self, t, name: str) -> Optional[dict]:
        """**轮到 `name` 的那一刻**引擎的宿舍态（`None` = 没开闲置入宿 / 她不是候选）。

        界面「闲置入宿」逐次表用它算每一行的「换谁」与「宿舍NN」：候选是**依次**处理的，
        必须按**她自己那一刻**的世界出选项 —— 否则会列出"这一刻已经被前面的人换出宿舍"
        的对象（用户报过"面板里列着清流、那一刻宿舍里并没有清流"）。
        取不到时调用方退回 `world_at(t)`（整份段世界）那份。
        """
        seg = self._segment(t)
        if seg is None:
            return None
        return self.idle_states.get(seg[0], {}).get(name)

    def mood_at(self, name: str, t) -> Decimal:
        """时刻 t 的心情（节点之间线性插值；超界取端点值）。

        ⚠️ 正好落在**同刻跳变**（进驻事件 / 闲置入宿 / 心情指定事件）上时取**跳变后**的值：
        轨迹在同一时刻有两个节点（跳变前 / 跳变后），`bisect_right` 自然落在后一个上。
        ⚠️ 所以这里**不能**用 `if t <= ts[0]: return vals[0]` 提前返回 —— `t = 0` 恰好就是
        "周期起点被事件改过"的那种情况，提前返回会取到跳变**前**的值（老 bug）。
        """
        vals = self.moods[name]
        t = to_decimal(t)
        ts = self.times
        i = bisect.bisect_right(ts, t)
        if i == 0:
            return vals[0]
        if i >= len(ts):
            return vals[-1]
        t0, t1 = ts[i - 1], ts[i]
        v0, v1 = vals[i - 1], vals[i]
        if t1 == t0:
            return v1
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    def moods_at(self, t) -> Dict[str, Decimal]:
        """某时刻所有干员的心情（界面"时间滑动"用的就是这个）。"""
        return {n: self.mood_at(n, t) for n in self.names}

    def rate_at(self, name: str, t) -> Decimal:
        """某一时刻的心情变化速率（**点 / 时**；`>0` 表示在下降、`<0` 表示在上升）。

        段内速率恒定（这是"事件驱动精确积分"的前提），所以取该时刻所在折线的斜率就是**精确值**。
        正好落在**跳变节点**（进驻事件 / 闲置入宿那种同刻跳变）上时取**右侧**那一段——
        即"跳变之后正在按什么速率走"。
        """
        t = to_decimal(t)
        times = self.times
        if len(times) < 2 or name not in self.moods:
            return ZERO
        i = bisect.bisect_right(times, t) - 1
        if i < 0:
            i = 0
        while i + 1 < len(times) - 1 and times[i + 1] == times[i]:
            i += 1                        # 同刻跳变：跳到右边那一段
        if i + 1 >= len(times):
            i = len(times) - 2
        dt = times[i + 1] - times[i]
        if dt == 0:
            return ZERO
        series = self.moods[name]
        return -(series[i + 1] - series[i]) / dt

    def shift_average_rate(self, name: str, index: int) -> Decimal:
        """第 `index` 班（0 基）那一班里的**平均**速率（点 / 时，含进班那一刻的跳变）。"""
        if self.schedule is None or index < 0 or index >= len(self.schedule.shifts):
            return ZERO
        start = self.schedule.starts[index]
        hours = self.schedule.shifts[index].hours
        if hours == 0:
            return ZERO
        return -(self.mood_at(name, start + hours) - self.mood_at(name, start)) / hours

    def bounds(self, name: str) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """(最低值, 最低时刻, 最高值, 最高时刻)。"""
        vals, ts = self.moods[name], self.times
        lo = hi = vals[0]
        lo_t = hi_t = ts[0]
        for t, v in zip(ts, vals):
            if v < lo:
                lo, lo_t = v, t
            if v > hi:
                hi, hi_t = v, t
        return lo, lo_t, hi, hi_t

    def red_face_spans(self, name: str) -> List[Tuple[Decimal, Decimal]]:
        """红脸（心情 == 0）的时间区间列表。"""
        spans: List[Tuple[Decimal, Decimal]] = []
        vals, ts = self.moods[name], self.times
        start = None
        for t, v in zip(ts, vals):
            if v <= ZERO and start is None:
                start = t
            elif v > ZERO and start is not None:
                spans.append((start, t))
                start = None
        if start is not None:
            spans.append((start, ts[-1]))
        return spans

    def sample(self, name: str, points: int = 480) -> List[Tuple[Decimal, Decimal]]:
        """按等距采样给画图用（因为轨迹是折线，采样只是展示密度，不引入误差）。"""
        total = self.total_hours
        if points < 2:
            points = 2
        step = total / (points - 1)
        return [(step * i, self.mood_at(name, step * i)) for i in range(points)]

    def min_mood_at_each_shift(self, name: str) -> List[Tuple[str, Decimal]]:
        """每个班次内该干员的最低心情（"哪一班最危险"）。"""
        out = []
        for i, s in enumerate(self.schedule.shifts):
            start = self.schedule.starts[i]
            end = start + s.hours
            lo = MOOD_MAX
            for t, v in zip(self.times, self.moods[name]):
                if start <= t <= end and v < lo:
                    lo = v
            out.append((s.label, lo))
        return out


def rates_in_world(world, names: Sequence[str]) -> Dict[str, Decimal]:
    """某个布局快照下、各干员的净速率（未进驻者 = 0）。

    与 `compute_rates` 的区别：直接吃一个 `BaseLayout`，用于"模拟期间布局会被改动"
    的场景（如进驻事件把两人的位置也对调了）。
    """
    return {n: (compute_net_rate(world, n) if world.get_operator(n) is not None else ZERO)
            for n in names}


def compute_rates(schedule: Schedule, index: int, moods: Dict[str, Decimal],
                  names: Sequence[str]) -> Dict[str, Decimal]:
    """某班次下、给定心情快照时的净速率（未排班者 = 0）。"""
    return rates_in_world(schedule.shifts[index].world, names)


def _sync_moods(world, moods: Dict[str, Decimal]) -> None:
    """把模拟中的心情写进布局副本（进驻事件要按"当前心情"判断条件）。"""
    for o in world.all_operators():
        if o.name in moods:
            o.mood = moods[o.name]


def _read_back_moods(world, moods: Dict[str, Decimal]) -> None:
    for o in world.all_operators():
        if o.name in moods:
            moods[o.name] = o.mood


def _record_jump(times, series, names, moods, t) -> None:
    """记一个**同刻跳变**（进驻事件 / 闲置入宿 / 心情指定事件）。

    做法是**追加同刻的第二个节点**：`(t, 跳变前)` 已经由推进循环写下了，这里再写 `(t, 跳变后)`。
    于是折线上这一段是**竖直**的，而 `mood_at(t)` 取到的正是**跳变后**的值
    （`mood_at` 用 `bisect_right`，同刻节点里它落在后一个上）。

    ⚠️ 不要改回"直接改写前一个节点"：那样跳变**之前**的那一小段会被插值成斜坡——
    旧实现就有这个毛病，跳变前最多 15 分钟（一个 `MAX_SEGMENT_HOURS`）的曲线会从旧值
    斜着爬到新值，看起来像"提前开始换心情"。
    """
    times.append(t)
    for n in names:
        series[n].append(moods[n])


def _next_event(schedule: Schedule, moods: Dict[str, Decimal], rates: Dict[str, Decimal],
                t: Decimal, seg_end: Decimal, groups: Sequence[Sequence[str]]):
    """求下一个会让速率改变的时刻。

    返回 `(时刻, 吸附表, 就地吸附的人)`：

    - **吸附表** `{干员: 阈值}` = "这个人的心情正好在**本段末尾**跨过该阈值"，积分之后
      再把它**精确置为阈值**——否则 `dt = (m-h)/r` 的非终止小数会让结果带上
      `16.19999999999999999999999999` 这样的尾巴（数值卫生，不是精确性问题）。
    - **就地吸附的人** = 有人在 `MIN_EVENT_GAP` 以内**已经**跨过阈值（只差末位舍入那种），
      本函数**当场**把它的心情置为阈值，并把这批人报给调用方去重算速率
      （依赖心情的条件要按吸附后的值判）。

    ⚠️ **这两条必须分开，不能把"只差 1e-26"的人也塞进吸附表**：吸附表是在**积分之后**
    才应用的，而那一刻 `nxt` 可能已经过去了一整个 `MAX_SEGMENT_HOURS` —— 积分早就把这个人
    推过阈值了，再"吸附"回去等于**把它往回拽**，白白扣掉一整段心情变化。
    实测（2026-09，示例排班宿舍段）：默认步长 0.25h 时同宿舍 8 名干员在阈值处被倒扣
    **整整 1.0**（速率 4/h × 0.25h），曲线看着像"卡在 20 不动、未满 24"；步长调到 0.05h
    就全对——**步长不该影响结果**，所以那是 bug 不是口径。见 `04-特殊机制.md` 第 35 条。
    """
    nxt = seg_end
    snaps: Dict[str, Decimal] = {}
    snapped: Dict[str, Decimal] = {}          # 就地吸附（此刻已在阈值上）
    # ① 心情触到阈值（含 0 与 24）
    for name, r in rates.items():
        if r == ZERO:
            continue
        m = moods[name]
        for h in EVENT_THRESHOLDS:
            if not ((r > ZERO and m > h) or (r < ZERO and m < h)):
                continue
            dt = (m - h) / r
            if dt < MIN_EVENT_GAP:
                # 此刻**已经**在阈值上（只差末位舍入）→ 就地吸附，绝不留给"积分之后"
                if moods[name] != h:
                    moods[name] = h
                    snapped[name] = h
            elif dt < nxt - t:
                nxt = t + dt
                snaps = {name: h}         # 更早的时刻 ⇒ 之前收的那些都在它之后，作废
            elif dt == nxt - t:
                snaps.setdefault(name, h)  # 同一时刻跨阈值 ⇒ 一起吸附（原先会互相顶掉）
    # ② 同设施内两人心情交叉（单体回复受益者 / 池分配对象会换人）
    for group in groups:
        for i in range(len(group)):
            ni = group[i]
            for j in range(i + 1, len(group)):
                nj = group[j]
                dv = rates[ni] - rates[nj]
                if dv == ZERO:
                    continue
                dt = (moods[nj] - moods[ni]) / dv
                if MIN_EVENT_GAP <= dt < nxt - t:
                    nxt = t + dt
                    snaps = {}          # 交叉事件不吸附（两人相等，谁前谁后都不影响取值）
    return nxt, snaps, snapped


def default_initial_moods(schedule: Schedule) -> Dict[str, Decimal]:
    """周期起点心情的缺省值：**第一班布局里写的值**（没有则满心情 24）。

    为什么不是一律 24：场景/排班文件可以带起始心情（`{"name":"x","mood":10}`），
    直接导入时那个值就是"这个周期的起点"。界面上手动设的心情也走同一入口。

    ⚠️ 两类人**不在第一班的 facilities 里**，这里要单独照顾：
      - **只在别的班次出场**的人（如只在第 2 班顶班）——去所有班次里找他写的 `mood`；
      - **「不在基建」的人**（`schedule.bench_names()`）——谁都没他的 `mood` 字段，
        缺省给满心情 24（要改就由 `initial_moods` 覆盖，见 `ui/batch.py` 那一段）。
    """
    base = {n: MOOD_MAX for n in schedule.operator_names()}
    if schedule.shifts:
        for op in schedule.shifts[0].world.all_operators():
            if op.name in base:
                base[op.name] = to_decimal(op.mood)
        # 第一班里没有的那些人：逐个去所有班次里找他写的 mood
        for name in list(base):
            if schedule.shifts[0].world.get_operator(name) is not None:
                continue
            for s in schedule.shifts:
                op = s.world.get_operator(name)
                if op is not None:
                    base[name] = to_decimal(op.mood)
                    break
    return base


@dataclass
class MoodSetEvent:
    """「**心情指定事件**」：在**某个周期的某个时刻**把某位干员的心情**直接置为**给定值。

    这是"心情指定事件"的唯一载体（界面「干员与心情」面板里的锚点就是它）：

    | 字段 | 含义 |
    |---|---|
    | `name` | 干员名 |
    | `t` | **周期内时刻**（`0 ≤ t < 周期时长`，单位小时） |
    | `mood` | 那一刻直接置成的值（构造时钳位到 `[0, 24]`） |
    | `cycle` | **第几个周期**（1 基）。**只对这个周期生效**，别的周期不受影响 |

    语义：那一刻她"被调到"这个心情（例如刚吃了体力药、刚被换下来休息），
    **之后按正常速率演化**。轨迹上它是**同刻跳变**——`mood_at(t)` 取到的正是设定值。

    ⚠️ 同一时刻若与进驻事件 / 闲置入宿重合，**锚点最后生效**（显式设定优先于机制推算）。
    ⚠️ `cycle` 超出当前「周期数」范围（比如设了第 3 周期、后来周期数改成 1）时**不生效**，
    也不会报错——面板会把这类锚点标出来。
    """
    name: str
    t: Decimal
    mood: Decimal
    cycle: int = 1

    def __post_init__(self):
        self.t = to_decimal(self.t)
        self.mood = _clamp_mood(to_decimal(self.mood))
        self.cycle = int(self.cycle)

    def absolute(self, cycle_hours) -> Decimal:
        """换算成"从轨迹起点算起"的绝对时刻（`(cycle-1) * 周期 + t`）。"""
        return to_decimal(self.t) + to_decimal(cycle_hours) * (self.cycle - 1)


def _clamp_mood(value: Decimal) -> Decimal:
    """心情钳位到 `[0, 24]`（与引擎其余部分同一口径）。"""
    if value > MOOD_MAX:
        return MOOD_MAX
    if value < MOOD_MIN:
        return MOOD_MIN
    return value


def _fmt_value(value: Decimal) -> str:
    """`Decimal` → 短字符串（给标记文案用；本模块不依赖 `ui.theme`，它是要拉 tkinter 的）。"""
    return format(value.normalize(), "f")


def simulate_schedule(schedule: Schedule, cycles: int = 1,
                      initial_moods: Optional[Dict[str, Decimal]] = None,
                      entry_events: bool = False,
                      entry_swap_with: Optional[str] = None,
                      entry_scope: Optional[str] = None,
                      entry_restore_back: Optional[bool] = None,
                      entry_force: Optional[bool] = None,
                      entry_when: Optional[str] = None,
                      entry_per_shift: Optional[List[EntryShiftOverride]] = None,
                      idle_to_dorm: bool = True,
                      idle_entries: Optional[Sequence["IdleToDormEntry"]] = None,
                      mood_events: Optional[Sequence[MoodSetEvent]] = None,
                      max_segment: Decimal = MAX_SEGMENT_HOURS) -> Trajectory:
    """把排班跑成"整周期心情轨迹"（事件驱动精确积分）。

    ⚠️ **入口先固定本线程的 Decimal 上下文**（`use_project_decimal_context`）：`decimal` 的
    上下文是线程局部的，后台线程默认是 `ROUND_HALF_EVEN`，不设就会在末位差 1e-26 并连锁
    改变事件时刻（界面异步重算 P5 实测踩到：主线程与工作线程 52 名干员的轨迹不同）。

    参数：
        cycles        跑几个周期（心情跨周期连续，用来看是否收敛）
        initial_moods 周期起点的心情；缺省取**第一班布局里写的值**（没有则 24）
        entry_events  总开关：要不要结算进驻事件（M15a 患难之交；默认否）
        entry_swap_with  与**谁**换：人名 / `"any"`（自动挑全基建最累的）/
                       `None`（用场景 JSON，再没有＝「前一位进驻」）
        entry_scope      `"dorm"`（限同宿舍）/ `"anywhere"`（**基建任意位置**）；`None` = 用 JSON
        entry_restore_back  `True` = 只换心情、两人留在原位置（默认）；`False` = **位置也一起互换**
        entry_force      **旧参数**（`True` = 等她回满再换；`False` = 只在她满心情时换）
        entry_when       **什么时候换**：`"immediate"`（默认，**强制立刻换**：不管她满不满、
                       也不管对方心情是多少）/ `"wait"`（等她回满再换）/ `"full"`（只在她满心情时换）；
                       `None` = 用场景 JSON
        entry_per_shift  **按班次覆盖**（`[EntryShiftOverride, ...]`）——
                       3 班排班就可以"第 1 班换给巫恋、第 2 班自动挑最累的、第 3 班不用"；
                       `None` = 用场景 JSON 里的 `per_shift`
        idle_to_dorm  **闲置入宿**：每班开始时把"没在上班、也不在宿舍、心情还没满"的干员
                       安排进宿舍（有空位就放进去，没空位就与宿舍里心情已满的那位互换）。
                       ⚠️ **默认 `True`（开）**（用户口径"闲置入宿默认是开启的"）——
                       要"完全不动布局"的旧口径就显式传 `False`。
        idle_entries  界面的逐人设置（`[IdleToDormEntry, ...]`，**只列改过默认的**：
                       不参与的人、或指定了交换对象的人）。给了它就**盖过** JSON 里的
                       `idle_to_dorm.per_operator`（界面口径优先）；`None` = 用 JSON。
        mood_events   **心情指定事件**（`[MoodSetEvent, ...]`，见那个类）：在
                       `(周期, 周期内时刻)` 把某位干员的心情**直接置为**给定值，
                       之后按正常速率演化（同刻跳变 + 速率重算）。缺省 `None` = 没有。
        max_segment   单段最长时长（兜底安全上限）

    数值说明：单段内心情是精确的线性函数；误差只来自 Decimal 除法在 28 位有效数字处的
    舍入（量级 1e-26），界面上按"显示边界"舍入到 2 位小数即可。

    **「不在基建」的人**（`schedule.bench_names()`，来自场景 JSON 顶层 `detached`）：
    他们在轨迹里**是一条平线**，心情恒等于起点心情。这不是特例代码，而是三条既有规矩的
    自然结果 —— ① `names` 含他们（`iterator.operator_names()` 已并上名单）；
    ② 他们不在任何 `world` 里 ⇒ `rates_in_world` 给 0 ⇒ 心情不消耗也不回复；
    ③ `_next_event` 遇到 `r == 0` 直接跳过 ⇒ 不会为他们生成事件。
    另外他们**不参与任何技能计数**（计数读的是 `world.facilities` 里的进驻者），
    这条有测试钉死（`tests/test_equivalence.py::Test不在基建`）。

    实现说明：**每一段（含每个周期的每一班）都从一份"未动过的计划副本"重建**当前布局
    （`pristine` —— 见段循环里的说明）——这样 `entry_restore_back=False`（位置也互换）与
    闲置入宿的换人**只在这一次生效**，既不污染排班本身、也不会跨周期继承；
    同时把"这一班的有效配置"挂到副本上，`apply_entry_events` 直接读它。
    """
    use_project_decimal_context()      # 本线程的 Decimal 上下文（见上：线程局部，必须显式设）
    if cycles < 1:
        raise ValueError("cycles 至少为 1")
    total = schedule.cycle_hours * Decimal(cycles)
    names = schedule.operator_names()
    start_moods = default_initial_moods(schedule)
    if initial_moods:
        start_moods.update({k: to_decimal(v) for k, v in initial_moods.items()})
    moods: Dict[str, Decimal] = {n: start_moods.get(n, MOOD_MAX) for n in names}

    cfg = schedule.entry_config()
    when = entry_when if entry_when is not None else getattr(cfg, "when", None)
    base = EntryEventConfig(
        enabled=None,                     # 由 entry_events 总开关决定
        swap_with=(entry_swap_with if entry_swap_with is not None else cfg.swap_with),
        scope=(entry_scope if entry_scope is not None else cfg.scope),
        restore_back=(entry_restore_back if entry_restore_back is not None else cfg.restore_back),
        force=(entry_force if entry_force is not None else cfg.force),
        # ⚠️ `when` 必须显式带上：否则会退化成 dataclass 默认值，把 JSON 里的
        #    "只在她满心情时换 / 等她回满再换" 覆盖成"强制立刻换"。
        when=(normalize_entry_when(when) or "immediate"),
    )
    #: 每个班次一份**"从未被动过"的计划副本** —— 引擎每段（含每个周期）都从它**重建**当前布局，
    #: 见下面段循环里的说明（副本绝不能被就地改着复用）。
    pristine = [copy.deepcopy(s.world) for s in schedule.shifts]
    # 闲置入宿：界面传了逐人设置就盖过 JSON 的（"界面口径优先"，与进驻事件同一约定）
    if idle_to_dorm:
        idle_cfg = (IdleToDormConfig(enabled=True, per_operator=list(idle_entries))
                    if idle_entries is not None else None)
        if idle_cfg is not None:
            for w in pristine:
                w.idle_to_dorm = idle_cfg
    # 每个班次解析一次"这一班的有效配置"，挂到该班次的副本上（按班次覆盖在这里生效）。
    # ⚠️ 覆盖列表要**显式传给 resolve_entry_config**：它默认读的是"第一个参数"的 per_shift，
    #    而这里的 base 是拼出来的（per_shift 为空），不显式传就会把按班次配置整段忽略。
    per_shift = list(entry_per_shift) if entry_per_shift is not None else list(cfg.per_shift)
    effs: List[EntryEventConfig] = []
    for i, s in enumerate(schedule.shifts):
        eff = resolve_entry_config(base, i, s.label, per_shift)
        effs.append(eff)
        pristine[i].entry_events = eff

    times: List[Decimal] = [ZERO]
    series: Dict[str, List[Decimal]] = {n: [moods[n]] for n in names}
    marks: List[Mark] = []
    for i, s in enumerate(schedule.shifts):
        marks.append(Mark(schedule.starts[i], "shift", s.label))

    # —— 心情指定事件：换算成**绝对时刻** → `{绝对时刻: {干员: 值}}` ——
    # 只对 `1 <= cycle <= cycles` 的生效（周期数被调小后超范围的那些自然失效）；
    # 落在 `[0, total)` 之外（例如"最后一个周期的末尾"）的忽略——它在轨迹上没有下一步可影响。
    mood_at_time: Dict[Decimal, Dict[str, Decimal]] = {}
    for ev in (mood_events or ()):
        cyc = int(getattr(ev, "cycle", 1) or 1)
        if cyc < 1 or cyc > cycles:
            continue
        at = to_decimal(ev.t) + schedule.cycle_hours * (cyc - 1)
        if at < ZERO or at >= total:
            continue
        mood_at_time.setdefault(at, {})[ev.name] = _clamp_mood(to_decimal(ev.mood))
    mood_times: List[Decimal] = sorted(mood_at_time)

    # 覆盖 [0, total] 的班次段（跨周期重复）
    segments: List[Tuple[Decimal, Decimal, int]] = []
    t = ZERO
    while t < total:
        for i, s in enumerate(schedule.shifts):
            if t >= total:
                break
            end = min(t + s.hours, total)
            segments.append((t, end, i))
            t = end

    # 每段**实际用的那份布局副本**（界面要显示"引擎这一刻怎么排的"，见 `Trajectory.world_at`）
    seg_worlds: List[BaseLayout] = []
    # 每段**逐位候选**的宿舍态（界面「闲置入宿」逐次表逐行取用，见 `Trajectory.idle_state_at`）
    idle_states: Dict[Decimal, Dict[str, dict]] = {}

    for seg_i, (t0, seg_end, idx) in enumerate(segments):
        # ⚠️ **每段（含每个周期）都从"未动过的计划副本"重建**：布局改动只有两处 ——
        #    进驻事件的「位置也一起互换」（`restore_back=False`）与**闲置入宿的换人** ——
        #    它们都**就地改**这份副本；而同一个班次会被**跨班次、跨周期复用**，
        #    不重建就会把上一轮改过的位置继承下来：第 2/3 周期那一班开局就没有她、
        #    **既不重判心情、也没有任何事件**（用户报过"菲亚梅塔 20.4 却显示被闲置入宿换出"：
        #    那次换出是第 1 周期用 24 判的旧决定，后面几个周期压根没再判）。
        #    ⚠️ 重建只碰**布局**：心情在 `moods` 里，照旧**跨周期连续**（那是模型口径）。
        #    成本 = 每段一次深拷贝（示例实测 ≈0.35ms，7 周期 21 段 ≈ 7ms）。
        world = copy.deepcopy(pristine[idx])
        eff = effs[idx]                     # 本班次的**有效**进驻事件配置（含按班次覆盖）
        pending: List[str] = []             # "等她回满心情再换"的触发者（force）
        # —— 进驻事件（可选）：进入班次那一刻先试一次 ——
        # 这是 t0 处的**跳变**：t0 之前是换心情前的值，t0 起是换之后的（就地改写节点，
        # 而不是追加同刻节点——否则 mood_at(t0) 会取到跳变前的旧值）。
        # 总开关（entry_events）打开时，逐班看这次要不要做（per_shift 里写 "enabled": false 就不做）。
        if entry_events and eff.enabled is not False:
            _sync_moods(world, moods)
            # ⚠️ 班次开始 = 一次**新的进驻瞬间**：副本虽然每段都重建（见段首），但
            #    `apply_entry_events` 用 `Operator.entry_swapped` 保证"同一份快照只结算一次"，
            #    而**段内**还会因「等她回满」再进这个分支 ⇒ 每次进班次前都要归位，
            #    否则第 2 个周期被标记挡住、换心情一次都不触发（多周期漏算的老 bug）。
            reset_entry_events(world)
            events = apply_entry_events(world, enabled=True)
            # ⚠️ 事件里可能只有"未执行"的说明（如配了 force 但她此刻没满心情）——
            # 那种情况既不算换成功、也不能拦住"等她回满"的等待逻辑。
            swapped = [ev for ev in events if ev.group == "entry_swap"]
            for ev in events:
                marks.append(Mark(t0, "entry", ev.detail))
            if swapped:
                _read_back_moods(world, moods)
                _record_jump(times, series, names, moods, t0)
            elif eff.when == "wait":
                # 「等她回满再换」：此刻她不满心情 → 登记，等她回满**那一刻**再换
                pending = [h for h, _room in entry_event_holders(world)
                           if moods.get(h, MOOD_MAX) < MOOD_MAX]

        # —— 闲置入宿（可选）：把"没在上班、也不在宿舍、心情还没满"的干员安排进宿舍 ——
        # 顺序上**排在进驻事件之后**：换心情是"进驻那一刻"的游戏事件，闲置入宿是人工调度；
        # 而且闲置入宿会把满心情的人换出宿舍，先换心情可以保住"前一位进驻"的判定基准。
        # ⚠️ 本班未排班的干员不在 `world` 里（心情在 `moods` 字典里），所以要把他们的心情传进去。
        if idle_to_dorm:
            _sync_moods(world, moods)
            idle_moods = {n: moods[n] for n in names if world.get_operator(n) is None}
            # 这一刻是"第几周期的第几班"：逐人设置按它取最具体的那一条
            # （心情跨班跨周期连续 ⇒ 每次的候选与可交换对象都不一样）
            scope = (seg_i // len(schedule.shifts) + 1, idx + 1)
            # 逐位候选各留一份"轮到她的那一刻"的宿舍态 → 界面逐次表逐行取用
            # （面板不能拿班末那份 `world_at` 或排班快照当"她那一刻的世界"）。
            per_cand: Dict[str, dict] = {}
            for ev in apply_idle_to_dorm(world, enabled=True, idle=idle_moods, scope=scope,
                                         trace=per_cand):
                marks.append(Mark(t0, "idle", ev.detail))
            idle_states[t0] = per_cand
        # 本段**实际生效**的那份布局 → `Trajectory.world_at(t)`（界面看板 / API `layout_at` 都读它）。
        # ⚠️ 闲置入宿 / 位置互换会**就地改这份副本**（`world` 是"本段这份"，每段重建，不再跨周期复用），
        #    而**段内**还可能再改它（`restore_back=False` 的「等她回满再换」）：快照要留在**段首**，
        #    这样 `world_at(t)` 给出的是"这一段开始时"的排布 —— 与"这一刻谁在宿舍"的判定口径一致。
        #    没开闲置入宿时位置只在段内被改（换心情只改心情值），照旧记引用。
        seg_worlds.append(copy.deepcopy(world) if idle_to_dorm else world)
        groups = [[o.name for o in f.operators] for f in world.facilities]

        t = t0
        rates = None
        # 本段内要"踩点"的心情指定事件（严格在段内；正好在段首那个由循环顶部的检查处理）
        seg_moods = [tt for tt in mood_times if t0 < tt < seg_end]
        while t < seg_end:
            # —— 心情指定事件：正好落在这一刻 → **直接置值**（同刻跳变）——
            # 放在循环顶部是为了统一处理"段首那一刻"（如班次边界、周期起点）与"段内踩点"：
            # 两者都是"推进到 t 之后置值"，而 `_record_jump` 在 `times[-1] == t` 时改写该节点，
            # 于是曲线在这里出现一条竖直跳变、`mood_at(t)` 取到的就是设定值。
            if t in mood_at_time:
                for n, v in mood_at_time[t].items():
                    if n not in moods:
                        continue
                    moods[n] = v
                    marks.append(Mark(t, "moodset", f"指定 {n} 心情={_fmt_value(v)}"))
                _record_jump(times, series, names, moods, t)
                rates = None                   # 心情变了 ⇒ 依赖心情的条件技能要重判
            # 速率只在"可能变了"时重算：进新班次、发生了事件（跨阈值/两人交叉/位置互换）、
            # 或走了兜底分支。纯"安全上限推进"时速率必然不变，直接复用（这是主要的提速点：
            # 重算一次全布局约 2.5ms，复用它能把 3 周期的重算从 ~1.9s 压到 ~0.2s）。
            if rates is None:
                # ⚠️ 算速率前必须把"真实心情"同步进副本：`rates_in_world` 读的是副本里每个干员的
                #    `.mood`，而时间推进的是上面那个 `moods` 字典。不同步的话，**依赖心情的条件技能**
                #    （宿舍单体回复该选谁、池分配、自身条件…）会拿旧值判断 —— 速率就错了。
                #    （示例排班恰好对心情不敏感，换成有这类技能的真实阵容就会歪。）
                _sync_moods(world, moods)
                rates = rates_in_world(world, names)
            nxt, snaps, snapped = _next_event(schedule, moods, rates, t, seg_end, groups)
            if snapped:
                # 就地吸附（只差末位舍入就跨过阈值）：把**刚记下的那个节点**也改成阈值，
                # 免得轨迹里留一条 1e-26 的尾巴与内部值不一致。
                for n, h in snapped.items():
                    if series.get(n):
                        series[n][-1] = h
            event_fired = bool(snaps) or bool(snapped) or nxt < seg_end
            # ⚠️ 不许**跨过**心情指定事件：把 nxt 截到它那一刻，并丢掉原本那个时刻的阈值吸附
            #    （吸附是按"原 nxt"算的，截断之后不再成立——留着会把心情钉在不该钉的值上）。
            for tt in seg_moods:
                if t < tt < nxt:
                    nxt, snaps, event_fired = tt, {}, True
                    break
            if nxt - t > max_segment:          # 兜底上限截断 → 本段没有真事件
                nxt, snaps, event_fired = t + max_segment, {}, bool(snapped)
            if nxt <= t:                       # 数值兜底，绝不原地打转
                nxt, snaps, event_fired = min(t + max_segment, seg_end), {}, bool(snapped)
            if nxt <= t:                       # 数值兜底，绝不原地打转
                nxt, snaps, event_fired = min(t + max_segment, seg_end), {}, False
            dt = nxt - t
            for n in names:
                r = rates[n]
                if r != ZERO:
                    m = moods[n] - r * dt
                    moods[n] = MOOD_MAX if m > MOOD_MAX else (MOOD_MIN if m < MOOD_MIN else m)
            for n, h in snaps.items():        # 跨阈值者精确置为阈值（去掉数值尾巴）
                moods[n] = h
            t = nxt
            times.append(t)
            for n in names:
                series[n].append(moods[n])
            # —— entry_force：有人刚回到满心情 → 立刻换 ——
            # 只尝试一次（pending 清空）：否则"两人都满、换不动"时会每个 tick 反复触发。
            if pending and any(moods.get(h, ZERO) >= MOOD_MAX for h in pending):
                pending = []
                _sync_moods(world, moods)
                events = apply_entry_events(world, enabled=True)
                if events:
                    for ev in events:
                        marks.append(Mark(t, "entry", ev.detail))
                    if any(ev.group == "entry_swap" for ev in events):
                        _read_back_moods(world, moods)
                        _record_jump(times, series, names, moods, t)
                rates = None
                groups = [[o.name for o in f.operators] for f in world.facilities]
            elif event_fired:
                rates = None

    # 红脸区间记成标记（画图时画阴影）
    traj = Trajectory(names=names, times=times, moods=series, schedule=schedule,
                      cycles=cycles, marks=marks,
                      segments=[(a, b, w) for (a, b, _i), w in zip(segments, seg_worlds)],
                      idle_states=idle_states)
    for n in names:
        for a, b in traj.red_face_spans(n):
            marks.append(Mark(a, "redface", n))
    return traj


# ============================================================================
# 干员候选（界面上的"选人"下拉用）
# ============================================================================
_OPERATOR_CACHE: Optional[List[str]] = None


def all_operator_names(extra: Iterable[str] = ()) -> List[str]:
    """全部可选干员名：`data/operators.txt`（全量 921 名）+ 内置心情技能表 + 指定补充。

    全量名册优先（它含只有生产/训练技能的干员）；读不到时退回内置表。
    """
    global _OPERATOR_CACHE
    if _OPERATOR_CACHE is None:
        from data.paths import OPERATORS_TXT
        names = set(DEFAULT_OPERATORS)
        try:
            with open(OPERATORS_TXT, "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) > 1 and parts[0] != "operator_id":
                        names.add(parts[1])
        except OSError:
            pass
        _OPERATOR_CACHE = sorted(names)
    return sorted(set(_OPERATOR_CACHE) | set(extra))


__all__ = [
    "DEFAULT_CYCLE_HOURS", "EVENT_THRESHOLDS", "MAX_SEGMENT_HOURS", "MIN_EVENT_GAP",
    "Shift", "Schedule", "Trajectory", "Mark", "LoadedSchedule", "MoodSetEvent",
    "shift_from_facilities", "shifts_from_maa_file", "shifts_from_scenario_file",
    "shifts_from_import", "shift_file_kind", "load_schedule", "load_schedule_ex",
    "load_schedule_from_imports",
    "simulate_schedule", "compute_rates",
    "rates_in_world", "default_initial_moods", "all_operator_names",
]
