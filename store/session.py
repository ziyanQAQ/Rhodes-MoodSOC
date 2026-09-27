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

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from data.skills_data import DEFAULT_OPERATORS
from mood_soc.battery import to_decimal
from mood_soc.config import MOOD_MAX, FacilityType
from mood_soc.models import IdleToDormEntry, normalize_entry_when
from mood_soc.rules import dorm_state, mood_skill_summary

from .layout import build_base_layout
from .schedule import (MoodSetEvent, LoadedSchedule, Schedule, Shift, Trajectory,
                       default_initial_moods, execution_points, load_schedule_ex,
                       load_schedule_from_imports, simulate_schedule)

ZERO = Decimal("0")

#: **周期数上限**（唯一口径）：界面下拉、`Session.set_cycles`、`closure`、
#: API `set_timeline` / `closure` 都按它夹取。超过就取上限（不报错）。
#: 为什么是 7：成本≈线性（示例排班实测重算一次 1 周期 0.32s、3 周期 1.16s、**7 周期 2.84s**），
#: 7 是"还能忍"的一档；再多就该去用 API 批量算、而不是让人盯着界面等。
MAX_CYCLES = 7


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
    #: 周期数（1~`MAX_CYCLES`）：把同一排班连跑几个周期
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

    #: 闲置入宿总开关（**默认开**：用户口径"闲置入宿默认是开启的"；文件里显式写 false 才关）
    idle_to_dorm: bool = True
    #: **锁定位置数**（文档 §5）：按竖向正序锁前 N 个位置，自动交换不换锁定区里的人（默认 5）
    idle_protected_slots: int = 5
    #: **黑名单**（文档 §6）：永远不能**通过闲置入宿进宿舍**的干员（不是"保护其宿舍位次"）
    idle_blacklist: List[str] = field(default_factory=list)
    #: 不限班次/周期的逐人设置 `{干员: (参与, 目标标签)}`
    idle_globals: Dict[str, Tuple[bool, Optional[str]]] = field(default_factory=dict)
    #: 逐次设置 `{(周期, 班次, 干员): (参与, 目标标签)}`（序号均为 1 基）
    idle_entries: Dict[Tuple[int, int, str], Tuple[bool, Optional[str]]] = field(default_factory=dict)

    #: 「不在基建」名单（既不在工作设施、也不在宿舍的人；场景 JSON 顶层 `detached`）。
    #: 语义：轨迹里**一条平线**（心情恒定），且**不参与任何技能计数**。见 `store.schedule`。
    detached: List[str] = field(default_factory=list)

    #: 最近一次重算耗时（毫秒），状态栏与 `status_text()` 用
    last_recompute_ms: float = 0.0

    #: **增量重算的种子**：`(算好的轨迹, 它的逐段输入指纹)`；
    #: 只有"前缀段的指纹没变"才拿它续算（见 `recompute_inputs` / `_segment_signatures`）。
    _resume_from: Optional[Tuple[Trajectory, list]] = None

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

    def load_data(self, data: Any, hours: Optional[Sequence] = None,
                  cycle_hours=None, cycles: Optional[int] = None,
                  apply_file_settings: bool = False,
                  entry_events: Optional[bool] = None,
                  idle_to_dorm: Optional[bool] = None) -> "LoadedSchedule":
        """直接给一份**已经解析好的 JSON**（dict）建排班 —— 不落临时文件（给 Rust 侧调用用）。

        `data` 与 `load_paths` 认得的东西完全一样，**格式自动识别**：
        本工具场景 / MAA 排班 / **v3 求解输出**（`{"result": {...}}` 信封）/ v4 蓝图+干员池。
        装配走 `load_schedule_from_imports`（与按文件载入**同一条路**），
        所以「同一份 JSON 走文件 vs 走内存」的数值必须逐位相同。

        ⚠️ **本入口与界面 / `load_file` 的差别只剩"文件里的开关"那一行**：

        | 项 | 本入口默认 | 理由 |
        |---|---|---|
        | 文件里的开关（如 v3 的 `Fiammetta.enable`） | **不继承**（`apply_file_settings=False`） | 换干员必须手动配置，默认关 |
        | 闲置入宿 | **开**（本项目默认四级优先级） | v3 没给就按本项目默认逻辑；**这也是全项目默认**（用户裁决"闲置入宿默认是开启的"），界面 / `load_file` / `simulate_schedule` 同样默认开 |
        | 周期 | 取文件里的班次时长（如 12/6/6 → 24h），`cycles=1` | 正好一个完整周期 |

        要按文件里的设置走（老口径）就传 `apply_file_settings=True`。
        """
        from .sources import import_data as _import_data

        ld = load_schedule_from_imports([_import_data(data, source="api:inline")],
                                        cycle_hours=cycle_hours, hours=hours)
        self.loaded = ld
        self.schedule = ld.schedule
        self.initial_moods.clear()
        self.mood_events.clear()
        self._sync_from_schedule()
        if not apply_file_settings:
            # 换心情（进驻事件）：本入口默认**关**，且不继承文件里的逐班覆盖。
            self.entry_events = False
            self.entry_swap_with = None
            self.entry_scope = "dorm"
            self.entry_restore_back = True
            self.entry_when = "full"
            self.entry_per_shift = []
            self.idle_globals = {}
            self.idle_entries = {}
            # 文件里的闲置入宿全局口径也不继承（锁定位置数 / 黑名单回到项目默认）
            self.idle_protected_slots = 5
            self.idle_blacklist = []
        if entry_events is not None:
            self.entry_events = bool(entry_events)
        # 闲置入宿：本入口默认**开**（未显式指定时）；显式 `False` 才关。
        self.idle_to_dorm = True if idle_to_dorm is None else bool(idle_to_dorm)
        if cycles is not None:
            self.set_cycles(int(cycles))
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
        # ⚠️ 默认**开**（用户口径"闲置入宿默认是开启的"）：文件里没写这个键 → 开；
        #    显式写 `"idle_to_dorm": false` / `{"enabled": false}` → 关。
        self.idle_to_dorm = bool(getattr(idle, "enabled", True))
        self.idle_protected_slots = int(getattr(idle, "protected_slots", None) or 5)
        self.idle_blacklist = [str(n) for n in (getattr(idle, "blacklist", None) or [])]
        self.idle_globals = {}
        self.idle_entries = {}
        for e in (getattr(idle, "per_operator", None) or []):
            label = _idle_label_of(e)
            if e.cycle is None and e.shift is None:
                self.idle_globals[e.name] = (bool(e.enabled), label)
            else:
                self.idle_entries[(e.cycle or 1, e.shift or 1, e.name)] = (bool(e.enabled), label)

    # ================================================================ 重算
    @staticmethod
    def _world_digest(world) -> tuple:
        """一份布局的**内容摘要**（用它判"这一班变没变"）。

        ⚠️ 为什么不只用 `id(world)`：改练度（`set_training`）之类是**就地改**干员对象的，
        对象身份不变 —— 只比身份会以为"这一段没变"，于是拿旧轨迹当种子、数值静默出错。
        摘要按内容算（房间/等级/进驻者/练度/副手），一次重算只算几遍，代价可忽略。
        """
        return tuple(
            (f.display_name, int(getattr(f, "level", 0) or 0), bool(getattr(f, "enabled", True)),
             tuple((o.name, int(getattr(o, "elite", 0) or 0), int(getattr(o, "level", 0) or 0))
                   for o in f.operators),
             tuple(o.name for o in f.deputies))
            for f in world.facilities)

    def _segment_signatures(self, cycles: int) -> list:
        """**逐段输入指纹**（P6b）：只要前 k 段的指纹与上一份轨迹一致，那 k 段就能复用。

        "一段"＝一个**换班执行点**（班初或长班的内部换班点，见 `schedule.execution_points`）。
        "一段的输入"＝ 全局设置 ＋ 这一班的布局/按班覆盖 ＋ **这一格（周期×班次）**的闲置入宿
        逐次设置 ＋ 落在这一段里的心情锚点。改哪一类，切点就落在第一个受影响段：
        布局/练度/房间等级 ⇒ 那一班在**第 1 周期**的出现处；末周期的锚点/逐次设置 ⇒ 那一段；
        全局设置（初始心情/总开关/名单/时间轴）⇒ 第 0 段（整条重算）。
        ⚠️ 同一班的多个执行点共用同一份逐人设置（文档 §8/§14）⇒ 指纹里的 `cells` 那项**相同**，
        只有心情锚点按各自时刻归属。
        """
        sch = self.schedule
        n = len(sch.shifts)
        glob = (
            n, str(sch.cycle_hours), tuple(str(s.hours) for s in sch.shifts),
            str(getattr(sch, "start_clock", "")),
            tuple(sorted((str(k), str(v)) for k, v in self.initial_moods.items())),
            bool(self.entry_events), self.entry_swap_with, self.entry_scope,
            bool(self.entry_restore_back), self.entry_when,
            bool(self.idle_to_dorm), tuple(self.detached),
            # 闲置入宿的**全局口径**：锁定位置数 / 黑名单 / 不限班次的逐人设置 ——
            # ⚠️ 三者都会影响**每一段**，所以进"整条重算"的指纹。
            #    （`idle_globals` 是列表推导式的：`{名字: (参与, 目标)}`，
            #      值里有 `None`，直接排序会 TypeError ⇒ 用 repr 归一。）
            int(self.idle_protected_slots), tuple(sorted(self.idle_blacklist)),
            tuple(sorted((str(k), repr(v)) for k, v in self.idle_globals.items())),
        )
        per_shift = []
        for i, s in enumerate(sch.shifts):
            overrides = tuple(sorted(repr(e) for e in self.entry_per_shift
                                     if int(getattr(e, "key", 0) or 0) == i + 1))
            per_shift.append((self._world_digest(s.world), overrides))
        cells: Dict[tuple, list] = {}
        for (cyc, shf, name), val in self.idle_entries.items():
            cells.setdefault((cyc, shf), []).append((name, repr(val)))
        points = execution_points(sch, cycles)
        # 心情锚点落到**它所在的那个执行点段**上（按绝对时刻二分，不做下标算术 ——
        # 执行点的个数随班长/周期数变化，算术很容易错位）。
        ev_by_seg: Dict[int, list] = {}
        for ev in self.mood_events:
            cyc = int(getattr(ev, "cycle", 1) or 1)
            if cyc < 1 or cyc > cycles:
                continue
            at = to_decimal(ev.t) + sch.cycle_hours * (cyc - 1)
            idx = next((j for j, (t0, seg_end, *_rest) in enumerate(points)
                        if t0 <= at < seg_end), None)
            if idx is None:
                continue
            ev_by_seg.setdefault(idx, []).append(repr(ev))
        sigs = []
        for j, (_t0, _seg_end, i, cyc, _offset) in enumerate(points):
            sigs.append((glob, per_shift[i],
                         tuple(sorted(cells.get((cyc, i + 1), ()))),
                         tuple(sorted(ev_by_seg.get(j, ())))))
        return sigs

    def recompute_inputs(self) -> Optional[dict]:
        """把"重算要用的**全部输入**"冻结成一个参数包（`simulate_schedule` 的关键字参数）。

        为什么要拆出来（2026-09，响应速度 P5）：界面把重算挪到**工作线程**去跑，
        线程里只能碰"主线程不会再改"的东西 —— 于是这里做三件事：
          ① 该同步的先同步（「不在基建」名单写回 `Schedule`，与 `recompute` 原来一致）；
          ② 传**浅拷贝**：`initial_moods` / `mood_events` / `entry_per_shift` 都 `dict()`/`list()`
             一份 —— 界面上这些字段是**就地改**的（`set_initial_moods` 等）；
          ③ `schedule` 直接传引用：它是**换新对象**的（`set_slots`/`set_timeline`/`with_detached`
             都返回新 `Schedule`），线程拿着旧对象不会被主线程改到。
        **增量（P6a/P6b）**：逐段输入指纹一比对，就能算出"第一个变了的段" —— 从它续算，
        前面的段直接复用（周期数变少＝只截断，一段都不算）。
        没有排班（未导入）→ `None`。
        """
        if self.schedule is None:
            return None
        if list(getattr(self.schedule, "detached", []) or []) != list(self.detached):
            self.schedule = self.schedule.with_detached(self.detached)
        sigs = self._segment_signatures(self.cycles)
        cached = self._resume_from
        resume = None
        if cached is not None and cached[0] is self.traj:
            prev_sigs = cached[1] or ()
            m = min(len(prev_sigs), len(sigs))
            cut = next((i for i in range(m) if prev_sigs[i] != sigs[i]), m)
            if cut > 0:
                resume = (cached[0], cut)
        return dict(
            schedule=self.schedule, cycles=self.cycles,
            initial_moods=dict(self.initial_moods),
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
            idle_protected_slots=int(self.idle_protected_slots),
            idle_blacklist=list(self.idle_blacklist),
            mood_events=list(self.mood_events),
            continue_from=resume,
            _sigs=sigs,               # 给 `adopt` 记账用，不进引擎（见 `compute_trajectory`）
        )

    @staticmethod
    def compute_trajectory(inputs: dict) -> Trajectory:
        """**纯计算**：由参数包算一条轨迹（不碰 `Session` 的任何状态）。

        界面把它丢进工作线程；`Session` 自己的 `recompute()` 也在主线程直接调它。
        `simulate_schedule` 全程只读参数包、返回全新对象，所以线程安全。
        """
        args = dict(inputs)
        args.pop("_sigs", None)
        return simulate_schedule(**args)

    def adopt(self, traj: Optional[Trajectory], sigs: Optional[list] = None) -> None:
        """把算好的轨迹**装进会话**（只该在主线程调），并记下"它是哪套输入算出来的"。"""
        self.traj = traj
        self._resume_from = (traj, sigs) if traj is not None and sigs is not None else None

    def recompute(self) -> Optional[Trajectory]:
        """按当前设置重算整周期轨迹（**唯一的重算入口**；同步、阻塞）。

        ⚠️ 界面上的"编辑"走的是异步版（`ui/app.py: recompute_async`，后台线程 + 落地），
        这里保持同步语义：导入 / 程序接口 / 测试都指望"函数返回时轨迹已经是新的"。
        """
        inputs = self.recompute_inputs()
        if inputs is None:
            self.traj = None
            return None
        sigs = inputs["_sigs"]
        self.traj = self.compute_trajectory(inputs)
        self._resume_from = (self.traj, sigs)
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

    def layout_at(self, t) -> Optional[dict]:
        """**（绝对）时刻 t 的基地布局快照**：每间房 + 在位干员 + 该刻心情与速率。

        读的是**引擎那份布局副本**（`Trajectory.world_at(t)`）—— 换心情 / 闲置入宿换过人之
        后的**真实**排布，**不是**导入的那份快照（`schedule.shifts[i].world`）；两者在换过人
        之后会不一样。落在班次边界取**右侧**（与 `mood_at` / `rate_at` 同口径）。

        返回 `None` = 还没有轨迹（尚未载入排班）。数值是 `Decimal`，出参由 `api` 侧收敛。
        """
        if self.traj is None or self.schedule is None:
            return None
        t = to_decimal(t)
        world = self.traj.world_at(t)
        if world is None:
            return None
        cycle_no, in_cycle = self.cycle_of(t)
        idx = self.shift_index_at(t)

        def _person(o, slot=None) -> dict:
            row = {"name": o.name, "mood": self.mood_at(o.name, t),
                   "rate": self.rate_at(o.name, t),
                   "elite": o.elite, "level": o.level}
            if slot is not None:
                row["slot"] = slot
            return row

        rooms: List[dict] = []
        placed = set()
        for i, f in enumerate(world.facilities):
            rooms.append({
                "index": i + 1,
                "type": f.label,
                "name": f.display_name,
                "level": f.level,
                "capacity": f.capacity,
                "enabled": bool(f.enabled),
                "operators": [_person(o, slot) for slot, o in enumerate(f.operators, start=1)],
                "deputies": [_person(o) for o in f.deputies],
            })
            placed.update(o.name for o in f.operators)
            placed.update(o.name for o in f.deputies)
        return {
            "at": t,
            "cycle": cycle_no,
            "in_cycle": in_cycle,
            "shift_index": idx + 1,
            "shift_label": self.schedule.shifts[idx].label,
            "rooms": rooms,
            # 「不在基建」= 既不在工作设施、也不在宿舍（心情一条平线，见 `bench_names`）
            "detached": [_person_light(self, n, t) for n in self.traj.names if n not in placed],
            "moods": self.moods_at(t),
        }

    def closure(self, cycles: int = 3) -> Optional[dict]:
        """**闭环体检**：把同一排班连跑 `cycles` 个周期，看心情是否收敛（＝布局能不能长期跑）。

        口径：`cycles` 是"心情跨周期连续"（见 `simulate_schedule`），所以第 k 个周期末的心情
        就是"下一轮开局的心情"。若第 k 与 k−1 个周期末**逐人相同**，说明第 k 轮起进入固定点
        ——这套布局可以一直跑下去（是否有人红脸另算，见 `layout_sustain_hours`）。

        ⚠️ **会改会话的 `cycles` 并重算**（与 `moods` 的 `cycles` 参数同一口径）——
        本方法是"改成多周期看收敛"，返回的 `cycles` 就是改完的值。
        `cycles` 同样夹到 `1 ~ MAX_CYCLES`（见 `set_cycles`）。

        返回 `None` = 尚未载入排班。
        """
        if self.schedule is None:
            return None
        self.set_cycles(cycles)
        self.recompute()
        cycle_hours = self.schedule.cycle_hours
        starts: Dict[int, Dict[str, Decimal]] = {}
        ends: Dict[int, Dict[str, Decimal]] = {}
        for k in range(1, self.cycles + 1):
            starts[k] = self.moods_at(cycle_hours * (k - 1))
            ends[k] = self.moods_at(cycle_hours * k)

        converged_from: Optional[int] = None
        for k in range(2, self.cycles + 1):
            if _same_moods(ends[k], ends[k - 1]):
                converged_from = k
                break

        worst_t: Optional[Decimal] = None
        bottleneck: Optional[str] = None
        red: Dict[str, list] = {}
        for n in self.traj.names:
            spans = self.red_face_spans(n)
            if spans:
                red[n] = [[a, b] for a, b in spans]
                if worst_t is None or spans[0][0] < worst_t:
                    worst_t, bottleneck = spans[0][0], n

        delta = {n: ends[self.cycles][n] - starts[1][n] for n in self.traj.names}
        if converged_from is None:
            verdict = (f"未收敛：{self.cycles} 个周期后周期末心情仍在变化"
                       f"（把 cycles 调大再看）")
        elif worst_t is None:
            verdict = (f"稳定：第 {converged_from} 周期起周期末心情逐人不再变化，"
                       f"且整段无人红脸")
        else:
            verdict = (f"稳定但有人红脸：第 {converged_from} 周期起周期末不再变化，"
                       f"但 {bottleneck} 在 {worst_t}h 就红了脸")
        return {
            "cycles": self.cycles,
            "cycle_hours": cycle_hours,
            "cycle_start_moods": {str(k): v for k, v in starts.items()},
            "cycle_end_moods": {str(k): v for k, v in ends.items()},
            "converged": converged_from is not None,
            "converged_from_cycle": converged_from,
            # 周期末 vs 周期初（>0 = 越跑越多、<0 = 越跑越少）
            "delta_last_vs_first": delta,
            "layout_sustain_hours": worst_t,
            "bottleneck": bottleneck,
            "red_face": red,
            "verdict": verdict,
        }

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
        """把这些人从**所有班次**的进驻位上摘掉（返回动过几个班次）。

        ⚠️ 布局里的 `operators` 有两种写法：**名字字符串**（`["泡泡", "火神"]`）与
        **带心情的对象**（`[{"name": "泡泡", "mood": 10}]`，场景 JSON 允许）——
        两种都要认。曾经直接 `n not in wanted`，遇到对象写法会 `TypeError: unhashable type: 'dict'`
        （2026-09 由"面板行序"用例暴露出来）。
        """
        if self.schedule is None or not names:
            return 0
        wanted = set(names)

        def _op_name(item) -> str:
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                return str(item.get("name") or "").strip()
            return str(item)

        touched = 0
        for i in range(len(self.schedule.shifts)):
            facs = self.facilities_of(i)
            hit = False
            for f in facs:
                ops = f.get("operators") or []
                kept = [n for n in ops if _op_name(n) not in wanted]
                if len(kept) != len(ops):
                    hit = True
                    f["operators"] = kept
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
                "protected_slots": int(self.idle_protected_slots),
                "blacklist": list(self.idle_blacklist),
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
        """设周期数：夹到 `1 ~ MAX_CYCLES`（越界不报错、直接取边界）。"""
        self.cycles = min(MAX_CYCLES, max(1, int(cycles)))

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
        （勾掉不参与的、指定了位置、或点名了对象的人）；没人改过就返回 `None`
        （＝全都参与、全自动）。

        目标有三类，界面用同一个下拉表达（标签 ↔ 字段的翻译只在这一处）：
        - `宿舍01`（放进那间的**下一个连续位**，不动任何人）→ `dorm=序号`
        - `宿舍01·第3位`（**精确位次**：是空位就入住、有人就过心情闸互换）→ `dorm=序号, slot=3`
        - 干员名（**手动点名**与那一刻在宿舍的这位互换）→ `swap_with=名字`
        """
        out: List[IdleToDormEntry] = []
        for name, (use, target) in self.idle_globals.items():
            if use and not target:
                continue
            out.append(_entry_from_label(name, use, target))
        for (cyc, shf, n), (use, target) in self.idle_entries.items():
            if use and not target:
                continue
            entry = _entry_from_label(n, use, target)
            entry.cycle, entry.shift = cyc, shf
            out.append(entry)
        return out or None

    @staticmethod
    def _idle_options(state: dict, name: str, mood_of: dict, traj, t0) -> List[str]:
        """某一行的「位置 / 换谁」下拉选项（按**轮到她的那一刻**的宿舍态算）。

        三类，顺序即下拉里的显示顺序（「自动…」由面板自己加在最前面）：

        | 选项 | 含义 |
        |---|---|
        | `宿舍NN` | 那间宿舍的**下一个连续位**（只在那间还有空位时列出） |
        | `宿舍NN·第M位` | **精确位次**：M 取 `1..下一个连续位`（含）—— 更靠后的位次会留空洞，引擎必跳过，所以不列 |
        | `人名 心情` | **点名互换**：那一刻**所有可用宿舍里的人**（含黑名单干员；不列候选人自己） |

        ⚠️ 必须用 `traj.idle_state_at(t0, 干员)`（引擎**轮到这一位**时的宿舍态）——
        候选是依次处理的，拿班末世界或排班快照会列出那一刻早已被换出宿舍的人。
        """
        opts: List[str] = []
        dorms = state.get("dorms", {})
        for no in sorted(dorms):
            nxt = int(state.get("next", {}).get(no, 1))
            cap = int(state.get("capacity", {}).get(no, 0))
            if nxt <= cap:
                opts.append(f"宿舍{no:02d}")
            for slot in range(1, min(nxt, cap) + 1):
                opts.append(f"宿舍{no:02d}·第{slot}位")
        people = []
        for no in sorted(dorms):
            for who in dorms[no]:
                if who == name:
                    continue                     # 候选人不列自己
                m = mood_of[who] if who in mood_of else traj.mood_at(who, t0)
                people.append((-m, f"{who} {m:.1f}"))
        people.sort()                            # 心情高的排前面（最好的目标先出现）
        opts += [label for _k, label in people]
        return opts

    def idle_groups(self, cycles: Optional[int] = None,
                    entries: Optional[dict] = None) -> List[tuple]:
        """按时间排序的**逐次入宿表** → `[(标题, (周期, 班次), [行, ...], 起点, 终点), ...]`。

        `行 = (干员, 心情显示值, 位置, 参与, 目标, [可选目标…])`。
        只列出**真的有候选**的那几次（心情跨班跨周期连续 ⇒ 每次谁没满都不一样）。
        `cycles` 同样夹到 `1 ~ MAX_CYCLES`（与 `set_cycles` 一个口径）。

        **一个换班执行点一组**（文档 §3/§14）：真实班初一组，长班的每个内部换班点各一组
        （标题写成 `第 1 周期 · 第 2 班（12h 内部换班）`）；`起点`/`终点` 是绝对小时，
        界面据它渲染时钟区间。⚠️ **同班各执行点共用同一份逐人设置** —— 组里的
        `(周期, 班次, 干员)` 键是同一个，所以面板改任一组会同步影响同班其他执行点。

        **候选口径与引擎同一份**（文档 §7，`rules.apply_idle_to_dorm`）：该班
        **完全没有出现在任何设施**里、心情 < 24、且不在**黑名单**里的人；
        行序＝(心情↑, 名字↑) —— 候选是**依次**处理的，所以表里的行序就是引擎的安排顺序。
        ⚠️ 班初在加工站 / 训练室的人、副手、所有上班与在宿舍的人**都不是候选**，
        所以表里不再出现"挂件位"那些行。

        ⚠️ **「谁在宿舍 / 哪间有空位」按引擎那一刻的那份世界算**（AGENTS 坑 20）：
        每一行取 `traj.idle_state_at(t0, 干员)`（＝引擎**轮到这一位**时的宿舍态），
        取不到（没开闲置入宿 / 她不是引擎候选）就退回整份段世界 `world_at(t0)`，
        再退回排班快照。**不能**直接拿 `shift.world` 快照当"她那一刻的世界" ——
        候选是依次处理的，排前面的人会把宿舍里的人换出去，而快照里那位看着还在宿舍
        （用户报过"面板里列着清流、那一刻宿舍里并没有清流"）。
        """
        cycles = min(MAX_CYCLES, max(1, int(cycles if cycles is not None else self.cycles)))
        entries = self.idle_entries if entries is None else entries
        traj = self.traj
        if self.schedule is None or traj is None:
            return []
        total = self.schedule.cycle_hours * cycles
        bench = set(self.bench_names())          # 「不在基建」的人（含名单点名的）
        blocked = set(self.idle_blacklist)       # 黑名单：不进候选、也不出现在这张表里
        groups = []
        for (t0, seg_end, i, cyc, offset) in execution_points(self.schedule, cycles):
            if t0 >= total:
                break
            shift = self.schedule.shifts[i]
            end = min(seg_end, total)
            mood_of = traj.moods_at(t0)
            # 兜底那份宿舍态：整份段世界（引擎那份）→ 排班快照
            base_state = dorm_state(traj.world_at(t0) or shift.world)
            cands = []                       # [(心情, 名字, 位置)]
            for name in traj.names:
                if name in blocked:
                    continue                 # 黑名单：不进初始候选
                if shift.world.facility_of(name) is not None:
                    continue                 # 该班出现在任何设施里 ⇒ 不是候选（文档 §7-3）
                mood = mood_of[name]
                if mood >= MOOD_MAX:
                    continue                 # 心情满 24，不需要恢复
                where = "不在基建" if name in bench else "未排班"
                cands.append((mood, name, where))
            if not cands:
                continue
            # 与引擎同一口径：① 心情从低到高 ② 名字（排序稳定）。候选依次处理 ⇒ 行序＝安排顺序。
            cands.sort(key=lambda row: (row[0], row[1]))
            rows = []
            for mood, name, where in cands:
                # 这一行的可选目标＝**轮到她的那一刻**宿舍里的人和空位（引擎那份世界）
                state = traj.idle_state_at(t0, name) or base_state
                opts = self._idle_options(state, name, mood_of, traj, t0)
                use, target = entries.get((cyc, i + 1, name)) \
                    or self.idle_globals.get(name) or (True, None)
                rows.append((name, mood, where, use, target, opts))
            title = f"第 {cyc} 周期 · 第 {i + 1} 班"
            if offset > 0:
                title += f"（{format(offset.normalize(), 'f')}h 内部换班）"
            groups.append((title, (cyc, i + 1), rows, t0, end))
        return groups

    def idle_count(self) -> int:
        """当前设置下会有多少次"有人入宿"、共涉及多少人（**按换班执行点**计）。"""
        return sum(len([r for r in group[2] if r[3]]) for group in self.idle_groups())

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
def _person_light(session: "Session", name: str, t) -> dict:
    """「不在基建」的人在 `layout_at` 里的那一行：心情一条平线，速率恒为 0。"""
    return {"name": name, "mood": session.mood_at(name, t), "rate": ZERO}


def _same_moods(a: Dict[str, Decimal], b: Dict[str, Decimal],
                eps: Decimal = Decimal("1e-9")) -> bool:
    """两组"全员心情"是否逐人相同（闭环判据；`eps` 吸收 Decimal 除法的末位舍入）。"""
    if set(a) != set(b):
        return False
    return all(abs(a[n] - b[n]) <= eps for n in a)


#: 「宿舍NN」/「宿舍NN·第M位」标签的形状（人与界面的翻译只认这一处）
_DORM_LABEL_RE = re.compile(r"^宿舍\s*0*(\d+)\s*(?:[·.\-]?\s*第\s*0*(\d+)\s*位)?$")


def _dorm_index_of(label) -> Optional[int]:
    """「宿舍01」/「宿舍01·第3位」→ `1`；不是这种标签（人名 / 空）就返回 `None`。"""
    match = _DORM_LABEL_RE.match((label or "").strip())
    return int(match.group(1)) if match else None


def _dorm_slot_of(label) -> Optional[int]:
    """「宿舍01·第3位」→ `3`；只写了宿舍序号（或压根不是位置标签）→ `None`。"""
    match = _DORM_LABEL_RE.match((label or "").strip())
    return int(match.group(2)) if match and match.group(2) else None


def _idle_label_of(entry) -> Optional[str]:
    """`IdleToDormEntry` → 下拉里的标签（`宿舍01` / `宿舍01·第3位` / 人名；都没有 = 自动）。"""
    if getattr(entry, "dorm", None) is not None:
        slot = getattr(entry, "slot", None)
        return f"宿舍{entry.dorm:02d}" + (f"·第{slot}位" if slot else "")
    return (getattr(entry, "swap_with", None) or None)


def _entry_from_label(name: str, use: bool, label) -> IdleToDormEntry:
    """下拉标签 → `IdleToDormEntry`（**界面的标签只有这一处翻译成引擎字段**）。

    `宿舍01` → `dorm=1`；`宿舍01·第3位` → `dorm=1, slot=3`；人名 → `swap_with=名字`；
    空 / 「自动…」→ 全都参与、全自动。
    """
    dorm = _dorm_index_of(label)
    if dorm is not None:
        return IdleToDormEntry(name=name, enabled=use, dorm=dorm,
                               slot=_dorm_slot_of(label))
    return IdleToDormEntry(name=name, enabled=use,
                           swap_with=idle_target_name(label))


def idle_target_name(label) -> Optional[str]:
    """下拉标签 → **干员名**（`"巫恋 23.4"` → `"巫恋"`）；`宿舍NN` / 「自动…」/ 空 → `None`。

    ⚠️ 为什么标签里带心情：第④级的「点名换人」是**主动换**（不要求对方满 24），
    所以选项必须让人看得见他当时的心情（`store/session.idle_groups` 里生成）。
    剥法：只剩最后一段"能解析成数字"的尾巴才当心情剥掉 —— 干员名里没有空格
    （`data/operators.txt`），所以这条规则不会误伤。
    """
    text = (label or "").strip()
    if not text or text.startswith("自动") or _dorm_index_of(text) is not None:
        return None
    head, _sp, tail = text.rpartition(" ")
    if head and tail:
        try:
            float(tail)
        except ValueError:
            return text
        return head
    return text


__all__ = [
    "Session", "Validation", "ValidationIssue",
    "DEFAULT_OPERATORS",
]
