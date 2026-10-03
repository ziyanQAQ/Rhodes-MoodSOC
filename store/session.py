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
from mood_soc.models import IdleToDormEntry, normalize_entry_when, read_manual
from mood_soc.rules import mood_skill_summary

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
    #: **锁定位置数**（文档 §5）：按竖向正序锁前 N 个位置，自动入宿不换锁定区里的人（默认 5）
    idle_protected_slots: int = 5
    #: **黑名单**（文档 §6）：永远不能**通过闲置入宿进宿舍**的干员（不是"保护其宿舍位次"）
    idle_blacklist: List[str] = field(default_factory=list)
    #: 不限班次/周期的逐人设置：`{干员: 参不参与}`（只记**改过默认**的，即 `False`）
    idle_globals: Dict[str, bool] = field(default_factory=dict)
    #: 逐次设置 `{(周期, 班次, 干员): 参不参与}`（序号均为 1 基）
    idle_entries: Dict[Tuple[int, int, str], bool] = field(default_factory=dict)

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
        self._sync_from_schedule(from_import=True)
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
        | 闲置入宿 | **开**（空位优先；全满后按竖向反序扫描锁定区外位置，取首个严格高于候选者） | v3 没给就按本项目默认逻辑；**这也是全项目默认**（用户裁决"闲置入宿默认是开启的"），界面 / `load_file` / `simulate_schedule` 同样默认开 |
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
        self._sync_from_schedule(from_import=True)
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
        self._sync_from_schedule(from_import=True)
        self.recompute()

    def _sync_from_schedule(self, *, from_import: bool = False) -> None:
        """把排班自带（场景 JSON 顶层）的设置同步到会话（导入后调一次）。

        `from_import=True`（三条 `load_*` 传 `True`）时额外做一件事：**名单优先** ——
        文件里同时写了 `detached` 与"这个人占着某个位次"时（老文件、手改文件都可能），
        把她从位置上摘掉、**并解除那位次的锁**，再往导入摘要里记一条（`LoadedSchedule.notes`）。

        ⚠️ **为什么导入"让步"、交互层却"拒绝"**：交互层拒绝是为了**不让用户造出**
        "锁着 + 在名单里"这个自相矛盾的状态（见 `set_detached`）；而导入是**数据**，
        报错会让老文件直接打不开 —— 两边口径不同是有意的（Q6 的 (d3) + (i)）。
        """
        if self.schedule is None:
            return
        self.detached = list(getattr(self.schedule, "detached", []) or [])
        if from_import and self.detached:
            self._resolve_imported_detached()
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
        protected_slots = getattr(idle, "protected_slots", None)
        self.idle_protected_slots = 5 if protected_slots is None else int(protected_slots)
        self.idle_blacklist = [str(n) for n in (getattr(idle, "blacklist", None) or [])]
        self.idle_globals = {}
        self.idle_entries = {}
        for e in (getattr(idle, "per_operator", None) or []):
            if e.cycle is None and e.shift is None:
                self.idle_globals[e.name] = bool(e.enabled)
            else:
                self.idle_entries[(e.cycle or 1, e.shift or 1, e.name)] = bool(e.enabled)

    def _resolve_imported_detached(self) -> List[str]:
        """导入时解决"名单里的人却占着某个位次"（**名单优先**）；返回一句人类可读的说明。

        做两件事：
        ① 把她从**所有班次**的位置上摘掉（**留空洞、不左移** —— 位次是正式概念，
           与 `set_detached` 那条路的"紧凑化"不同：这里动的是**导入进来的原始数据**，
           不该顺手把别人的位次挪一格）；
        ② **解除那些位次的锁** —— 她都不在那儿了，留一个"锁住这一格"的锁只会让自动入宿
           永远填不进它（用户会看到"这一格空着却没人住"），语义上也说不通。

        ⚠️ 这也是**行为变化**：在此之前**导入根本不摘人**（摘人只发生在显式调 `set_detached`
        的时候），于是一份同时带 `detached` 与占位的文件会让"她在名单里 ↔ 她在岗算速率"
        自相矛盾（`bench_names()` 把她列进"不在基建"，而引擎按在岗算）。
        """
        names = set(self.detached)
        hit_names: List[str] = []
        touched = 0
        for i in range(len(self.schedule.shifts)):
            facs = self.facilities_of(i)
            hit = False
            for f in facs:
                specs = _seat_specs(f)
                kept: List[object] = []
                cleared = False
                for idx, spec in enumerate(specs):
                    who = _spec_name(spec)
                    if who and who in names:
                        if who not in hit_names:
                            hit_names.append(who)
                        # 先解这一位的锁（`_set_lock` 要按"摘人之前"的占位找她是谁），
                        # 再把这一格留成空洞。
                        if f.get("manual"):
                            _set_lock(f, idx, False)
                        kept.append("")
                        cleared = True
                    else:
                        kept.append(spec)
                if cleared:
                    hit = True
                    _write_seats(f, kept)
            if hit:
                self.schedule = self.schedule.replaced_shift(i, facs)
                touched += 1
        if not hit_names:
            return []
        note = (f"不在基建名单优先：{'、'.join(hit_names)} 同时出现在位置上 ⇒ 已从 "
                f"{touched} 个班次里摘掉并解除该位次的锁")
        if self.loaded is not None:
            self.loaded.notes.append(note)
        return [note]

    # ================================================================ 重算
    @staticmethod
    def _world_digest(world) -> tuple:
        """一份布局的**内容摘要**（用它判"这一班变没变"）。

        ⚠️ 为什么不只用 `id(world)`：改练度（`set_training`）之类是**就地改**干员对象的，
        对象身份不变 —— 只比身份会以为"这一段没变"，于是拿旧轨迹当种子、数值静默出错。
        摘要按内容算（房间/等级/进驻者/练度/副手/**容量/氛围/位次/手动台账**），
        一次重算只算几遍，代价可忽略。

        ⚠️ **摘要必须覆盖"引擎读得到的每一个输入"**：漏一项 ⇒ 只改那一项时切点落错位置、
        复用旧前缀、**给出旧结果**。曾经漏掉的就是下面这五项（实测：单班 2 小时只改宿舍
        氛围 1000→5000，增量给 9.8、全量给 13.0）。加字段时先问一句
        "`build_base_layout` 会读它吗"，会读就得进摘要。
        """
        return tuple(
            (f.display_name, int(getattr(f, "level", 0) or 0), bool(getattr(f, "enabled", True)),
             tuple((o.name, int(getattr(o, "elite", 0) or 0), int(getattr(o, "level", 0) or 0))
                   for o in f.operators),
             tuple(o.name for o in f.deputies),
             # ---- 下面五项是 2026-10 补进摘要的（漏它们 ⇒ 增量重算静默复用旧前缀）
             int(getattr(f, "capacity", 0) or 0),
             getattr(f, "atmosphere", None),
             # 位次：同样几个人排在别的格子上，`slots` 空洞形状也变了（`operators` 是**紧凑**的，
             # 看不出空洞），而位次会影响闲置入宿的锁定区与手动台账的语义
             tuple((int(i), o.name, int(getattr(o, "elite", 0) or 0),
                    int(getattr(o, "level", 0) or 0))
                   for i, o in sorted(f.slot_map().items())),
             tuple(sorted(read_manual(f).slots)),      # 手动钉住的位次
             tuple(sorted(read_manual(f).names)))      # 手动放进去的人
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
            int(self.idle_protected_slots), tuple(sorted(self.idle_blacklist)),
            tuple(sorted((str(k), bool(v)) for k, v in self.idle_globals.items())),
        )
        per_shift = []
        for i, s in enumerate(sch.shifts):
            overrides = tuple(sorted(repr(e) for e in self.entry_per_shift
                                     if int(getattr(e, "key", 0) or 0) == i + 1))
            per_shift.append((self._world_digest(s.world), overrides))
        cells: Dict[tuple, list] = {}
        for (cyc, shf, name), val in self.idle_entries.items():
            cells.setdefault((cyc, shf), []).append((name, bool(val)))
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

        ⚠️ **手动锁优先（用户裁决 2026-10，Q6）**：名单里的人若**已被手动锁在某个位次**上，
        本方法**拒绝执行**并抛 `ValueError`（消息里写明她被锁在第几班哪一间的第几位）。
        理由：手动锁的目的就是"把干员放回基建内"，所以"锁着 ↔ 在名单里"这个状态**不允许被
        造出来** —— 与其造出来再挑一个赢家（选哪个都会让人意外），不如让用户先解锁。
        （**导入**那条路不拒绝、而是"**名单优先**"地摘人 + 解锁，见 `_resolve_imported_detached`。）
        """
        out: List[str] = []
        for n in (names or []):
            n = str(n).strip()
            if n and n not in out:
                out.append(n)
        blocked = [(n, seats) for n, seats in
                   ((n, self.locked_seats_of(n)) for n in out) if seats]
        if blocked:
            detail = "；".join(
                f"{n} 已被手动锁在 " + "、".join(
                    f"第 {si + 1} 班 {label} 第 {slot + 1} 位"
                    for si, _fi, label, slot in seats)
                for n, seats in blocked)
            raise ValueError(f"无法加入「不在基建」名单：{detail}。请先在「闲置入宿」里解锁。")
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

        ⚠️ 布局里的占位有两种写法：`operators`（紧凑）与 `slots`（有位次空洞）——
        **必须按位次读**（`_seat_specs` 认 `slots` 优先），只读 `operators` 会在
        `slots` 型设施上**摘不动人**（她一边在名单里、一边还在岗，破坏不变式）。
        ⚠️ 干员 spec 可能是**名字字符串**，也可能是**带心情/练度的对象**
        （`{"name": "泡泡", "mood": 10}`，场景 JSON 允许）—— 两种都要认。
        曾经直接 `n not in wanted`，遇到对象写法会 `TypeError: unhashable type: 'dict'`
        （2026-09 由"面板行序"用例暴露出来）。
        """
        if self.schedule is None or not names:
            return 0
        wanted = set(names)

        touched = 0
        for i in range(len(self.schedule.shifts)):
            facs = self.facilities_of(i)
            hit = False
            for f in facs:
                specs = _seat_specs(f)
                kept = [s for s in specs if _spec_name(s) not in wanted]
                if len(kept) != len(specs):
                    hit = True
                    _write_seats(f, kept)
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
                # ⚠️ 只剩"参不参与"（`enabled`）：手动入宿是**布局编辑**（看板 / 「干员与心情」），
                #    不再有 `target` / `dorm` / `slot` 那种第二套写法。
                "per_operator": [
                    {"name": n, "enabled": use}
                    for n, use in self.idle_globals.items()] +
                    [{"name": n, "enabled": use, "cycle": c, "shift": s}
                     for (c, s, n), use in self.idle_entries.items()],            },
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
        """改某个班次某间房的**进驻干员**（`operators` 里的空串 = 该位留空）。

        ⚠️ 位次语义：`operators[i]` 写的就是**第 i+1 位**。空串**留空洞、不左移**
        （"清空第 1 位"不会把后面的人往前挪），导出时写成 `slots: ["", …]`。

        **手动编辑逻辑**：调用方传进来的整份列表都被当作"人写的"——
        传进来的**整段位次**（**含被清空的那些**）与被写进去的人一起记进手动台账
        （`_write_manual`，原来那份台账整份作废）。自动入宿从此不占这些位次、不换这些人；
        **手动清空的位次从此"保持空着"**（2026-10 与 `set_facility_slots` 统一了这条口径，
        见 `04-特殊机制.md` 第 30 条）。导入**不打标**（只有这个入口与 `set_facility_slots` 会打）。

        ⚠️ 传空列表（`[]`）＝**清空整间房**：位次"到过的长度"塌成 0，于是**一个位次都不上锁**
        —— 相当于把整间房交还给自动入宿。要"锁住一整间空房"，请逐位用 `set_seat_lock`。
        """
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        names = [str(n) if n else "" for n in operators]
        fac = dict(facs[facility_index])
        _write_seats(fac, names)
        # ⚠️ `touched=len(names)` ⇒ **清空即上锁**。只把"有名字的位次"记进台账的话，
        #    手动清空的格子会被自动入宿立刻填上 —— 而文档（`04` 第 30 条 / AGENTS 坑 17）
        #    写的是"手动清空的位次**保持空着**"：旧实现在这条上与文档相反，且两条写入口
        #    （`set_slots` 解锁 / `set_facility_slots` 上锁）口径不一致。
        _write_manual(fac, raw_slots=[i for i, n in enumerate(names) if n],
                      raw_names=[n for n in names if n], touched=len(names))
        facs[facility_index] = fac
        self.replace_facilities(shift_index, facs)

    def set_facility_slots(self, shift_index: int, facility_index: int,
                           slots: Sequence[Optional[str]]) -> None:
        """**带空洞的逐位写入**（界面的看板/表格走这条）：`slots[i]` = 第 i+1 位的干员名或 `None`。

        与 `set_slots` 的区别是**不动没提到的位次**：传进来的每一位按原样写
        （`None`/空串 = 该位留空），后面的位次与它们的手动标记保持原样。
        手动台账：这些位次被标成"人写的"，写了名字的连人一起标（`_write_manual`）。
        """
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        fac = dict(facs[facility_index])
        current = _seat_values(fac)
        merged = list(current)
        for i, spec in enumerate(slots):
            value = str(spec) if spec else ""
            while len(merged) <= i:
                merged.append("")
            merged[i] = value
        _write_seats(fac, merged)
        _write_manual(fac, raw_slots=[i for i, n in enumerate(merged) if n],
                      raw_names=[n for n in merged if n], touched=len(slots))
        facs[facility_index] = fac
        self.replace_facilities(shift_index, facs)

    def set_seat_lock(self, shift_index: int, facility_index: int, slot: int,
                      locked: bool = True) -> None:
        """给某个班次某间房的**某一位**单独上锁 / 解锁（**不动**这一位坐的是谁）。

        这是「手动入宿」里**唯一只改锁、不改布局**的入口 —— 界面上那个 ☑ 走它。

        · `locked=True`（上锁）：该位次进手动台账的 `slots` ⇒ 自动入宿**既不占它、也不换
          里面的人**；若该位是空的，效果就是"这一格**保持空着**"。
        · `locked=False`（解锁）：把该位次从 `slots` 摘掉，**并且**把这一位的人从 `names`
          摘掉（否则裁决点第 1 层会继续按"这个人被钉住"把她护着，解锁看起来没生效）。

        ⚠️ 位次必须在**容量**内（越界抛 `ValueError`）；容量为 0 的设施（活动室）没有可锁的位。
        ⚠️ 与"改动布局"无关：本方法**不碰** `slots`/`operators` 的占位（谁坐在哪不变）。
        """
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        fac = dict(facs[facility_index])
        cap = _capacity_of_dict(fac)
        if not (0 <= int(slot) < cap):
            raise ValueError(f"该设施没有第 {int(slot) + 1} 位（容量 {cap}）")
        _set_lock(fac, int(slot), bool(locked))
        facs[facility_index] = fac
        self.replace_facilities(shift_index, facs)

    def clear_seat_locks(self, shift_index: Optional[int] = None) -> int:
        """**全部解锁**（逃生门）：整份清掉手动台账。返回动过几个班次。

        缺省＝**这份排班的所有班次**；传了 `shift_index` 就只清那一班。
        用途很具体："我导入了一份带锁的排班，想把它**全部交还**给自动入宿"。

        ⚠️ 它连 `names`（"这个人是我手动放的"）**一起清** —— 只清 `slots` 的话，
        `_seat_verdict` 第 1 层仍会按 `pins_name` 把人钉住，用户会以为"解锁失败了"。
        """
        if self.schedule is None:
            return 0
        n = len(self.schedule.shifts)
        wanted = list(range(n)) if shift_index is None else [int(shift_index)]
        touched = 0
        for i in wanted:
            if not (0 <= i < n):
                continue
            facs = self.facilities_of(i)
            hit = False
            for f in facs:
                if f.get("manual"):
                    f.pop("manual", None)
                    hit = True
            if hit:
                self.schedule = self.schedule.replaced_shift(i, facs)
                touched += 1
        if touched:
            self.recompute()
        return touched

    def locked_seats_of(self, name: str) -> List[Tuple[int, int, str, int]]:
        """某人被**手动锁住**的位置 → `[(班次下标 0 基, 设施下标, 设施显示名, 位次 0 基), …]`。

        判据与 `rules._seat_verdict` 的第 1 层**逐字一致**：她所在的位次被钉住
        （`ManualLedger.pins_slot`）**或**她的名字被钉住（`pins_name`）。空表 = 她没被锁在
        任何地方。谁在用：`set_detached` 的拒绝提示、界面"她已被手动锁在 …"的文案。
        """
        out: List[Tuple[int, int, str, int]] = []
        if self.schedule is None or not name:
            return out
        for i, shift in enumerate(self.schedule.shifts):
            for fi, fac in enumerate(shift.world.facilities):
                idx = fac.slot_of(name)
                if idx is None:
                    continue
                led = read_manual(fac)
                if led.pins_slot(idx) or led.pins_name(name):
                    out.append((i, fi, fac.display_name, idx))
        return out

    def set_room_level(self, shift_index: int, facility_index: int, level: int) -> None:
        """改某个班次某间房的等级（容量随之变化）。

        ⚠️ 容量变小 ⇒ **越界的位次与它的手动标记一起丢掉**（Q37 的裁决）：
        数据里不保留"看不见的第 7 位"，否则导出/再导入会让位次与容量对不上。
        """
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        fac = dict(facs[facility_index])
        fac["level"] = int(level)
        cap = _capacity_of_dict(fac)
        if cap:
            fac = _trim_to_capacity(fac, cap)
        facs[facility_index] = fac
        self.replace_facilities(shift_index, facs)

    def replace_facilities(self, shift_index: int, facilities: List[dict]) -> None:
        """整份替换某班次的布局，然后重算。"""
        if self.schedule is None:
            raise ValueError("尚未载入排班")
        self.schedule = self.schedule.replaced_shift(shift_index, facilities)
        self.recompute()

    def facilities_of(self, shift_index: int) -> List[dict]:
        """某班次布局的**可改副本**（`{"type","level","slots"/"operators","manual",...}` 形式）。

        占位按**位次**给出：有空洞时 `slots`（数组长度 = 到过的最大位次，空槽为 `""`），
        没有空洞时仍是紧凑的 `operators`。调用方拿到的是一份**独立副本**，改完要
        `replace_facilities` 写回。

        ⚠️ **两种写法互斥、不再无条件塞一个空的 `operators`**：`slots` 型设施过去会同时拿到
        `slots`（真的）与 `operators: []`（假的），而读的人大多只读后者 ⇒ 静默漏人
        （练度 / 填池 / 进名单 / 面板表格都中过这一枪）。读位次请用 `_seat_specs`
        或 `models.facility_occupancy`，别再自己 `f.get("operators")`。
        """
        if self.schedule is None:
            return []
        out: List[dict] = []
        for f in self.schedule.shifts[shift_index].facilities:
            copy = dict(f)
            if isinstance(copy.get("slots"), (list, tuple)):
                copy["slots"] = list(copy["slots"])          # 位次数组：独立副本
            else:
                copy["operators"] = list(copy.get("operators") or [])
            out.append(copy)
        return out

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
                new_ops: List[object] = []
                for spec in _seat_specs(f):        # ⚠️ 按位次读（`slots` 优先），不是只读 operators
                    name = _spec_name(spec)
                    if not name:                   # 空槽：原样留空，别把人挪进来
                        new_ops.append("")
                        continue
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
                _write_seats(f, new_ops)
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
        wanted = (list(range(len(self.schedule.shifts)))
                  if shift_index is None else [shift_index])
        used = set()
        for idx in wanted:
            for f in self.schedule.shifts[idx].world.facilities:
                for o in f.operators:
                    used.add(o.name)

        def _take():
            """按池的顺序取下一个没用过的人（池空了 → `None`），顺带按满练口径转 spec。

            ⚠️ 旧实现的守卫是 `len(used) < len(pool)`（"已就位总人数" vs "池大小"）——
            两者根本不是一回事：场内已有 2 人、池里 2 人时它**一个都不填**，
            却仍然 `replaced_shift` 一遍（还往 slots 型设施里塞了一个空 `operators`）。
            这里只认"池里还有没有没用过的人"。
            """
            for p in pool:
                if p["name"] not in used:
                    used.add(p["name"])
                    if int(p.get("elite", 2)) != 2 or int(p.get("level", 30)) != 30:
                        return {"name": p["name"], "elite": int(p.get("elite", 2)),
                                "level": int(p.get("level", 30))}
                    return p["name"]
            return None

        filled = 0
        for idx in wanted:
            facs = self.facilities_of(idx)
            touched = False
            for f in facs:
                cap = self._capacity_of(f)
                if cap <= 0:
                    continue
                specs = _seat_specs(f)          # ⚠️ 按位次读（`slots` 优先）
                changed_here = False
                # ① 先填**空位**（含中间空洞，竖向正序）：位次是正式概念，别跳过空位往后面放人
                for i in range(min(len(specs), cap)):
                    if specs[i]:
                        continue
                    spec = _take()
                    if spec is None:
                        break
                    specs[i] = spec
                    filled += 1
                    touched = changed_here = True
                # ② 还有空容量就从尾巴续着放
                placed = sum(1 for s in specs if s)
                while placed < cap:
                    spec = _take()
                    if spec is None:
                        break
                    specs.append(spec)
                    placed += 1
                    filled += 1
                    touched = changed_here = True
                if changed_here:
                    _write_seats(f, specs)
            if touched:
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
        """把界面的逐次设置转成 `[IdleToDormEntry, ...]`——**只列"改过默认"的**
        （勾掉不参与的人）；没人改过就返回 `None`（＝全都参与）。

        ⚠️ 三层解耦后这里只剩**"参不参与"**：手动入宿不走这条路，它写的是**布局快照 +
        手动台账**（看板 / 「干员与心情」→ `set_slots` / `set_facility_slots`）。
        """
        out: List[IdleToDormEntry] = []
        for name, use in self.idle_globals.items():
            if use:
                continue
            out.append(IdleToDormEntry(name=name, enabled=False))
        for (cyc, shf, n), use in self.idle_entries.items():
            if use:
                continue
            out.append(IdleToDormEntry(name=n, enabled=False, cycle=cyc, shift=shf))
        return out or None

    def idle_groups(self, cycles: Optional[int] = None,
                    entries: Optional[dict] = None) -> List[tuple]:
        """按时间排序的**逐次入宿表** → `[(标题, (周期, 班次), [行, ...], 起点, 终点), ...]`。

        `行 = (干员, 心情显示值, 位置, 参与, None, [])` —— **位置列只读、不可选**：
        三层解耦后手动入宿归**布局编辑**（看板 / 「干员与心情」写班次快照 + 手动台账），
        闲置入宿面板只剩"这一位参不参与"。
        只列出**真的有候选**的那几次（心情跨班跨周期连续 ⇒ 每次谁没满都不一样）。
        `cycles` 同样夹到 `1 ~ MAX_CYCLES`（与 `set_cycles` 一个口径）。

        **一个换班执行点一组**（文档 §3/§14）：真实班初一组，长班的每个内部换班点各一组
        （标题写成 `第 1 周期 · 第 2 班（12h 内部换班）`）；`起点`/`终点` 是绝对小时，
        界面据它渲染时钟区间。⚠️ **同班各执行点共用同一份逐人设置** —— 组里的
        `(周期, 班次, 干员)` 键是同一个，所以面板改任一组会同步影响同班其他执行点。

        **候选口径与引擎同一份**（`rules.apply_idle_to_dorm`）：该班
        **完全没有出现在任何设施**里、心情 < 24、且不在**黑名单**里的人；
        行序＝(心情↑, 名字↑) —— 候选是按这个顺序处理的，所以表里的行序就是引擎的安排顺序。
        ⚠️ 班初在加工站 / 训练室的人、副手、所有上班与在宿舍的人**都不是候选**。
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
            cands = []                       # [(心情, 名字, 位置)]
            for name in traj.names:
                if name in blocked:
                    continue                 # 黑名单：不进初始候选
                if shift.world.facility_of(name) is not None:
                    continue                 # 该班出现在任何设施里 ⇒ 不是候选
                mood = mood_of[name]
                if mood >= MOOD_MAX:
                    continue                 # 心情满 24，不需要恢复
                where = "不在基建" if name in bench else "未排班"
                cands.append((mood, name, where))
            if not cands:
                continue
            cands.sort(key=lambda row: (row[0], row[1]))
            rows = []
            for mood, name, where in cands:
                use = entries.get((cyc, i + 1, name))
                if use is None:
                    use = self.idle_globals.get(name, True)
                note = traj.idle_note_at(name, t0) or ""
                rows.append((name, mood, where, use, note, []))
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


# ---------------------------------------------------------------- 布局写入原语
def _spec_name(spec) -> str:
    """干员 spec（`"名字"` 或 `{"name": ..., "elite": ...}` 对象）→ 名字。"""
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        return str(spec.get("name") or "")
    return str(spec or "")


def _seat_specs(fac: dict) -> List[object]:
    """设施描述 dict → **按位次对齐的 spec 列表**（空槽写 `""`；spec 保留对象写法）。

    有 `slots` 用 `slots`（空槽 = `null`/`""`），否则用紧凑的 `operators`（位次 = 下标）。
    ⚠️ "所有干员名都排在前面"是导入数据的常态，所以紧凑列表在这里就是"从第 1 位起连续"。
    ⚠️ **`slots` 优先于 `operators`**（`models.facility_occupancy` 的口径）：只读
    `operators` 会在"留过空洞"的设施上**静默漏掉全部住户** —— 练度（`set_training`）、
    填池（`fill_from_pool`）、进名单（`_remove_from_slots`）三条写入口都这么失效过。
    """
    raw = fac.get("slots")
    # ⚠️ **只有列表型 `slots` 才是"按位次的占位"**：数字型 `slots` 是**历史的容量覆盖**
    #    （v4 蓝图的 `dorm_beds` 也走它，见 `models.facility_occupancy` 与
    #    `store.layers.build_base_layout`），当占位数组迭代会直接 `TypeError`
    #    —— 旧实现只判 `is None`，是个潜伏 bug：一旦有人对"数字 slots"的设施调它就炸。
    if not isinstance(raw, (list, tuple)):
        raw = fac.get("operators") or []
    return [s if s else "" for s in raw]


def _seat_values(fac: dict) -> List[str]:
    """`_seat_specs` 的**名字版**（空槽写 `""`）—— 手动台账与容量裁剪都按名字算。"""
    return [_spec_name(s) for s in _seat_specs(fac)]


def _write_seats(fac: dict, values: Sequence[object]) -> None:
    """把**按位次对齐的 spec 列表**写回设施描述（`_seat_specs` 的逆）。

    写法（Q25/Q32 的裁决）：**尾部没有空槽 ⇒ 紧凑的 `operators`**（老文件、老读法不受影响）；
    只要**中间或开头留了空洞** ⇒ `slots`（真正的空槽写 `null`）。

    ⚠️ **两份写法互斥**：先把 `slots` 与 `operators` **都**摘掉，再写其中一份。
    只 pop 一份的话，过时的那份会被 `facility_occupancy` 优先读走 ⇒
    **"改人看着成功、世界一点没变"**（修过的 bug：`set_slots` 在曾留空洞的设施上是静默 no-op）。
    ⚠️ **不裁剪尾部空槽**：`[null, null, "丙"]` 这种前面留空必须原样写下来 —— 位次是正式的
    概念，裁掉头部空洞会让"第 3 位的丙"变成"第 1 位的丙"（手动台账、`dorm_state.reach` 全跟着错）。
    内容全空时按紧凑写法写空数组。
    """
    values = [v if v else "" for v in values]
    fac.pop("slots", None)
    fac.pop("operators", None)
    reached = [i for i, v in enumerate(values) if v]
    if not reached:
        fac["operators"] = []
        return
    width = reached[-1] + 1
    trimmed = values[:width]
    if any(not v for v in trimmed):
        fac["slots"] = [v or None for v in trimmed]
    else:
        fac["operators"] = list(trimmed)


# ---------------------------------------------------------------- 公开的位次读写（给 ui/ 与脚本用）
def seat_specs(fac: dict) -> List[object]:
    """设施描述 → **按位次对齐的 spec 列表**（空槽 `""`；spec 保留对象写法）。

    ⚠️ 这是 `ui/` 与脚本该用的**公开**入口：位次口径只有一套（**`slots` 优先**），
    而 `_seat_specs` 是下划线名、不该被 UI import（见 `tests/test_layers.py` 的分层意图）。
    """
    return _seat_specs(fac)


def seat_values(fac: dict) -> List[str]:
    """`seat_specs` 的**名字版**（空槽 `""`）—— 界面表格按它逐格渲染。"""
    return _seat_values(fac)


def write_seats(fac: dict, values: Sequence[object]) -> None:
    """把**按位次对齐的 spec 列表**写回设施描述（`seat_specs` 的逆）。

    两种写法（紧凑 `operators` / 带空洞 `slots`）**互斥**，且**不左移**（空洞原样保留）。
    `ui/batch.py` 的写回**必须**走它：否则会同时留下旧的 `slots` 与新写的 `operators`，
    而 `models.facility_occupancy` 里 `slots` 优先 ⇒ **改动静默失效**（修过的 bug）。
    """
    _write_seats(fac, values)


def _write_manual(fac: dict, *, raw_slots=(), raw_names=(), touched: Optional[int] = None) -> None:
    """写设施描述的 `manual` 台账（`store.session.set_slots` / `set_facility_slots` 用）。

    规则（Q2/Q12/Q13）：
      · 传进来的位次（前 `touched` 个）一律进 `slots`＝"这一位归人管"；
      · 写了名字的进 `names`＝"这个人是人放的"；
      · 同一份数据里**已经被写掉的人**（不再出现在 `values` 里）从 `names` 摘掉；
      · 台账空了就把 `manual` 键删掉（导出保持干净）。

    ⚠️ **位次的上界是"容量"，不是"占位数组的长度"**：`_write_seats` 会**裁掉尾部空槽**
    （`["甲","乙",""]` → `["甲","乙"]`），若 `limit` 跟着数组长度算，"清空最后一个有人位"
    就连那一位都不存在了 ⇒ **"清空即上锁"落不到它身上**（实测过）。容量才是"这一位还能不能
    存在"的正式上界（`_capacity_of_dict` 与 `Facility.capacity` 同一套规则）。
    """
    cap = max(_capacity_of_dict(fac), len(_seat_values(fac)))
    limit = cap if touched is None else min(int(touched), cap)
    slots = {int(i) for i in raw_slots if 0 <= int(i) < limit}
    slots |= set(range(limit)) if touched is not None else set()
    names = {str(n) for n in raw_names if n}
    live = {v for v in _seat_values(fac) if v}
    names &= live
    _put_manual(fac, slots, names)


def _put_manual(fac: dict, slots, names) -> None:
    """把 `(位次集合, 人名集合)` 写进设施描述的 `manual`；两个都空就把整份台账删掉。

    ⚠️ **全项目只有这一个地方写 `fac["manual"]`**（两条布局写入口、逐位锁/解锁都走它），
    这样"空台账不留痕"（导出保持干净）这条口径不会在别处被写漏。
    """
    slots = sorted({int(i) for i in slots})
    names = sorted({str(n) for n in names if n})
    if slots or names:
        fac["manual"] = {"slots": slots, "names": names}
    else:
        fac.pop("manual", None)


def _set_lock(fac: dict, slot: int, locked: bool) -> None:
    """**只改某一位的锁** —— 不动这份名单的其它位次，也不动这一位坐的是谁。

    · `locked=True`：该位次进 `slots`＝"这一位归人管"（自动入宿既不占它、也不换里面的人；
      该位是空的，就是"保持空着"）。
    · `locked=False`：把该位次从 `slots` 摘掉，**并且**把这一位的人从 `names` 摘掉 ——
      否则 `_seat_verdict` 第 1 层会继续按 `pins_name` 把她钉住，"解锁"看起来没生效。

    ⚠️ **上锁不往 `names` 里加人**：锁是"位置级"的意图；`names` 表达的是"这个人是我手动放的"，
    由 `set_slots` / `set_facility_slots`（真写了人）产生 —— 别让"勾一个框"顺带改变
    "这个人归我管"的语义（那会让"她以后换到别的位次也仍然被钉住"）。
    """
    led = dict(fac.get("manual") or {})
    slots = {int(i) for i in (led.get("slots") or [])}
    names = {str(n) for n in (led.get("names") or [])}
    slot = int(slot)
    seats = _seat_values(fac)
    who = seats[slot] if 0 <= slot < len(seats) else ""
    if locked:
        slots.add(slot)
    else:
        slots.discard(slot)
        if who:
            names.discard(who)
    names &= {v for v in seats if v}          # 已经被写掉的人不留名字标记
    _put_manual(fac, slots, names)


def _capacity_of_dict(fac: dict) -> int:
    """某条设施描述（dict）的容量：`capacity` 覆盖优先（历史的数字型 `slots` 也认），
    否则按等级查容量表（宿舍各等级都是 5）。"""
    override = fac.get("capacity")
    if override is None:
        legacy = fac.get("slots")
        if isinstance(legacy, (int, float)) and not isinstance(legacy, bool):
            override = legacy
    if override is not None:
        return int(override)
    from mood_soc.config import facility_slots, parse_facility_type

    ftype = parse_facility_type(fac.get("type"))
    if ftype is None:
        return 0
    return int(facility_slots(ftype, int(fac.get("level", 1))))


def _trim_to_capacity(fac: dict, capacity: int) -> dict:
    """容量变小 ⇒ **越界的空位次与它的手动标记一起丢掉**（Q37 的裁决）。

    ⚠️ **只丢空位、不丢人**：越界格子里还住着人时**保留**（那正是"超容量"这种布局问题，
    要交给自检报出来，不能在用户没看见的时候把人悄悄删掉 —— 曾经的实现就是那样）。
    ⚠️ 用 `_seat_specs`（带对象写法）而不是 `_seat_values`（只有名字）：否则裁一次容量
    就把这批人的练度 `{"elite": 1, "level": 30}` 悄悄丢成纯名字。
    """
    values = _seat_specs(fac)
    if len(values) <= capacity:
        return fac
    drop = [i for i in range(capacity, len(values)) if not values[i]]
    if not drop:
        return fac
    keep = values[:capacity]
    ledger = dict(fac.get("manual") or {})
    if ledger:
        ledger["slots"] = [i for i in (ledger.get("slots") or []) if int(i) < capacity]
    out = {k: v for k, v in fac.items() if k not in ("slots", "operators")}
    _write_seats(out, keep)
    if ledger:
        # ⚠️ **只有 `names` 的台账也要留下来**：旧实现的条件是"`ledger["slots"]` 非空"，
        #    于是"手动放的人 + 该位次恰好没被锁"这份台账会在缩容时被整份丢掉
        #    （等于悄悄把她解锁了）。空台账由 `_put_manual` 统一负责删键。
        _put_manual(out, ledger["slots"], ledger.get("names") or ())
    return out


__all__ = [
    "Session", "Validation", "ValidationIssue",
    "DEFAULT_OPERATORS",
    # 位次读写的公开入口（`ui/` 与脚本用；位次口径只有一套：`slots` 优先）
    "seat_specs", "seat_values", "write_seats",
]
