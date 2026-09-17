"""store/session.py —— **会话状态**：图形界面与程序接口共用的一份"当前在算的东西"。

## 为什么要有它

在这之前，界面上的全部可调项（初始心情 / 心情指定事件 / 换心情 / 闲置入宿 / 周期数 /
时间轴）都是 `ui/app.py`（`MoodSocApp`）的实例属性，`recompute()` 也是它的方法。
于是"图形界面能做的一切"只有拉得起 tkinter 才做得到 —— 程序接口（`api/`，给 Rust 用）
只能重新实现一遍，两份状态一旦不一致就会出现"界面算出来 8.4、API 算出来 9.1"。

现在：**状态与重算在 `Session`，`ui/` 与 `api/` 都只是它的入口**。

```
Session（本模块）            ui/app.py（视图）              api/ops.py（程序接口）
  schedule / traj       ←── 读 ── 看板、曲线、滑块      ←── 读 ── moods / trajectory
  initial_moods 等策略  ←── 写 ── 设置中心四个分区       ←── 写 ── set_moods / set_idle_to_dorm
  recompute()           ←── 调 ── 每次改动               ←── 调 ── 同一套 op
```

## 口径（与图形界面完全一致，别在这里另立一套）

- 心情在周期起点取 `initial_moods`（缺省＝第一班布局里写的值，见 `default_initial_moods`）；
- `cycles` 是"把同一排班连跑几个周期"（心情接着上一周期末尾继续，用来判断能否永动）；
- 进驻事件（M15a）默认不结算，`entry_events=True` 时**每班开始时**结算一次；
- 闲置入宿排**在进驻事件之后**（顺序有语义，别调换）；
- 同刻跳变（事件 / 入宿 / 心情锚点）在轨迹上是**两个节点**（跳变前 / 跳变后），
  取值规则见 `Trajectory.mood_at`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from data.skills_data import DEFAULT_OPERATORS
from mood_soc.battery import to_decimal
from mood_soc.config import MOOD_MAX, FacilityType
from mood_soc.models import IdleToDormEntry, normalize_entry_when
from mood_soc.rules import mood_skill_summary

from .layout import build_base_layout
from .schedule import (MoodSetEvent, LoadedSchedule, Schedule, Shift, Trajectory,
                       default_initial_moods, load_schedule_ex, simulate_schedule)

ZERO = Decimal("0")


# ============================================================================
# 校验结果
# ============================================================================
@dataclass
class ValidationIssue:
    """一条布局自检问题（房间数超上限 / 建造位超 9 / 人数超等级容量 等）。"""
    shift: int          # 第几个班次（1 基）
    label: str          # 班次名
    message: str

    def to_dict(self) -> dict:
        return {"shift": self.shift, "shift_label": self.label, "message": self.message}


@dataclass
class Validation:
    """`Session.validate()` 的结果。"""
    ok: bool
    issues: List[ValidationIssue] = field(default_factory=list)

    def messages(self) -> List[str]:
        return [f"第 {i.shift} 班（{i.label}）：{i.message}" for i in self.issues]

    def text(self) -> str:
        """一行中文摘要（状态栏用；无问题返回空串）。"""
        return "；".join(self.messages())

    def to_dict(self) -> dict:
        return {"ok": self.ok, "issues": [i.to_dict() for i in self.issues]}


# ============================================================================
# 会话
# ============================================================================
@dataclass
class Session:
    """一次会话：一份排班 + 全部设置 + 算好的整周期轨迹。

    构造后请调 `recompute()` 一次（`load_paths()` 会自动调）。
    """

    # ---------------------------------------------------------------- 排班与结果
    schedule: Optional[Schedule] = None
    traj: Optional[Trajectory] = None
    #: 最后一次导入的附赠信息（干员池 / 变量初始值 / 导入报告）
    loaded: Optional[LoadedSchedule] = None

    # ---------------------------------------------------------------- 策略设置
    #: 周期数（1~3）：把同一排班连跑几个周期
    cycles: int = 1
    #: 周期起点心情（`{干员: 心情}`）；缺省＝第一班布局里写的值
    initial_moods: Dict[str, Decimal] = field(default_factory=dict)
    #: 「心情指定事件」锚点（`MoodSetEvent` 列表）
    mood_events: List[MoodSetEvent] = field(default_factory=list)

    #: 进驻事件（M15a）总开关
    entry_events: bool = False
    #: 与谁互换心情：None=同宿舍前一位进驻 / 人名 / "any"=全基建最累的
    entry_swap_with: Optional[str] = None
    #: 目标范围："dorm" 仅同宿舍 / "anywhere" 基建任意位置
    entry_scope: str = "dorm"
    #: 换完是否把位置也换回去（True=只换心情、两人留原位）
    entry_restore_back: bool = True
    #: 什么时候换："immediate" 立刻 / "wait" 等她回满 / "full" 只在她满心情时
    entry_when: str = "full"
    #: 按班次的覆盖（`EntryShiftOverride` 列表）
    entry_per_shift: List[object] = field(default_factory=list)

    #: 闲置入宿总开关
    idle_to_dorm: bool = False
    #: 不限班次/周期的逐人设置 `{干员: (参与, 目标标签)}`
    idle_globals: Dict[str, Tuple[bool, Optional[str]]] = field(default_factory=dict)
    #: 逐次设置 `{(周期, 班次, 干员): (参与, 目标标签)}`（序号均为 1 基）
    idle_entries: Dict[Tuple[int, int, str], Tuple[bool, Optional[str]]] = field(default_factory=dict)

    #: 「不在基建」名单（既不在工作设施、也不在宿舍的人；场景 JSON 顶层 `detached`）。
    #: 语义：轨迹里**一条平线**（心情恒定），且**不参与任何技能计数**。见 `store.schedule`。
    detached: List[str] = field(default_factory=list)

    #: 最近一次重算耗时（毫秒），状态栏与 `status_text()` 用
    last_recompute_ms: float = 0.0

    # ================================================================ 装配
    def load_paths(self, paths: Sequence[Union[str, Path]]) -> "LoadedSchedule":
        """按文件集合装配排班（自动识别 4 种格式），并把文件里的设置同步进来。

        可能抛 `ValueError`（格式不认识 / 班次拼不起来）——调用方展示原因即可。
        """
        ld = load_schedule_ex(paths)
        self.loaded = ld
        self.schedule = ld.schedule
        self.initial_moods.clear()
        self.mood_events.clear()
        self._sync_from_schedule()
        self.recompute()
        return ld

    def load_layout(self, data: dict, hours=None, label: str = "班次 1") -> None:
        """直接给一份**布局 dict**（本工具场景格式）建一个单班排班。

        `data = {"facilities": [...]}`；可选顶层 `entry_events` / `idle_to_dorm` /
        `initial_global` / `detached` 与场景 JSON 完全同义（见 `store/layout.build_base_layout`）。
        """
        top = {k: v for k, v in data.items()
               if k in ("entry_events", "idle_to_dorm", "detached")}
        shift = Shift(label=label, hours=to_decimal(hours if hours is not None else 24),
                      facilities=list(data.get("facilities") or []),
                      source="api:inline",
                      entry_events=None,
                      initial_global=dict(data.get("initial_global") or {}),
                      detached=list(data.get("detached") or []))
        world = build_base_layout({"facilities": shift.facilities,
                                   "initial_global": dict(shift.initial_global or {}),
                                   **top})
        shift.world = world
        self.schedule = Schedule([shift], to_decimal(hours if hours is not None else 24),
                                 detached=list(world.detached or []))
        self.loaded = LoadedSchedule(schedule=self.schedule)
        self.initial_moods.clear()
        self.mood_events.clear()
        self._sync_from_schedule()
        self.recompute()

    def _sync_from_schedule(self) -> None:
        """把排班自带（场景 JSON 顶层）的设置同步到会话（导入后调一次）。"""
        if self.schedule is None:
            return
        self.detached = list(getattr(self.schedule, "detached", []) or [])
        cfg = self.schedule.entry_config()
        self.entry_events = bool(cfg.enabled)
        self.entry_swap_with = cfg.swap_with
        self.entry_scope = getattr(cfg, "scope", "dorm")
        # 「位置也一起互换」按用户要求从界面收掉：界面固定"只换心情、两人留原位"。
        # 场景 JSON 里写 restore_back: false 会被这条界面口径覆盖（CLI / API 不受影响）。
        self.entry_restore_back = True
        self.entry_when = normalize_entry_when(getattr(cfg, "when", None)) or "full"
        self.entry_per_shift = list(getattr(cfg, "per_shift", []) or [])
        idle = getattr(self.schedule.shifts[0].world, "idle_to_dorm", None) if self.schedule.shifts else None
        self.idle_to_dorm = bool(getattr(idle, "enabled", False))
        self.idle_globals = {}
        self.idle_entries = {}
        for e in (getattr(idle, "per_operator", None) or []):
            label = _idle_label_of(e)
            if e.cycle is None and e.shift is None:
                self.idle_globals[e.name] = (bool(e.enabled), label)
            else:
                self.idle_entries[(e.cycle or 1, e.shift or 1, e.name)] = (bool(e.enabled), label)

    # ================================================================ 重算
    def recompute(self) -> Optional[Trajectory]:
        """按当前设置重算整周期轨迹（**唯一的重算入口**）。"""
        if self.schedule is None:
            self.traj = None
            return None
        # 「不在基建」名单是**排班级**设置：重算前同步进 Schedule（界面/接口改的是会话字段）
        if list(getattr(self.schedule, "detached", []) or []) != list(self.detached):
            self.schedule = self.schedule.with_detached(self.detached)
        self.traj = simulate_schedule(
            self.schedule, cycles=self.cycles,
            initial_moods=self.initial_moods,
            entry_events=self.entry_events,
            entry_swap_with=self.entry_swap_with,
            entry_scope=self.entry_scope,
            entry_restore_back=self.entry_restore_back,
            entry_when=self.entry_when,
            # ⚠️ 原样传（不要把 `[]` 变成 None）：`None` 会让引擎回退去读排班自带的
            #     `per_shift`（导入时可能带来了 Fiammetta 的逐班配置），
            #     而界面里"全不勾"就该是"没有覆盖"。
            entry_per_shift=list(self.entry_per_shift),
            idle_to_dorm=self.idle_to_dorm,
            idle_entries=self.idle_entry_list(),
            mood_events=list(self.mood_events))
        return self.traj

    @property
    def total_hours(self) -> Decimal:
        """整条轨迹覆盖的总时长（= 周期时长 × 周期数）。"""
        return self.schedule.cycle_hours * self.cycles if self.schedule else ZERO

    # ================================================================ 查询
    def moods_at(self, t) -> Dict[str, Decimal]:
        """（绝对）时刻 t 的全员心情。"""
        return self.traj.moods_at(t) if self.traj is not None else {}

    def mood_at(self, name: str, t) -> Optional[Decimal]:
        """（绝对）时刻 t 某人的心情；无轨迹/查无此人返回 None。"""
        if self.traj is None or name not in self.traj.moods:
            return None
        return self.traj.mood_at(name, t)

    def rate_at(self, name: str, t) -> Decimal:
        """（绝对）时刻 t 某人的净速率（点/时，>0 下降、<0 上升）。"""
        return self.traj.rate_at(name, t) if self.traj is not None else ZERO

    def rate_in_shift(self, name: str, index: int) -> Decimal:
        """第 `index` 班（0 基）里某人的速率（取班次起点那一刻）。"""
        if self.schedule is None or self.traj is None:
            return ZERO
        return self.rate_at(name, self.schedule.starts[index])

    def red_face_spans(self, name: str) -> List[Tuple[Decimal, Decimal]]:
        """某人的红脸区间 `[(起, 止), ...]`（单位小时，绝对时刻）。"""
        return self.traj.red_face_spans(name) if self.traj is not None else []

    def operator_names(self) -> List[str]:
        return self.schedule.operator_names() if self.schedule else []

    def shifts(self) -> List[Shift]:
        return list(self.schedule.shifts) if self.schedule else []

    def cycle_of(self, t) -> Tuple[int, Decimal]:
        """绝对时刻 t → `(第几个周期 1 基, 周期内时刻)`。"""
        if self.schedule is None:
            return 1, ZERO
        horizon = self.schedule.cycle_hours
        t = to_decimal(t)
        return int(t // horizon) + 1, t % horizon

    def shift_index_at(self, t) -> int:
        return self.schedule.index_at(t) if self.schedule else 0

    # ================================================================ 不在基建
    def bench_names(self) -> List[str]:
        """**「不在基建」的干员** = 名单里点名的 ∪ 整个排班都没排到位置的人（去重保序）。

        「不在基建」= **既不在工作设施、也不在宿舍**：他们在 `world` 里没有位置，
        所以不消耗、不回复、也不参与任何技能计数；轨迹里是一条**平线**（心情恒定）。
        """
        return self.schedule.bench_names() if self.schedule else []

    def not_in_shift(self, shift_index: int = 0) -> List[str]:
        """**本班次没排到位置**的人（含"不在基建"名单）——界面「不在基建」那一段的自动名单。

        = `operator_names()` − 该班次里**进驻/副手**的人。
        注意：他们此刻可能在**别的班次**上班，所以这一段的标题是
        「不在工作、也不在宿舍（**本班未排班**）」。
        """
        if self.schedule is None:
            return []
        idx = min(max(int(shift_index), 0), len(self.schedule.shifts) - 1)
        here = {o.name for o in self.schedule.shifts[idx].world.all_operators()}
        return [n for n in self.schedule.operator_names() if n not in here]

    def set_detached(self, names: Sequence[str], remove_from_slots: bool = True,
                     recompute: bool = False) -> None:
        """整份替换「不在基建」名单（去重保序）。

        `remove_from_slots=True`（默认）会把名单里的人**从所有班次的进驻位上摘下来**，
        于是"名单里的人 ↔ 不在任何房间里"成为一条**不变式** —— 不摘的话她一边在名单里、
        一边占着位置（有消耗、还会被"基建内每有 N 名 XX"数到），语义自相矛盾。
        ⚠️ 这一条踩过：只有 `add_detached` 摘位置、`set_detached` 不摘，
        于是"导入带名单的文件"与"直接设名单"两条路给出**不同**数值。

        `recompute=False`（默认）：只改状态、**不重算**（导入路径随后自己会算一次）；
        只想改名单就要新结果时传 `recompute=True`。
        """
        out: List[str] = []
        for n in (names or []):
            n = str(n).strip()
            if n and n not in out:
                out.append(n)
        self.detached = out
        if self.schedule is None:
            return
        if remove_from_slots:
            self._remove_from_slots(out)
        self.schedule = self.schedule.with_detached(out)
        self._sync_from_schedule()      # 名单也写回每班的 Shift/world
        if recompute:
            self.recompute()

    def _remove_from_slots(self, names: Sequence[str]) -> int:
        """把这些人从**所有班次**的进驻位上摘掉（返回动过几个班次）。"""
        if self.schedule is None or not names:
            return 0
        wanted = set(names)
        touched = 0
        for i in range(len(self.schedule.shifts)):
            facs = self.facilities_of(i)
            hit = False
            for f in facs:
                ops = [n for n in f.get("operators", []) if n not in wanted]
                if len(ops) != len(f.get("operators", [])):
                    hit = True
                    f["operators"] = ops
            if hit:
                self.schedule = self.schedule.replaced_shift(i, facs)
                touched += 1
        return touched

    def add_detached(self, name: str, remove_from_slots: bool = True) -> bool:
        """把某人加进「不在基建」名单（已在名单里返回 False）；默认连位置一起摘。"""
        name = str(name or "").strip()
        if not name or name in self.detached:
            return False
        self.set_detached(list(self.detached) + [name],
                          remove_from_slots=remove_from_slots)
        return True

    def remove_detached(self, name: str) -> bool:
        """把某人从「不在基建」名单里移除（**不**动他在房间里的位置）。"""
        if name not in self.detached:
            return False
        self.set_detached([n for n in self.detached if n != name])
        return True

    # ================================================================ 校验与摘要
    def validate(self) -> Validation:
        """逐班次跑布局自检（房间数上限 / 建造位 9 / 人数 ≤ 等级容量）。"""
        issues: List[ValidationIssue] = []
        for i, shift in enumerate(self.shifts(), start=1):
            for msg in shift.world.validate():
                issues.append(ValidationIssue(shift=i, label=shift.label, message=msg))
        return Validation(ok=not issues, issues=issues)

    def status_text(self) -> str:
        """一行状态摘要（状态栏与 API 的 `describe` 共用同一句口径）。"""
        if self.schedule is None or self.traj is None:
            return "尚未载入排班"
        return (f"{len(self.schedule.shifts)} 班 / 周期 "
                f"{self.schedule.cycle_hours}h　干员 {len(self.traj.names)} 名"
                f"　轨迹节点 {len(self.traj.times)}"
                f"　重算耗时 {self.last_recompute_ms:.0f} ms")

    def describe(self) -> dict:
        """结构化描述（API `describe` / 界面初始化共用）。"""
        sch = self.schedule
        if sch is None:
            return {"loaded": False}
        return {
            "loaded": True,
            "cycle_hours": str(sch.cycle_hours),
            "cycles": self.cycles,
            "total_hours": str(self.total_hours),
            "start_clock": str(sch.start_clock),
            "shifts": [{"index": i + 1, "label": s.label, "hours": str(s.hours),
                        "operators": list(s.operators),
                        "facilities": [[f.display_name, f.level,
                                        [o.name for o in f.operators]]
                                       for f in s.world.facilities]}
                       for i, s in enumerate(sch.shifts)],
            "operators": self.operator_names(),
            "detached": self.bench_names(),
            "detached_explicit": list(self.detached),
            "pool": list(getattr(self.loaded, "pool", []) or []),
            "import_reports": [r.to_dict() for r in (getattr(self.loaded, "reports", []) or [])],
            "settings": self.settings_dict(),
            "validation": self.validate().to_dict(),
        }

    def settings_dict(self) -> dict:
        """全部可调项的当前值（API 读、界面回填都用它）。"""
        return {
            "cycles": self.cycles,
            "start_clock": str(self.schedule.start_clock) if self.schedule else "0",
            "initial_moods": {n: str(v) for n, v in self.initial_moods.items()},
            "detached": list(self.detached),
            "mood_events": [{"name": e.name, "cycle": e.cycle, "t": str(e.t),
                             "mood": str(e.mood)} for e in self.mood_events],
            "entry_events": {
                "enabled": self.entry_events,
                "swap_with": self.entry_swap_with,
                "scope": self.entry_scope,
                "restore_back": self.entry_restore_back,
                "when": self.entry_when,
                "per_shift": [{"key": getattr(o, "key", None),
                               "enabled": getattr(o, "enabled", None),
                               "swap_with": getattr(o, "swap_with", None),
                               "scope": getattr(o, "scope", None),
                               "when": getattr(o, "when", None)}
                              for o in self.entry_per_shift],
            },
            "idle_to_dorm": {
                "enabled": self.idle_to_dorm,
                "per_operator": [
                    {"name": n, "enabled": use, "target": target}
                    for n, (use, target) in self.idle_globals.items()] +
                    [{"name": n, "enabled": use, "target": target,
                      "cycle": c, "shift": s}
                     for (c, s, n), (use, target) in self.idle_entries.items()],
            },
        }

    # ================================================================ 编辑（设置）
    def set_cycles(self, cycles: int) -> None:
        self.cycles = max(1, int(cycles))

    def set_timeline(self, hours: Optional[Sequence] = None,
                     cycle_hours=None, start_clock=None,
                     detached: Optional[Sequence[str]] = None) -> None:
        """「时间轴」：改各班时长 / 周期 / 周期起点钟点 / **不在基建名单**，然后重算。

        - `hours`：各班时长（长度必须等于班次数），改完周期**自动等于各班长之和**；
        - `cycle_hours`：只校验用（与各班长之和不等就抛 `ValueError`）；
        - `start_clock`：周期起点是几点，**纯显示口径**，引擎数值一字不变；
        - `detached`：**「不在基建」名单**（既不在工作设施、也不在宿舍的人）——
          整份替换（`[]` = 清空）。
        """
        if self.schedule is None:
            raise ValueError("尚未载入排班")
        if hours:
            if len(hours) != len(self.schedule.shifts):
                raise ValueError(f"需要 {len(self.schedule.shifts)} 个时长，收到 {len(hours)} 个")
            self.schedule = self.schedule.with_hours(hours)
        if cycle_hours is not None and to_decimal(cycle_hours) != self.schedule.cycle_hours:
            raise ValueError(f"各班时长之和 {self.schedule.cycle_hours}h ≠ 指定周期 "
                             f"{to_decimal(cycle_hours)}h")
        if start_clock is not None:
            self.schedule = self.schedule.with_start_clock(start_clock)
        if detached is not None:
            self.set_detached(detached)
        self.recompute()

    def set_start_clock(self, clock) -> None:
        """改「周期起点钟点」（纯显示口径）。"""
        if self.schedule is None:
            raise ValueError("尚未载入排班")
        self.schedule = self.schedule.with_start_clock(clock)

    def set_slots(self, shift_index: int, facility_index: int,
                  operators: Sequence[str]) -> None:
        """改某个班次某间房的**进驻干员**（`operators` 为空串的位置直接丢弃 = 清空该位）。"""
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        facs[facility_index]["operators"] = [n for n in operators if n]
        self.replace_facilities(shift_index, facs)

    def set_room_level(self, shift_index: int, facility_index: int, level: int) -> None:
        """改某个班次某间房的等级（容量随之变化）。"""
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        facs[facility_index]["level"] = int(level)
        self.replace_facilities(shift_index, facs)

    def replace_facilities(self, shift_index: int, facilities: List[dict]) -> None:
        """整份替换某班次的布局，然后重算。"""
        if self.schedule is None:
            raise ValueError("尚未载入排班")
        self.schedule = self.schedule.replaced_shift(shift_index, facilities)
        self.recompute()

    def facilities_of(self, shift_index: int) -> List[dict]:
        """某班次布局的**可改副本**（`{"type","level","operators",...}` 形式）。"""
        if self.schedule is None:
            return []
        return [dict(f, operators=list(f.get("operators", [])))
                for f in self.schedule.shifts[shift_index].facilities]

    # ---------------------------------------------------------------- 心情
    def imported_moods(self) -> Dict[str, Decimal]:
        """导入时各人的起点心情（「恢复导入值」用的基准）。"""
        return default_initial_moods(self.schedule) if self.schedule else {}

    def set_initial_mood(self, name: str, mood) -> None:
        """设置**周期起点**的心情（第 1 周期 0:00 那一格）。"""
        self.initial_moods[name] = to_decimal(mood)

    def set_initial_moods(self, moods: Dict[str, object]) -> None:
        """整份替换周期起点心情（`{}` = 全部回到导入值）。"""
        self.initial_moods = {str(k): to_decimal(v) for k, v in dict(moods).items()}

    def set_mood_at(self, name: str, mood, t=0) -> MoodSetEvent:
        """在**某个周期的某个时刻**把某人的心情直接置为给定值（心情指定事件/锚点）。

        第 1 周期的 0:00 会被自动路由到 `initial_moods`（那就是"起点心情"，不是锚点）。
        """
        cycle, offset = self.cycle_of(t)
        if cycle == 1 and offset == ZERO:
            self.set_initial_mood(name, mood)
            return None
        ev = MoodSetEvent(name=name, t=offset, mood=to_decimal(mood), cycle=cycle)
        self.mood_events = [e for e in self.mood_events
                            if not (e.name == name and e.cycle == cycle and e.t == offset)]
        self.mood_events.append(ev)
        return ev

    def clear_mood_events(self) -> None:
        """清空全部锚点（「恢复导入值」时会连它一起清）。"""
        self.mood_events = []

    def restore_imported_moods(self) -> None:
        """「恢复导入值」：清手动起点心情 + 清全部锚点。"""
        self.initial_moods.clear()
        self.mood_events.clear()

    # ---------------------------------------------------------------- 练度
    def set_training(self, elite: Optional[int] = None, level: Optional[int] = None,
                     names: Optional[Iterable[str]] = None) -> int:
        """改干员练度（默认改全基建所有人；`elite`/`level` 为 None 表示不动这一项）。

        返回改动的位置数。写回布局时**只有非满练才**记成对象
        `{"name": ..., "elite": 1}`，其余仍是纯字符串（JSON 保持简洁）。
        """
        if self.schedule is None:
            return 0
        wanted = set(names) if names is not None else None
        changed = 0
        for idx in range(len(self.schedule.shifts)):
            facs = self.facilities_of(idx)
            touched = False
            for f in facs:
                new_ops = []
                for spec in f.get("operators", []):
                    name = spec if isinstance(spec, str) else spec.get("name")
                    if wanted is not None and name not in wanted:
                        new_ops.append(spec)
                        continue
                    cur = {} if isinstance(spec, str) else dict(spec)
                    old = (int(cur.get("elite", 2)), int(cur.get("level", 30)))
                    e = old[0] if elite is None else int(elite)
                    lv = old[1] if level is None else int(level)
                    if e == 2 and lv == 30:
                        new_ops.append(name)          # 满练 → 纯字符串
                    else:
                        new_ops.append({"name": name, "elite": e, "level": lv})
                    if (e, lv) != old:
                        changed += 1
                        touched = True
                f["operators"] = new_ops
            if touched:
                self.schedule = self.schedule.replaced_shift(idx, facs)
        if changed:
            self.recompute()
        return changed

    def elite_badges(self) -> Dict[str, str]:
        """`{干员: "E1"}`——取该干员在各班次里**最低**的精英化（只有非 E2 才进表）。

        为什么取最低：同一个人在不同班次可以有不同练度（少见但合法），
        用最低那档才不会漏掉"某一班他的技能其实没生效"。
        """
        out: Dict[str, str] = {}
        for shift in self.shifts():
            for op in shift.world.all_operators():
                if op.elite < 2:
                    badge = f"E{op.elite}"
                    if badge < out.get(op.name, "E9"):
                        out[op.name] = badge
        return out

    def operator_obj(self, name: str, shift_index: int = 0):
        """找这个干员的 `Operator`（优先指定班次，其次第一个有他的班次）。"""
        if self.schedule is None or not name:
            return None
        idx = min(max(shift_index, 0), len(self.schedule.shifts) - 1)
        for shift in [self.schedule.shifts[idx]] + list(self.schedule.shifts):
            op = shift.world.get_operator(name)
            if op is not None:
                return op
        return None

    def elite_text(self, name: str) -> str:
        """练度摘要（对点查询用）：`练度 E1 · 已解锁 6 条心情技能；因未满练少 1 条（…）`。"""
        op = self.operator_obj(name)
        if op is None:
            return ""
        unlocked, locked = mood_skill_summary(op)
        text = f"练度 E{op.elite}（等级 {op.level}）· 已解锁 {unlocked} 条心情技能"
        if locked:
            names = "、".join(f"「{n}」（E{e}）" for n, e, _lv in locked[:3])
            more = f" 等 {len(locked)} 条" if len(locked) > 3 else ""
            text += f"；⚠ 因未满练少 {len(locked)} 条：{names}{more}"
        return text

    # ---------------------------------------------------------------- 干员池
    def fill_from_pool(self, shift_index: Optional[int] = None) -> int:
        """「从池中依次填入」：把干员池按顺序铺满各班次的空位。

        返回填入的人数；没有池（MAA / v3 输出那类文件）时为 0。
        """
        pool = list(getattr(self.loaded, "pool", []) or [])
        if self.schedule is None or not pool:
            return 0
        wanted = range(len(self.schedule.shifts)) if shift_index is None else [shift_index]
        used = set()
        for idx in wanted:
            for f in self.schedule.shifts[idx].world.facilities:
                for o in f.operators:
                    used.add(o.name)
        filled = 0
        for idx in wanted:
            facs = self.facilities_of(idx)
            for f in facs:
                cap = self._capacity_of(f)
                ops = list(f.get("operators", []))
                while len(ops) < cap and len(used) < len(pool):
                    cand = next((p for p in pool if p["name"] not in used), None)
                    if cand is None:
                        break
                    used.add(cand["name"])
                    spec = cand["name"]
                    if int(cand.get("elite", 2)) != 2 or int(cand.get("level", 30)) != 30:
                        spec = {"name": cand["name"], "elite": int(cand.get("elite", 2)),
                                "level": int(cand.get("level", 30))}
                    ops.append(spec)
                    filled += 1
                f["operators"] = ops
            self.schedule = self.schedule.replaced_shift(idx, facs)
        if filled:
            self.recompute()
        return filled

    @staticmethod
    def _capacity_of(fac_dict: dict) -> int:
        """由布局 dict 反推该房间的容量（与 `Facility.capacity` 同一套规则）。"""
        world = build_base_layout({"facilities": [fac_dict]})
        return world.facilities[0].capacity if world.facilities else 0

    # ================================================================ 闲置入宿 / 进驻事件
    def idle_entry_list(self) -> Optional[List[IdleToDormEntry]]:
        """把界面的逐次设置转成 `[IdleToDormEntry, ...]`——**只列改过默认的**
        （勾掉不参与的、或指定了目标的人）；没人改过就返回 `None`（＝全都参与、全自动）。

        目标有两类，界面用同一个下拉表达：
        - `宿舍01`/`宿舍02`…（**放进那间宿舍的空位**，不动任何人）→ `dorm=序号`
        - 干员名（**与这位满心情的宿舍干员互换**，他换出来闲置）→ `swap_with=名字`
        """
        out: List[IdleToDormEntry] = []
        for name, (use, target) in self.idle_globals.items():
            if use and not target:
                continue
            dorm = _dorm_index_of(target)
            out.append(IdleToDormEntry(name=name, enabled=use, dorm=dorm,
                                       swap_with=(None if dorm else target) or None))
        for (cyc, shf, n), (use, target) in self.idle_entries.items():
            if use and not target:
                continue
            dorm = _dorm_index_of(target)
            out.append(IdleToDormEntry(name=n, enabled=use, cycle=cyc, shift=shf,
                                       dorm=dorm, swap_with=(None if dorm else target) or None))
        return out or None

    def idle_groups(self, cycles: Optional[int] = None,
                    entries: Optional[dict] = None) -> List[tuple]:
        """按时间排序的**逐次入宿表** → `[(标题, (周期, 班次), [行, ...]), ...]`。

        `行 = (干员, 心情显示值, 位置, 参与, 目标, [可选目标…])`。
        只列出**真的有候选**的那几次（心情跨班跨周期连续 ⇒ 每次谁没满都不一样）。
        """
        cycles = int(cycles if cycles is not None else self.cycles)
        entries = self.idle_entries if entries is None else entries
        traj = self.traj
        if self.schedule is None or traj is None:
            return []
        groups = []
        for k in range(max(1, cycles)):
            for i, shift in enumerate(self.schedule.shifts):
                t0 = self.schedule.cycle_hours * k + self.schedule.starts[i]
                cands, targets = [], []
                shift_dorms = [f for f in shift.world.facilities
                               if f.ftype == FacilityType.DORMITORY]
                for di, dorm in enumerate(shift_dorms, 1):
                    if len(dorm.operators) < dorm.capacity:
                        targets.append(f"宿舍{di:02d}")
                for name in traj.names:
                    mood = traj.mood_at(name, t0)
                    fac = shift.world.facility_of(name)
                    if fac is not None and fac.ftype == FacilityType.DORMITORY:
                        if mood >= MOOD_MAX:
                            targets.append(name)     # 可以作为"被换出"的对象
                        continue
                    if fac is not None and fac.ftype not in (FacilityType.WORKSHOP,
                                                             FacilityType.TRAINING):
                        continue
                    if mood >= MOOD_MAX:
                        continue
                    cands.append((mood, name, fac.display_name if fac else "未排班"))
                if not cands:
                    continue
                cands.sort(key=lambda row: (row[0], row[1]))
                rows = []
                for mood, name, where in cands:
                    use, target = entries.get((k + 1, i + 1, name)) \
                        or self.idle_globals.get(name) or (True, None)
                    rows.append((name, mood, where, use, target,
                                 [t for t in targets if t != name]))
                groups.append((f"第 {k + 1} 周期 · 第 {i + 1} 班", (k + 1, i + 1), rows))
        return groups

    def idle_count(self) -> int:
        """当前设置下会有多少次"有人入宿"、共涉及多少人。"""
        return sum(len([r for r in rows if r[3]]) for _t, _s, rows in self.idle_groups())

    def entry_candidates(self) -> Tuple[List[str], List[str]]:
        """返回 `(触发者名单, 可交换对象名单)`。

        触发者 = 排班里可能触发 M15a 的干员（如菲亚梅塔）；
        可交换对象 = **同宿舍的其他干员排前面**，后面跟上排班里的其他干员
        （因为"基建任意位置"模式下任何位置的干员都能换）。
        """
        from mood_soc.rules import entry_event_holders
        holders: List[str] = []
        mates: List[str] = []
        for s in self.shifts():
            for who, _room in entry_event_holders(s.world):
                if who not in holders:
                    holders.append(who)
            for f in s.world.facilities:
                if f.ftype != FacilityType.DORMITORY:
                    continue
                names = [o.name for o in f.operators]
                if not any(n in holders for n in names):
                    continue
                for n in names:
                    if n not in holders and n not in mates:
                        mates.append(n)
        others = [n for n in self.operator_names() if n not in holders and n not in mates]
        return holders, mates + others

    # ================================================================ 干员名
    def all_operator_names(self, extra: Iterable[str] = ()) -> List[str]:
        """全部可选干员名（全量名册 + 内置心情技能表 + 指定补充）。"""
        from .schedule import all_operator_names
        return all_operator_names(extra)


# ============================================================================
# 小工具（界面与 API 共用，避免两处各写一份）
# ============================================================================
def _dorm_index_of(label) -> Optional[int]:
    """「宿舍01」→ `1`；不是这种标签（人名 / 空）就返回 `None`。"""
    text = (label or "").strip()
    if text.startswith("宿舍") and text[2:].isdigit():
        return int(text[2:])
    return None


def _idle_label_of(entry) -> Optional[str]:
    """`IdleToDormEntry` → 下拉里的标签（`宿舍01` 或 人名；都没有 = 自动）。"""
    if getattr(entry, "dorm", None) is not None:
        return f"宿舍{entry.dorm:02d}"
    return (getattr(entry, "swap_with", None) or None)


__all__ = [
    "Session", "Validation", "ValidationIssue",
    "DEFAULT_OPERATORS",
]
