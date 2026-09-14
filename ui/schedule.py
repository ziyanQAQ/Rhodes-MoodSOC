"""ui/schedule.py —— 多班排班模型 + 「整周期心情轨迹」（图形界面的计算核心，**不依赖任何 GUI**）。

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
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from mood_soc import apply_entry_events, build_base_layout, compute_net_rate
from mood_soc.battery import ZERO, to_decimal
from mood_soc.config import MOOD_MAX, MOOD_MIN
from mood_soc.maa import read_maa
from mood_soc.models import BaseLayout
from mood_soc.skills import DEFAULT_OPERATORS

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
    """一个班次：一段时长 + 一份布局。"""
    label: str
    hours: Decimal
    facilities: List[dict]
    source: str = ""
    world: BaseLayout = field(init=False, repr=False)

    def __post_init__(self):
        self.hours = to_decimal(self.hours)
        if self.hours < ZERO:
            raise ValueError(f"班次「{self.label}」时长不能为负，收到 {self.hours}")
        # 注：hours == 0 表示"时长未知"（MAA 班次名里没带 h 时），
        # 由 `_hours_from_hints` 补齐、并由 `Schedule` 校验必须为正。
        self.world = build_base_layout({"facilities": self.facilities})
        self._names = [o.name for o in self.world.all_operators()]

    @property
    def operators(self) -> List[str]:
        """本班次进驻的干员（按房间顺序，去重保序）。"""
        return list(self._names)

    def facility_groups(self) -> List[Tuple[str, List[str]]]:
        """[(房间显示名, [干员名...]), ...]（只含进驻者，副手不计）。"""
        return [(f.display_name, [o.name for o in f.operators]) for f in self.world.facilities]


def shift_from_facilities(label: str, hours, facilities: List[dict], source: str = "") -> Shift:
    """由场景格式的 facilities 构建一个班次。"""
    return Shift(label=label, hours=to_decimal(hours), facilities=list(facilities), source=source)


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
    return [Shift(label=label, hours=h, facilities=data["facilities"], source=p.name)]


def shift_file_kind(path: Union[str, Path]) -> str:
    """判断文件类型：'maa' / 'scenario'。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get("plans"), list) and data["plans"]:
        return "maa"
    if isinstance(data, dict) and isinstance(data.get("facilities"), list):
        return "scenario"
    raise ValueError(f"无法识别的排班文件：{Path(path).name}"
                     f"（既没有 plans 也没有 facilities）")


@dataclass
class Schedule:
    """一个周期内的若干班次（不变式：**各班长之和 == 周期时长**）。"""
    shifts: List[Shift]
    cycle_hours: Decimal = DEFAULT_CYCLE_HOURS

    def __post_init__(self):
        if not self.shifts:
            raise ValueError("排班至少要有一个班次")
        self.cycle_hours = to_decimal(self.cycle_hours)
        for s in self.shifts:
            if s.hours <= ZERO:
                raise ValueError(f"班次「{s.label}」的时长还没确定（为 0）")
        total = self.total_hours
        if total != self.cycle_hours:
            raise ValueError(f"各班长之和 {total}h ≠ 周期 {self.cycle_hours}h"
                             f"（改班次时长或改周期，两者必须相等）")
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
        """周期内出现过的全部干员（按班次顺序去重保序）。"""
        seen, out = set(), []
        for s in self.shifts:
            for n in s.operators:
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        return out

    def hour_labels(self) -> List[str]:
        """各班次的"起始小时"标签（如 ['0:00', '12:00', '18:00']）。"""
        return [f"{int(t)}:{int((t - int(t)) * 60):02d}" for t in self._starts]

    # ------------------------------------------------------------------ 编辑
    def with_hours(self, hours: Sequence) -> "Schedule":
        """改班次时长（返回新的 Schedule；周期同步为新旧之和，故必然自洽）。"""
        if len(hours) != len(self.shifts):
            raise ValueError(f"需要 {len(self.shifts)} 个时长，收到 {len(hours)} 个")
        new = [Shift(label=s.label, hours=to_decimal(h),
                     facilities=copy.deepcopy(s.facilities), source=s.source)
               for s, h in zip(self.shifts, hours)]
        return Schedule(new, sum((s.hours for s in new), ZERO))

    def replaced_shift(self, index: int, facilities: List[dict]) -> "Schedule":
        """替换某个班次的布局（返回新的 Schedule）。"""
        new = [Shift(label=s.label, hours=s.hours,
                     facilities=(list(facilities) if i == index else copy.deepcopy(s.facilities)),
                     source=s.source)
               for i, s in enumerate(self.shifts)]
        return Schedule(new, self.cycle_hours)


# ============================================================================
# 装配：从若干文件到一个排班
# ============================================================================
def load_schedule(paths: Sequence[Union[str, Path]],
                  cycle_hours: Optional[Decimal] = None,
                  hours: Optional[Sequence] = None) -> Schedule:
    """从若干排班文件装配一个 `Schedule`。

    - 一个 MAA 文件可能含多个 plan ⇒ 每个 plan 一个班次（按文件顺序、plan 顺序）；
      **一个班次一个文件**（用户的 12h/6h/6h 三个文件）是最常见用法。
    - 每班时长：显式 `hours` > 班次名提示 > 均分。
    - 周期：默认 = 各班时长之和（自洽）；显式给 `cycle_hours` 时必须与之和相等。
    """
    shifts: List[Shift] = []
    for p in paths:
        kind = shift_file_kind(p)
        if kind == "maa":
            shifts.extend(shifts_from_maa_file(p))
        else:
            shifts.extend(shifts_from_scenario_file(p))
    if not shifts:
        raise ValueError("没有解析出任何班次")
    total = _hours_from_hints(shifts, cycle_hours)
    if cycle_hours is not None and to_decimal(cycle_hours) != total:
        raise ValueError(f"各班时长之和 {total}h ≠ 指定周期 {to_decimal(cycle_hours)}h")
    return Schedule(shifts, total)


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
    """整周期的分段线性心情轨迹（节点＝事件时刻，节点之间线性）。"""
    names: List[str]
    times: List[Decimal]
    moods: Dict[str, List[Decimal]]
    schedule: Schedule
    cycles: int = 1
    marks: List[Mark] = field(default_factory=list)

    # ------------------------------------------------------------------ 查询
    @property
    def total_hours(self) -> Decimal:
        return self.times[-1]

    def mood_at(self, name: str, t) -> Decimal:
        """时刻 t 的心情（节点之间线性插值；超界取端点值）。"""
        vals = self.moods[name]
        t = to_decimal(t)
        ts = self.times
        if t <= ts[0]:
            return vals[0]
        if t >= ts[-1]:
            return vals[-1]
        i = bisect.bisect_right(ts, t)
        t0, t1 = ts[i - 1], ts[i]
        v0, v1 = vals[i - 1], vals[i]
        if t1 == t0:
            return v1
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)

    def moods_at(self, t) -> Dict[str, Decimal]:
        """某时刻所有干员的心情（界面"时间滑动"用的就是这个）。"""
        return {n: self.mood_at(n, t) for n in self.names}

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


def compute_rates(schedule: Schedule, index: int, moods: Dict[str, Decimal],
                  names: Sequence[str]) -> Dict[str, Decimal]:
    """某班次下、给定心情快照时的净速率（未排班者 = 0）。"""
    shift = schedule.shifts[index]
    rates: Dict[str, Decimal] = {}
    for n in names:
        rates[n] = compute_net_rate(shift.world, n) if shift.world.get_operator(n) is not None else ZERO
    return rates


def _next_event(schedule: Schedule, moods: Dict[str, Decimal], rates: Dict[str, Decimal],
                t: Decimal, seg_end: Decimal, groups: Sequence[Sequence[str]]):
    """求下一个会让速率改变的时刻。

    返回 `(时刻, 吸附表)`：吸附表 `{干员: 阈值}` 表示"这个人的心情正好在本段末尾
    跨过该阈值"，积分后应把它**精确置为阈值**——否则 `dt = (m-h)/r` 的非终止小数会让
    结果带上 `16.19999999999999999999999999` 这样的尾巴（数值卫生，不是精确性问题）。
    """
    nxt = seg_end
    snaps: Dict[str, Decimal] = {}
    # ① 心情触到阈值（含 0 与 24）
    for name, r in rates.items():
        if r == ZERO:
            continue
        m = moods[name]
        for h in EVENT_THRESHOLDS:
            if (r > ZERO and m > h) or (r < ZERO and m < h):
                dt = (m - h) / r
                if MIN_EVENT_GAP <= dt < nxt - t:
                    nxt = t + dt
                    snaps = {name: h}
                elif dt < MIN_EVENT_GAP and snaps:
                    snaps[name] = h
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
    return nxt, snaps


def default_initial_moods(schedule: Schedule) -> Dict[str, Decimal]:
    """周期起点心情的缺省值：**第一班布局里写的值**（没有则满心情 24）。

    为什么不是一律 24：场景/排班文件可以带起始心情（`{"name":"x","mood":10}`），
    直接导入时那个值就是"这个周期的起点"。界面上手动设的心情也走同一入口。
    """
    base = {n: MOOD_MAX for n in schedule.operator_names()}
    if schedule.shifts:
        for op in schedule.shifts[0].world.all_operators():
            if op.name in base:
                base[op.name] = to_decimal(op.mood)
    return base


def simulate_schedule(schedule: Schedule, cycles: int = 1,
                      initial_moods: Optional[Dict[str, Decimal]] = None,
                      entry_events: bool = False,
                      max_segment: Decimal = MAX_SEGMENT_HOURS) -> Trajectory:
    """把排班跑成"整周期心情轨迹"（事件驱动精确积分）。

    参数：
        cycles        跑几个周期（心情跨周期连续，用来看是否收敛）
        initial_moods 周期起点的心情；缺省取**第一班布局里写的值**（没有则 24）
        entry_events  是否在每班开始时结算进驻事件（M15a 患难之交；默认否）
        max_segment   单段最长时长（兜底安全上限）

    数值说明：单段内心情是精确的线性函数；误差只来自 Decimal 除法在 28 位有效数字处的
    舍入（量级 1e-26），界面上按"显示边界"舍入到 2 位小数即可。
    """
    if cycles < 1:
        raise ValueError("cycles 至少为 1")
    total = schedule.cycle_hours * Decimal(cycles)
    names = schedule.operator_names()
    start_moods = default_initial_moods(schedule)
    if initial_moods:
        start_moods.update({k: to_decimal(v) for k, v in initial_moods.items()})
    moods: Dict[str, Decimal] = {n: start_moods.get(n, MOOD_MAX) for n in names}

    times: List[Decimal] = [ZERO]
    series: Dict[str, List[Decimal]] = {n: [moods[n]] for n in names}
    marks: List[Mark] = []
    for i, s in enumerate(schedule.shifts):
        marks.append(Mark(schedule.starts[i], "shift", s.label))

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

    for seg_i, (t0, seg_end, idx) in enumerate(segments):
        shift = schedule.shifts[idx]
        # —— 进驻事件（可选）：进入班次那一刻结算一次（会就地改心情） ——
        # 这是 t0 处的**跳变**：t0 之前是换心情前的值，t0 起是换之后的（把节点就地改写，
        # 而不是再追加一个同刻节点——否则 mood_at(t0) 会取到跳变前的旧值）。
        if entry_events:
            w = copy.deepcopy(shift.world)
            for o in w.all_operators():
                o.mood = moods.get(o.name, MOOD_MAX)
            events = apply_entry_events(w)
            if events:
                for ev in events:
                    marks.append(Mark(t0, "entry", ev.detail))
                for o in w.all_operators():
                    if o.name in moods:
                        moods[o.name] = o.mood
                if times[-1] == t0:
                    for n in names:
                        series[n][-1] = moods[n]
                else:
                    times.append(t0)
                    for n in names:
                        series[n].append(moods[n])
        groups = [[o.name for o in f.operators] for f in shift.world.facilities]

        t = t0
        rates = None
        while t < seg_end:
            # 速率只在"可能变了"时重算：进新班次、发生了事件（跨阈值/两人交叉）、
            # 或走了兜底分支。纯"安全上限推进"时速率必然不变，直接复用（这是主要的提速点：
            # 重算一次全布局约 2.5ms，复用它能把 3 周期的重算从 ~1.9s 压到 ~0.2s）。
            if rates is None:
                rates = compute_rates(schedule, idx, moods, names)
            nxt, snaps = _next_event(schedule, moods, rates, t, seg_end, groups)
            event_fired = bool(snaps) or nxt < seg_end
            if nxt - t > max_segment:          # 兜底上限截断 → 本段没有真事件
                nxt, snaps, event_fired = t + max_segment, {}, False
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
            if event_fired:
                rates = None

    # 红脸区间记成标记（画图时画阴影）
    traj = Trajectory(names=names, times=times, moods=series, schedule=schedule,
                      cycles=cycles, marks=marks)
    for n in names:
        for a, b in traj.red_face_spans(n):
            marks.append(Mark(a, "redface", n))
    return traj


# ============================================================================
# 干员候选（界面上的"选人"下拉用）
# ============================================================================
_OPERATOR_CACHE: Optional[List[str]] = None


def all_operator_names(extra: Iterable[str] = ()) -> List[str]:
    """全部可选干员名：`resources/operators.txt`（全量 921 名）+ 内置心情技能表 + 指定补充。

    全量名册优先（它含只有生产/训练技能的干员）；读不到时退回内置表。
    """
    global _OPERATOR_CACHE
    if _OPERATOR_CACHE is None:
        names = set(DEFAULT_OPERATORS)
        path = Path(__file__).resolve().parent.parent / "resources" / "operators.txt"
        try:
            with open(path, "r", encoding="utf-8") as f:
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
    "Shift", "Schedule", "Trajectory", "Mark",
    "shift_from_facilities", "shifts_from_maa_file", "shifts_from_scenario_file",
    "shift_file_kind", "load_schedule", "simulate_schedule", "compute_rates",
    "default_initial_moods", "all_operator_names",
]
