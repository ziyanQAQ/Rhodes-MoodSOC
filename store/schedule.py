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

from mood_soc import (apply_entry_events, apply_idle_to_dorm, compute_net_rate, net_rates,
                      entry_event_holders, reset_entry_events)
from mood_soc.battery import ZERO, to_decimal
from mood_soc.config import MOOD_MAX, MOOD_MIN, use_project_decimal_context
from mood_soc.models import (BaseLayout, EntryEventConfig, EntryShiftOverride,
                             IdleToDormConfig,
                             build_entry_event_config, build_idle_to_dorm_config,
                             normalize_entry_when, resolve_entry_config)
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
def _merged_names(*groups: Iterable[str]) -> List[str]:
    """把若干组名字**去重保序**并成一份（`Schedule.detached` / `roster` 共用同一手法）。"""
    seen, out = set(), []
    for group in groups:
        for n in (group or ()):
            n = str(n)
            if n and n not in seen:
                seen.add(n)
                out.append(n)
    return out


@dataclass
class Shift:
    """一个班次：一段时长 + 一份布局（+ 可选的「不在基建」名单）。"""
    label: str
    hours: Decimal
    facilities: List[dict]
    source: str = ""
    # 进驻事件配置（models.EntryEventConfig）：来自场景 JSON 顶层，随班次一起搬运
    entry_events: Optional[object] = None
    # **闲置入宿**配置（`models.IdleToDormConfig`，来自场景 JSON 顶层 `idle_to_dorm`）：
    # 与 `entry_events` 同层、同样**随班次一起搬运**。两种写法都收：
    # 已解析好的 `IdleToDormConfig`（重建时原样传），或**文件里那份原始值**
    # （`True` / `False` / `dict`，由 `__post_init__` 解析）。
    # ⚠️ 为什么必须留这个字段：`Shift` 是**可重建**的（`Schedule.with_hours` /
    #     `replaced_shift` / `with_detached` / `with_start_clock` 都会重建每个 `Shift`），
    #     而世界是由 `facilities` 现搭的 ⇒ 只挂在 `world` 上的全局设置在重建时会**静默
    #     退回默认值**（"碰一下「不在基建」名单就把面板上的闲置入宿配置打回 5/[]" 就是它）。
    idle_to_dorm: Optional[object] = None
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
        # ⚠️ **闲置入宿配置也要保留调用方给的值**（不是退回 `build_base_layout` 的默认值）：
        #    没有这一行，`Schedule.with_detached` 重建出来的世界就把全局设置丢成默认
        #    （`_sync_from_schedule` 再把它读回 `Session` ⇒ 面板设置被打回）。
        #    解析口径**只有一处**：`models.build_idle_to_dorm_config`（界面/导入同一份）。
        if self.idle_to_dorm is not None:
            self.world.idle_to_dorm = (
                self.idle_to_dorm if isinstance(self.idle_to_dorm, IdleToDormConfig)
                else build_idle_to_dorm_config(self.idle_to_dorm))
        self._names = [o.name for o in self.world.all_operators()]

    @property
    def operators(self) -> List[str]:
        """本班次进驻的干员（按房间顺序，去重保序）。"""
        return list(self._names)

    def facility_groups(self) -> List[Tuple[str, List[str]]]:
        """[(房间显示名, [干员名...]), ...]（只含进驻者，副手不计）。"""
        return [(f.display_name, [o.name for o in f.operators]) for f in self.world.facilities]


def shift_from_facilities(label: str, hours, facilities: List[dict], source: str = "",
                          entry_events=None, initial_global=None, detached=None,
                          idle_to_dorm=None) -> Shift:
    """由场景格式的 facilities 构建一个班次（`entry_events` 为可选进驻事件配置）。

    `idle_to_dorm`：顶层闲置入宿配置 —— 收**文件里的原始值**或已解析的
    `IdleToDormConfig`（解析在 `Shift.__post_init__`，只此一处）。
    """
    return Shift(label=label, hours=to_decimal(hours), facilities=list(facilities),
                 source=source, entry_events=entry_events,
                 initial_global=dict(initial_global or {}),
                 idle_to_dorm=idle_to_dorm,
                 detached=list(detached or []))


def shifts_from_import(imp, source: str = "") -> List[Shift]:
    """把 `importer.ImportResult` 的一个文件转成班次列表。

    - 每班的 `entry_events`（换心情）：沿用该文件解析出来的配置（挂在每个班次上）；
    - `idle_to_dorm`（闲置入宿）：**文件顶层那一份**（`imp.idle_to_dorm`，原样收下，
      由 `Shift.__post_init__` 按唯一口径解析）—— 不写这个键 ⇒ `None` ⇒ 默认开；
    - `initial_global`（变量初始值）：挂到每个班次（同一份排班共用）；
    - `detached`（不在基建的人）：同样挂到每个班次（同一份排班共用）。
    """
    cfg = build_entry_event_config(imp.entry_events) if imp.entry_events else None
    return [Shift(label=s.label, hours=s.hours if s.hours is not None else ZERO,
                  facilities=list(s.facilities), source=source or imp.report.source,
                  entry_events=cfg, initial_global=dict(imp.initial_global or {}),
                  idle_to_dorm=getattr(imp, "idle_to_dorm", None),
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
    """读一个本工具的场景 JSON（`{"facilities": [...]}`）→ 一个班次。

    顶层 `entry_events` / `idle_to_dorm` 都挂到这个班次上（解析口径只有一处，见 `Shift`）。
    """
    p = Path(path)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if "facilities" not in data:
        raise ValueError(f"{p.name} 既不是 MAA 排班（无 plans）也不是场景文件（无 facilities）")
    label = str(data.get("_source_plan") or p.stem)
    h = to_decimal(hours[0]) if hours else ZERO
    return [Shift(label=label, hours=h, facilities=data["facilities"], source=p.name,
                  entry_events=build_entry_event_config(data.get("entry_events")),
                  idle_to_dorm=data.get("idle_to_dorm"))]


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

    **顶层全局设置存在哪**（改这块前先看这条链路，2026-10 修过一次静默丢失）：

    | 设置 | 权威位置 | 传给 `world` |
    |---|---|---|
    | `entry_events`（换心情） | `Shift.entry_events` | `Shift.__post_init__` 透传 |
    | `idle_to_dorm`（闲置入宿） | `Shift.idle_to_dorm` | `Shift.__post_init__` 解析后写入 |
    | `initial_global` / `detached` | `Shift.*` | 经 `build_base_layout` 的 data |
    | `start_clock` / `cycle_hours` | `Schedule.*` | —（只改显示口径） |

    ⚠️ `Shift` 是**可重建**的（`with_hours` / `replaced_shift` / `with_detached` /
    `with_start_clock`），而 `world` 每次都从 `facilities` 现搭 ⇒ **全局设置必须挂在
    `Shift` 上并被重建路径原样搬运**；只挂在 `world` 上的那些会在重建时静默退回默认值
    （`Session` 随后从 `world` 读回，于是"碰一下名单，面板上的闲置入宿配置被打回 5/[]"）。
    """
    shifts: List[Shift]
    cycle_hours: Decimal = DEFAULT_CYCLE_HOURS
    start_clock: Decimal = ZERO
    detached: List[str] = field(default_factory=list)
    #: **导入时出现过的干员名册**（2026-10 新增，见 `operator_names()` 与 A1 工单）。
    #: `None` = 还没冻结过 ⇒ `__post_init__` 按"当前各班的进驻者 ∪ 名单"算一份。
    #: 冻结之后**不随布局编辑变化**：被人顶掉 / 被清空 / 被缩容挤掉的人**仍留在名册里**，
    #: 于是她照旧在实时心情表（`operator_names()`）里 —— M15a 的判据是
    #: "**心情表里有、这一班的 world 里没有 ⇒ 算存在**"（`rules._entry_trigger_ops`），
    #: 前提就是她**得在表里**。只靠"当前布局里还有没有人"算名册时，单班排班下她两边
    #: 都不在 ⇒ 换心情**静默 0 事件**（用户 2026-10 报的静默失效的残余一半）。
    #: ⚠️ 她**不进 `facilities`、不参与任何技能计数、不改变布局**（`names` 与 `world` 分离）。
    #: ⚠️ **必须被四条重建路径原样搬运**（`with_hours` / `replaced_shift` /
    #: `with_detached` / `with_start_clock`）—— 与 `entry_events` / `idle_to_dorm` 同一个坑。
    roster: Optional[List[str]] = field(default=None, repr=False, compare=False)
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
        # —— 导入名册：没冻结过就按"当前各班的进驻者 ∪ 名单"算一份（去重保序）——
        # ⚠️ 只在 `roster is None` 时算：一旦冻结，后面的布局编辑（顶人 / 清空 / 缩容）
        #    都不许改变它 —— 那正是"她还在心情表里"的依据。
        if self.roster is None:
            self.roster = _merged_names(self.detached, *(s.operators for s in self.shifts))
        else:
            self.roster = [str(n) for n in self.roster]
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

        ⚠️ **并上 `roster`（导入时出现过的名册）**（2026-10，A1 工单）：光靠"当前布局里
        还有谁"是不够的 —— 被人顶掉 / 被清空 / 被缩容挤掉之后，她就**两边都不在**
        （不在任何设施、也不在显式名单），于是连"心情表"里都没有她 ⇒ M15a 的
        "**表里有、world 里没有 ⇒ 算存在**"这条判据根本轮不到她 ⇒ 换心情**静默 0 事件**。
        并上名册之后：她仍在表里、仍是一条平线、**仍不进 `facilities`、不参与任何技能计数**，
        直接走现有那条分支。顺序＝名单 → 名册 → 各班进驻者（与旧口径向后兼容：
        旧口径就是"名单 → 各班进驻者"）。
        """
        return _merged_names(self.detached or (), self.roster or (),
                             *(s.operators for s in self.shifts))

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
                     idle_to_dorm=s.idle_to_dorm,
                     initial_global=dict(getattr(s, "initial_global", {}) or {}),
                     detached=list(getattr(s, "detached", []) or []))
               for s, h, name in zip(self.shifts, new_hours, names)]
        return Schedule(new, sum((s.hours for s in new), ZERO), self.start_clock,
                        list(self.detached), roster=list(self.roster or []))

    def with_start_clock(self, clock) -> "Schedule":
        """改「周期起点钟点」（纯显示口径，返回新的 Schedule）。"""
        return Schedule(copy.deepcopy(self.shifts), self.cycle_hours, to_decimal(clock),
                        list(self.detached), roster=list(self.roster or []))

    def with_roster(self, names: Sequence[str]) -> "Schedule":
        """**冻结导入名册**（`operator_names()` 会并上它；返回新的 Schedule）。

        只在导入装配那一步调一次（`Session._sync_from_schedule(from_import=True)`）——
        传进来的就是"**导入时这份排班里出现过的全部干员**"。之后任何布局编辑都**不再动它**
        （她被人顶掉之后照旧留在名册里，见 `operator_names()` 的说明）。

        ⚠️ 传 `[]` 是**权威值**：`roster=[]` 会被 `__post_init__` 原样收下（不再按当前
        布局重算）—— 否则"冻结成空"会被无声地撤销。
        """
        return Schedule(copy.deepcopy(self.shifts), self.cycle_hours, self.start_clock,
                        list(self.detached), roster=[str(n) for n in (names or [])])

    def replaced_shift(self, index: int, facilities: List[dict]) -> "Schedule":
        """替换某个班次的布局（返回新的 Schedule）。"""
        new = [Shift(label=s.label, hours=s.hours,
                     facilities=(list(facilities) if i == index else copy.deepcopy(s.facilities)),
                     source=s.source, entry_events=s.entry_events,
                     idle_to_dorm=s.idle_to_dorm,
                     initial_global=dict(getattr(s, "initial_global", {}) or {}),
                     detached=list(getattr(s, "detached", []) or []))
               for i, s in enumerate(self.shifts)]
        return Schedule(new, self.cycle_hours, self.start_clock, list(self.detached),
                        roster=list(self.roster or []))

    def with_detached(self, names: Sequence[str]) -> "Schedule":
        """改「不在基建」名单（返回新的 Schedule；各班的副本同步更新）。

        ⚠️ 传进去的名单是**权威值**（`[]` 就是清空）：`__post_init__` 默认会把各班
        Shift 上的旧名单并回来，所以这里要 `_detached_authoritative=True` ——
        否则"清空名单"会被无声地撤销（踩过）。
        ⚠️ 重建时会**原样搬运**每班的全局设置（`entry_events` / `idle_to_dorm` /
        `initial_global`）与**导入名册**（`roster`）——漏搬任何一项都会让"碰名单"顺手
        打回面板上的设置、或让被顶掉的人从心情表里再次消失。
        """
        new = [Shift(label=s.label, hours=s.hours, facilities=copy.deepcopy(s.facilities),
                     source=s.source, entry_events=s.entry_events,
                     idle_to_dorm=s.idle_to_dorm,
                     initial_global=dict(getattr(s, "initial_global", {}) or {}),
                     detached=[])
               for s in self.shifts]
        return Schedule(new, self.cycle_hours, self.start_clock,
                        [str(n) for n in (names or [])], _detached_authoritative=True,
                        roster=list(self.roster or []))

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
    #: 装配之后才发现的问题（不在任何一份文件的报告里，例如"名单里的人却占着位置"这类
    #: **跨字段冲突**：它是 `Session` 在 `_sync_from_schedule` 里解决掉的，不属于某一份文件）。
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        """一行导入摘要（状态栏用）。"""
        parts = [r.summary() for r in self.reports]
        parts.extend(str(n) for n in self.notes)
        return "；".join(p for p in parts if p)


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

    `idle_notes`：**引擎在每个执行点上实际把谁安排到了哪**（`{段起点: {候选名: 说明}}`），
    供逐次表的「说明」列只读显示（"进了宿舍#2 第 1 位" / "宿舍全满、没有合适的可换对象"）。
    ⚠️ 它是**布局事件文字**的一种索引，与 `marks` 同源（`Mark.kind == "idle"` 那些行）。
    """
    names: List[str]
    times: List[Decimal]
    moods: Dict[str, List[Decimal]]
    schedule: Schedule
    cycles: int = 1
    marks: List[Mark] = field(default_factory=list)
    segments: List[Tuple[Decimal, Decimal, "BaseLayout"]] = field(default_factory=list)
    idle_states: Dict[Decimal, Dict[str, dict]] = field(default_factory=dict)
    #: `{段起点: {候选名: 说明}}` —— 引擎在每个执行点上**实际把谁安排到了哪**（逐次表只读说明）
    idle_notes: Dict[Decimal, Dict[str, str]] = field(default_factory=dict)

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

    def idle_note_at(self, name: str, t) -> Optional[str]:
        """某一刻引擎**实际把某位候选安排到了哪**（逐次表「说明」列的只读来源）。

        取"这个时刻所在**段**"的那份索引（键是段起点，与 `idle_state_at` 同一口径）。
        取不到（没开闲置入宿 / 她不是候选）返回 `None`。
        """
        seg = self._segment(t)
        if seg is None:
            return None
        note = self.idle_notes.get(seg[0], {}).get(name)
        if not note:
            return None
        # 事件文字自带一层全角括号（`（…）`）；逐次表的「说明」列自己不加括号 ⇒ 这里剥掉
        return note[1:-1] if note.startswith("（") and note.endswith("）") else note

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

    ⚠️ 走 `mood_soc.net_rates`（**一次收一份变量快照**给这批人共用）—— 原先每人各收一遍，
    实测占整轮重算 ~15%（`collect_variables` 一次 ≈15µs × 47 人 × 每次重算速率）。
    """
    return net_rates(world, names)


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
    就全对——**步长不该影响结果**，所以那是 bug 不是口径。见 `03-特殊机制.md` 第 35 条。
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


# ============================================================================
# 换班执行点（文档《闲置入宿完整逻辑》§2/§3）
#
# 一个**真实班次**不止"班初"一个执行点：班长 > 12h 时，班内每个**严格小于班末**的 12h
# 整数倍都是一个**内部换班执行点**（那里的位置从该班原始布局重建、进驻事件与闲置入宿重跑）。
# 口径只有这一处，`simulate_schedule` 切段、`Session` 的逐段指纹与「闲置入宿」逐次表都读它。
# ============================================================================
#: 内部换班的间隔（小时）—— 文档 §2 写死 12h。
INTERNAL_SWAP_HOURS = Decimal("12")


def execution_offsets(shift_hours) -> List[Decimal]:
    """一个班次的**换班执行点**相对班初的偏移（小时）→ `[0, 12, 24, …]`（文档 §2）。

    ```
    12h 班次：[0]               （12 不严格小于 12 ⇒ 没有内部换班）
    18h 班次：[0, 12]
    24h 班次：[0, 12]           （第 24h 是班末，不重复执行）
    25h 班次：[0, 12, 24]
    36h 班次：[0, 12, 24]       （第 36h 是班末）
    ```

    每个周期再次运行同一个长班时，都**重新按该班时长**生成（纯函数，无需缓存）。
    """
    hours = to_decimal(shift_hours)
    out: List[Decimal] = [ZERO]
    offset = INTERNAL_SWAP_HOURS
    while offset < hours:
        out.append(offset)
        offset = offset + INTERNAL_SWAP_HOURS
    return out


def execution_points(schedule: Schedule, cycles: int = 1) -> List[Tuple[Decimal, Decimal, int, int, Decimal]]:
    """整条时间轴的**换班执行点** → `[(起点, 段止, 班次下标 0 基, 周期序号 1 基, 班内偏移), …]`。

    - `起点`/`段止` 是**绝对小时**（段止 = 下一个执行点，或周期末尾由调用方截断）；
    - **内部换班不增加班次数**：`班次下标`/`周期序号` 与"真实班次"完全一致（文档不变式 4），
      所以逐人配置（按 `(周期, 班次)` 取）在同班的各执行点之间**天然共用**（不变式 9）；
    - 最后一个执行点的 `段止` 允许越过 `cycles × cycle_hours`（截断由调用方做）。
    """
    if cycles < 1:
        raise ValueError("cycles 至少为 1")
    points: List[Tuple[Decimal, Decimal, int, int, Decimal]] = []
    for k in range(int(cycles)):
        base = schedule.cycle_hours * k
        for i, s in enumerate(schedule.shifts):
            offsets = execution_offsets(s.hours)
            for j, offset in enumerate(offsets):
                t0 = base + schedule.starts[i] + offset
                nxt = offsets[j + 1] if j + 1 < len(offsets) else s.hours
                points.append((t0, base + schedule.starts[i] + nxt, i, k + 1, offset))
    return points


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
                      idle_protected_slots: Optional[int] = None,
                      idle_blacklist: Optional[Sequence[str]] = None,
                      mood_events: Optional[Sequence[MoodSetEvent]] = None,
                      max_segment: Decimal = MAX_SEGMENT_HOURS,
                      continue_from: Optional[Tuple["Trajectory", int]] = None) -> Trajectory:
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
        entry_when       **什么时候换**：`"immediate"`（默认；她满 24 就换）/
                        `"wait"`（到点没满就等她回满再换）/ `"full"`（只在她满心情时换）；
                        ⚠️ **三档都以"她满 24"为前提**（2026-10 用户裁决）：她没满时三档都不换；
                        `None` = 用场景 JSON
        entry_per_shift  **按班次覆盖**（`[EntryShiftOverride, ...]`）——
                       3 班排班就可以"第 1 班换给巫恋、第 2 班自动挑最累的、第 3 班不用"；
                       `None` = 用场景 JSON 里的 `per_shift`
        idle_to_dorm  **闲置入宿**：每个**换班执行点**（每个真实班次的班初 ＋ 长班的每个内部
                       换班点）把"这一班完全没有出现在任何设施里、心情还没满"的干员安排进宿舍
                       （有连续空位就直接住进去，全满了才与宿舍里心情更高的那位互换）。
                       口径＝用户文档《闲置入宿完整逻辑》，见
                       `mood_soc.rules.apply_idle_to_dorm`。
                       ⚠️ **默认 `True`（开）**（用户口径"闲置入宿默认是开启的"）；
                       要"完全不动布局"的旧口径就显式传 `False`。
                       ⚠️ **不设班次数量门槛**（第二版 §2）：单班排班照样执行。
        idle_entries  ⚠️ **已撤（2026-10）**：原为"界面的逐人「参不参与」设置
                       （`[IdleToDormEntry, ...]`），给了就盖过 JSON 里的
                       `idle_to_dorm.per_operator`"。那个设置整条不存在了（类已删），
                       形参也一并删掉 —— 别再加回来。
        idle_protected_slots  **锁定位置数**（文档 §5）：给了就盖过 JSON 的
                       `idle_to_dorm.protected_slots`（默认 5）；`None` = 用 JSON。
        idle_blacklist **黑名单**（文档 §6）：给了就盖过 JSON 的 `idle_to_dorm.blacklist`；
                       `None` = 用 JSON（`[]` = 清空名单）。
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

    实现说明：**每个换班执行点（每个真实班次的班初 ＋ 长班的每个内部换班点）都从一份
    "未动过的计划副本"重建**当前布局（`pristine` —— 见段循环里的说明）——这样
    `entry_restore_back=False`（位置也互换）与闲置入宿的换人**只在这一次生效**，
    既不污染排班本身、也不会跨执行点/跨周期继承；同时把"这一班的有效配置"挂到副本上，
    `apply_entry_events` 直接读它。
    ⚠️ **长班的内部换班点**（`execution_offsets`：班内每个**严格小于班末**的 12h 整数倍）也算执行点：
    那里照旧重建位置、重跑进驻事件与闲置入宿，并记一条 `internal` 标记（文档 §3/不变式 10）；
    **它不增加班次数、不改 `shift_index`、也不给逐人配置新的序号**（同班各执行点共用一份）。
    ```
    ⚠️ **`continue_from`（P6a/P6b 增量）**：`(上一份轨迹, 它已经算完的**段数**)` —— 只算尾部。
    段＝一个**换班执行点**（`segments` 里的第几段，0 基）；段边界是班初、内部换班点与周期边界。
      · 段数 < 总段数 ⇒ 从第 `已算完段数` 段继续算（前面全部复用）；
      · 段数 ≥ 总段数 ⇒ 等于"只截断"（一段都不算），用于"周期数调小"。
    调用方（`store.session.Session.recompute_inputs`）负责判断"能不能复用、从哪一段起"：
    它按**逐段输入指纹**找出"第一个不一样的段"（见那里的说明）。
    种子取"**段边界上第一个节点**"的值＝**段首事件之前**的心情（段首的进驻事件会在续算时
    再跑一遍；用 `mood_at(t0)` 会拿到跳变**后**的值 ⇒ 进驻事件被算两次）。
    实现上是把前缀的 `times/series/marks/seg_worlds/idle_states` 预填进累加器、
    段循环从 `start_seg` 起跑 —— 于是产出的轨迹与"从头全量算"**逐位相同**（回归
    `Test增量重算`）。
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
        #    "只在她满心情时换 / 等她回满再换" 覆盖成 `immediate`（满 24 就换、双方都 24 也换）。
        when=(normalize_entry_when(when) or "immediate"),
    )
    #: 每个班次一份**"从未被动过"的计划副本** —— 引擎每段（含每个周期）都从它**重建**当前布局，
    #: 见下面段循环里的说明（副本绝不能被就地改着复用）。
    pristine = [copy.deepcopy(s.world) for s in schedule.shifts]
    # 闲置入宿：界面 / 调用方传了锁定位置数 / 黑名单就盖过 JSON 的（"调用方口径优先"，与进驻事件同一约定）
    # ⚠️ 原先这里还有"逐人「参不参与」"（`idle_entries`）那一项，2026-10 随功能整条撤销。
    if idle_to_dorm:
        needs_override = (idle_protected_slots is not None or idle_blacklist is not None)
        if needs_override:
            for w in pristine:
                base_idle = getattr(w, "idle_to_dorm", None) or IdleToDormConfig()
                w.idle_to_dorm = IdleToDormConfig(
                    enabled=True,
                    protected_slots=(int(base_idle.protected_slots)
                                     if idle_protected_slots is None
                                     else int(idle_protected_slots)),
                    blacklist=(list(base_idle.blacklist) if idle_blacklist is None
                               else [str(n) for n in idle_blacklist]))
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
    seg_worlds: List[BaseLayout] = []
    idle_notes: Dict[Decimal, Dict[str, str]] = {}
    idle_states: Dict[Decimal, Dict[str, dict]] = {}

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

    # 覆盖 [0, total] 的**换班执行点**段：每个真实班次的班初 + 长班的每个内部换班点（文档 §2）。
    # 段元组＝(起点, 段止, 班次下标, 周期序号 1 基, 班内偏移)。内部换班**不增加班次数**，
    # 班次下标与周期序号照旧指向那个真实班次 ⇒ 逐人配置在同班各执行点之间共用（不变式 4/9）。
    segments: List[Tuple[Decimal, Decimal, int, int, Decimal]] = []
    for (t0, nxt, idx, cyc, offset) in execution_points(schedule, cycles):
        if t0 >= total:
            break
        segments.append((t0, min(nxt, total), idx, cyc, offset))

    # —— P6a/P6b：从"上一个轨迹的某一**段边界**"续算（累加器用前缀预填）——
    start_seg = 0
    if continue_from is not None:
        prev, done_segs = continue_from
        if done_segs < 0:
            raise ValueError(f"continue_from 的已算段数 {done_segs} 不合法")
        # 段数比"新时间轴的段数"还多 = 周期数调小 ⇒ 只截断（切点取新时间轴的长度）
        cut_seg = min(done_segs, len(segments))
        start_seg = cut_seg
        t_cut = segments[cut_seg][0] if cut_seg < len(segments) else total
        if cut_seg:
            # 种子＝段边界上**第一个**节点（段首事件之前的值）
            i = bisect.bisect_left(prev.times, t_cut)
            if i >= len(prev.times) or prev.times[i] != t_cut:
                raise ValueError("continue_from 的轨迹没有落在该段边界上（时间轴变了？请整条重算）")
            moods = {n: prev.moods[n][i] for n in names}
            times = list(prev.times[:i + 1])
            series = {n: list(prev.moods[n][:i + 1]) for n in names}
            # 标记：**红脸标记不复用**（末尾统一按合并后的曲线重算），其余只留切点之前的
            marks = [m for m in prev.marks if m.t < t_cut and m.kind != "redface"]
            seg_worlds = [w for (a, _b, w) in prev.segments if a < t_cut]
            idle_states = {t: v for t, v in prev.idle_states.items() if t < t_cut}
            idle_notes = {t: v for t, v in prev.idle_notes.items() if t < t_cut}
        else:
            times = [ZERO]
            series = {n: [moods[n]] for n in names}
            marks = []
    # 班次标记**排在最前面**（与"从头全量算"的顺序一致：全量时它也是最先加进去的）。
    # ⚠️ 它是 `schedule` 的**纯函数**（时刻＝各班起点、标签＝各班标签）⇒ 一律**重新生成**：
    #    续算时前缀里那份是"旧时间轴的班次标记"（周期数改小后还多出几段），
    #    照搬就会出现"第 5 班的标记还在、第 6 班的没了"，与全量的标记序列对不上。
    marks = [m for m in marks if m.kind != "shift"]
    marks = [Mark(schedule.starts[i], "shift", s.label)
             for i, s in enumerate(schedule.shifts)] + marks

    for seg_i, (t0, seg_end, idx, cycle_no, offset) in enumerate(segments):
        if seg_i < start_seg:
            continue                        # P6a：这一段（含它的产物）由 `continue_from` 的前缀带着
        # —— 内部换班标记（文档 §3/不变式 10）：**每个内部换班都显示**，哪怕这一次没有换人、
        #    心情也没变（"可见事件标记"是口径的一部分，别只在有事发生时才记）。
        if offset > 0:
            marks.append(Mark(t0, "internal", f"第 {idx + 1} 班 · {_fmt_value(offset)}h 内部换班"))
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
            # ⚠️ `detached_moods=moods`：**「不在基建」名单里的人心情只在这张表里**
            #    （她不在 `world` 里）—— 2026-10 起 M15a 把她也算"存在"，不传就当她不在场
            #    （用户裁决的**有意例外**，见 `mood_soc/rules.apply_entry_events`）。
            events = apply_entry_events(world, enabled=True, detached_moods=moods)
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

        # —— 闲置入宿（可选）：把"这一班完全没出现在任何设施里、心情还没满"的干员安排进宿舍 ——
        # 顺序上**排在进驻事件之后**：换心情是"进驻那一刻"的游戏事件，闲置入宿是人工调度。
        # ⚠️ 本班未排班的干员不在 `world` 里（心情在 `moods` 字典里），所以要把他们的心情传进去。
        # ⚠️ **不设班次数量门槛**（文档 §2 第二版）：单班排班照样执行。
        # ⚠️ `scope` 用**真实班次**的 (周期, 班次)，不是"第几段"——内部换班点与班初共用一份
        #    逐人配置（文档 §8/§14、不变式 9）。
        if idle_to_dorm:
            _sync_moods(world, moods)
            # ⚠️ **候选判定看"原始布局"，候选心情看"执行点实时心情"** —— 两件事分开：
            #    · 存在性用 `pristine[idx]`（这一班**导入时**的那份布局），**不是** `world`：
            #      `world` 此刻已经跑过进驻事件（位置互换 / `restore_back=False`），
            #      将来若再加"会改变成员"的布局事件，用 `world` 判就会把"被事件挪出设施的人"
            #      误判成新候选（文档 §7 第 3 条：必须"在该班次原始布局中完全未出现在任何设施"）。
            #    · 心情一律取 `moods`（跨班/跨周期连续、内部换班点用当时的实际值、
            #      心情指定事件可能已经改过它）—— 别从原始布局里的干员对象读 `op.mood`。
            original_world = pristine[idx]
            idle_moods = {n: moods[n] for n in names
                          if original_world.get_operator(n) is None}
            # 逐位候选各留一份"轮到她的那一刻"的宿舍态 → 界面逐次表逐行取用
            # （面板不能拿班末那份 `world_at` 或排班快照当"她那一刻的世界"）。
            per_cand: Dict[str, dict] = {}
            per_note: Dict[str, str] = {}
            for ev in apply_idle_to_dorm(world, enabled=True, idle=idle_moods,
                                         scope=(cycle_no, idx + 1), trace=per_cand):
                marks.append(Mark(t0, "idle", ev.detail))
                if ev.owner:
                    per_note[ev.owner] = ev.detail
            idle_states[t0] = per_cand
            if per_note:
                idle_notes[t0] = per_note
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
            # ⚠️ **不在换班执行点那一刻抢跑**（文档 §3 第 1 步 + 不变式 8）：`t >= seg_end` 说明
            #    这一刻正是下一个换班执行点（内部换班或真实班初），那一段会**丢弃**这段的等待状态、
            #    从原始布局重建，再按重建后的布局重新判断。所以这里只处理"段内"到点的情况。
            if pending and t < seg_end and any(moods.get(h, ZERO) >= MOOD_MAX for h in pending):
                pending = []
                _sync_moods(world, moods)
                events = apply_entry_events(world, enabled=True, detached_moods=moods)
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
                      segments=[(a, b, w) for (a, b, *_rest), w in zip(segments, seg_worlds)],
                      idle_states=idle_states, idle_notes=idle_notes)
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
    "INTERNAL_SWAP_HOURS",
    "Shift", "Schedule", "Trajectory", "Mark", "LoadedSchedule", "MoodSetEvent",
    "shift_from_facilities", "shifts_from_maa_file", "shifts_from_scenario_file",
    "shifts_from_import", "shift_file_kind", "load_schedule", "load_schedule_ex",
    "load_schedule_from_imports",
    "simulate_schedule", "compute_rates",
    "execution_offsets", "execution_points",
    "rates_in_world", "default_initial_moods", "all_operator_names",
]
