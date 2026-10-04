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
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from data.skills_data import DEFAULT_OPERATORS
from mood_soc.battery import to_decimal
from mood_soc.config import MOOD_MAX, FacilityType
from mood_soc.models import (IdleToDormConfig, IdleToDormEntry, build_entry_event_config,
                             normalize_entry_when, read_manual)
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
    #: **导入原样**（任务 A）：三条 `load_*` 在**装配完成、任何用户编辑之前**取的一份
    #: **深拷贝布局快照**（每个班次一份；与 `schedule` **不共享对象**）。
    #: 用途＝「取消指定 ⇒ 回导入原位」与「恢复默认（回到导入时）」的基准；
    #: **不写进 JSON、不动导出格式、不进 `api/` 协议**（只活在会话里）。
    #: ⚠️ 时机＝导入层"名单优先"摘人**之后**（用户说的"导入时"＝**他刚导入看到的样子**）。
    #: ⚠️ 别改读 `loaded.schedule`：它与 `schedule` 曾经是**同一个对象**（不设防，踩过）。
    imported_layouts: List[List[dict]] = field(default_factory=list)

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
    @staticmethod
    def _scenario_payload(data: Any) -> dict:
        """从一份**已解析的 JSON** 里取出"本工具场景"那一层（取不到就是空 dict）。

        **只认场景格式**（`store.sources.detect_format` 说了算，本项目不含第二份格式判断）：
        顶层就是场景；v4 蓝图那种"场景嵌在 `layout.scenario`"的也认。
        `detect_format` 对本函数关心的输入只会返回 `scenario` 或 `plan_compute_v4`，
        所以它不会抛异常；真抛了（畸形文件）也只是**没有这两组设置**，不影响导入本身。
        """
        if not isinstance(data, dict):
            return {}
        from .sources import detect_format
        try:
            kind = detect_format(data)
        except Exception:                      # noqa: BLE001 —— 格式判不出来 = 没有这两组设置
            return {}
        if kind == "scenario":
            return data
        if kind == "plan_compute_v4":
            layout = data.get("layout")
            scen = layout.get("scenario") if isinstance(layout, dict) else None
            return scen if isinstance(scen, dict) else {}
        return {}

    def _read_scenario_moods(self, data: Any) -> Optional[Tuple[Dict[str, Decimal], List["MoodSetEvent"]]]:
        """读**场景 JSON** 的两个"心情设置"顶层键：`initial_moods` / `mood_events`。

        它们与 `facilities` / `entry_events` / `idle_to_dorm` / `initial_global` 同层，
        是本工具场景格式**本来就允许写**的两个键（`export_schedule` 会写出来，
        「导出 → 再导入 ⇒ 逐字段不变」靠的就是这里读回来）。

        返回 `None` ＝ **这份数据里一个都没写**（调用方保持原样清空）；
        写了（哪怕写成空对象/空数组）就返回整份 `({}, [])` 语义 ——
        "显式清空"与"没写"必须分得开，否则导出过的空设置会被当成没写、又退回默认值。

        ⚠️ **心里想的是 `initial_moods`，落点是 `Session.initial_moods`**：轨迹的**周期起点**
        取的就是它（缺省才用第一班布局里写的值）；`cycle=1` 且 `t=0` 的心情锚点在设置接口里
        本来就会被路由进 `initial_moods`（`set_mood_at`），两者语义一致。
        """
        scen = self._scenario_payload(data)
        if not scen:
            return None
        raw_moods = scen.get("initial_moods")
        raw_events = scen.get("mood_events")
        if raw_moods is None and raw_events is None:
            return None
        moods: Dict[str, Decimal] = {}
        if isinstance(raw_moods, dict):
            for name, value in raw_moods.items():
                if value is None:
                    continue
                try:
                    moods[str(name)] = to_decimal(value)
                except (ArithmeticError, ValueError):        # noqa: PERF203 —— 单个坏值不废整份
                    continue
        events: List["MoodSetEvent"] = []
        for item in (raw_events or []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            try:
                event = MoodSetEvent(name=str(item["name"]),
                                     cycle=int(item.get("cycle") or 1),
                                     t=to_decimal(item.get("t") or 0),
                                     mood=to_decimal(item["mood"]))
            except (ArithmeticError, KeyError, TypeError, ValueError):
                continue
            if event.cycle == 1 and event.t == ZERO:
                # 与 `set_mood_at` 同一条路由：第 1 周期 0:00 是"起点心情"，不是锚点
                moods.setdefault(event.name, event.mood)
                continue
            events.append(event)
        return moods, events

    @staticmethod
    def _load_json(path: Any) -> Any:
        """读一个 JSON 文件（读不了就返回 `{}`）—— 只给 `load_paths` 取场景那两组设置用。

        ⚠️ **不参与装配**（装配照旧只有 `store.sources.import_file` 一份实现）：
        这里只把"原始 JSON"拿来做一次**只读**取值；文件读不动/不是 JSON 时返回空，
        让 `load_schedule_ex` 去报那个**真正的**错（别在这里抢先抛，会盖掉原始报错）。
        """
        import json
        try:
            with open(Path(path), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def load_paths(self, paths: Sequence[Union[str, Path]]) -> "LoadedSchedule":
        """按文件集合装配排班（自动识别 4 种格式），并把文件里的设置同步进来。

        可能抛 `ValueError`（格式不认识 / 班次拼不起来）——调用方展示原因即可。
        """
        #: 场景格式的 `initial_moods` / `mood_events` 只有"原始 JSON"那一层有
        #: （`ImportResult` 不带它们）⇒ 这里在装配**之前**取出来（见 `_read_scenario_moods`）。
        scen_moods = [self._read_scenario_moods(self._load_json(p)) for p in paths]
        ld = load_schedule_ex(paths)
        self.loaded = ld
        self.schedule = ld.schedule
        self.initial_moods.clear()
        self.mood_events.clear()
        for got in scen_moods:
            if got is not None:
                self.initial_moods, self.mood_events = got
        self._sync_from_schedule(from_import=True)
        self._capture_import_layouts()             # ← 导入原样（名单优先之后、任何编辑之前）
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
        #: 场景格式的 `initial_moods` / `mood_events`（`export_schedule` 会写出来的那两个键）
        scen_moods = self._read_scenario_moods(data)
        self.initial_moods.clear()
        self.mood_events.clear()
        if scen_moods is not None:
            self.initial_moods, self.mood_events = scen_moods

        self._sync_from_schedule(from_import=True)
        self._capture_import_layouts()             # ← 导入原样（名单优先之后、任何编辑之前）
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
        # 闲置入宿：**「文件级那一组」整组继承 / 整组不继承**（2026-10 修，A4 工单）。
        # ⚠️ 旧写法把这行放在 `if not apply_file_settings:` **之外**，且无条件用
        #    `True if idle_to_dorm is None else …` 覆盖 —— 于是
        #    `load_data(data, apply_file_settings=True)` 只继承了**其余三项**
        #    （锁定位置数 / 黑名单 / 逐人），开关却被拍回 `True` ⇒ **半继承**
        #    （文件里明明写着 `"enabled": false`），与 `op_load_json` 的文档口径
        #    （"给了 `apply_file_settings: true` 才按文件里那个 `idle_to_dorm` 走"）
        #    以及 `load_paths` 的行为都对不上。
        # ⚠️ 优先序：**显式参数 > 文件 > 本入口默认（开）** —— `idle_to_dorm=False` 照样说了算。
        if idle_to_dorm is not None:
            self.idle_to_dorm = bool(idle_to_dorm)
        elif not apply_file_settings:
            self.idle_to_dorm = True
        # （`apply_file_settings=True` 且没显式给参数 ⇒ 保留 `_sync_from_schedule` 从文件读来的值）
        if cycles is not None:
            self.set_cycles(int(cycles))
        self.recompute()
        return ld

    def load_layout(self, data: dict, hours=None, label: str = "班次 1") -> None:
        """直接给一份**布局 dict**（本工具场景格式）建一个单班排班。

        `data = {"facilities": [...]}`；可选顶层 `entry_events` / `idle_to_dorm` /
        `initial_global` / `detached` 与场景 JSON 完全同义（见 `store/layout.build_base_layout`）。
        `idle_to_dorm` 的解析口径只有一处（`Shift.__post_init__` →
        `models.build_idle_to_dorm_config`），这里**原样传原始值**、不另解析一遍。
        """
        shift = Shift(label=label, hours=to_decimal(hours if hours is not None else 24),
                      facilities=list(data.get("facilities") or []),
                      source="api:inline",
                      entry_events=None,
                      idle_to_dorm=data.get("idle_to_dorm"),
                      initial_global=dict(data.get("initial_global") or {}),
                      detached=list(data.get("detached") or []))
        self.schedule = Schedule([shift], to_decimal(hours if hours is not None else 24),
                                 detached=list(shift.world.detached or []))
        self.loaded = LoadedSchedule(schedule=self.schedule)
        #: 场景格式的 `initial_moods` / `mood_events`（单班布局同义，见 `load_paths`）
        scen_moods = self._read_scenario_moods(data)
        self.initial_moods.clear()
        self.mood_events.clear()
        if scen_moods is not None:
            self.initial_moods, self.mood_events = scen_moods
        self._sync_from_schedule(from_import=True)
        self._capture_import_layouts()             # ← 导入原样（名单优先之后、任何编辑之前）
        self.recompute()

    def _sync_from_schedule(self, *, from_import: bool = False) -> None:
        """把排班自带（场景 JSON 顶层）的设置同步到会话（导入后调一次）。

        `from_import=True`（三条 `load_*` 传 `True`）时额外做一件事：**名单优先** ——
        文件里同时写了 `detached` 与"这个人占着某个位次"时（老文件、手改文件都可能），
        把她从位置上摘掉、**并解除那位次的锁**，再往导入摘要里记一条（`LoadedSchedule.notes`）。

        ⚠️ **为什么导入"让步"、交互层却"拒绝"**：交互层拒绝是为了**不让用户造出**
        "锁着 + 在名单里"这个自相矛盾的状态（见 `set_detached`）；而导入是**数据**，
        报错会让老文件直接打不开 —— 两边口径不同是有意的（Q6 的 (d3) + (i)）。

        ⚠️ **本方法读的是"排班快照里那份设置"** ⇒「面板上设的值"碰名单"会不会被打回」
        完全取决于 `Schedule` 重建时有没有把全局设置搬过去。2026-10 之前 `Shift.__post_init__`
        **只搬 `facilities / initial_global / detached`**，于是「换心情」与「闲置入宿」两组
        都会在 `with_detached` 重建时退回默认值、再被这里读回会话（实测：面板设
        `(True, 9, ['甲'])` 碰一次名单 ⇒ `(True, 5, [])`）。**根因已修**（`Shift` 现在带
        `entry_events` / `idle_to_dorm` 两个字段并被四条重建路径原样搬运）⇒ 这里**不再需要**
        任何"只同步名单"的开关；「碰名单」也不再改动这两组设置。
        """
        if self.schedule is None:
            return
        self.detached = list(getattr(self.schedule, "detached", []) or [])
        if from_import:
            # —— 冻结「导入时出现过的干员名册」（A1 工单）——
            # ⚠️ **必须赶在 `_resolve_imported_detached()` 之前**：那一步会按"名单优先"
            #    把人从位次上摘掉，摘完名册就少了她（而名册的全部意义就是"她曾经在这份
            #    排班里出现过"，用户 2026-10 裁决"本班未排班也算存在"）。
            # ⚠️ 只在**导入**这条路上冻结：之后任何布局编辑都不再改它 —— 她被人顶掉 /
            #    被清空 / 被缩容挤掉之后照旧留在 `operator_names()` 里（心情表），
            #    于是 M15a 的"表里有、world 里没有 ⇒ 算存在"这条判据轮得到她。
            # ⚠️ 时机上 `self.schedule` 此刻还是**导入装配后、任何编辑前**的那一份
            #    （`load_*` 先 `_sync_from_schedule(from_import=True)` 再让用户编辑）。
            self.schedule = self.schedule.with_roster(self.schedule.operator_names())
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
        # ⚠️ **"没写 `enabled`" ≠ "写了 `false`"**（2026-10 修，A5 工单）：旧实现是
        #    `bool(getattr(idle, "enabled", True))` —— `getattr` 的缺省值救不了
        #    **属性存在但值是 `None`** 那种情况（`bool(None) == False`）。而
        #    `models.build_idle_to_dorm_config` 对"只写了 protected_slots / blacklist
        #    的配置对象"就是留一个 `enabled=None`（"未指定"）⇒ 会话把开关判成 **关**、
        #    引擎（`rules.apply_idle_to_dorm` 把 `None` 当**开**）却照旧结算
        #    ⇒ **同一份 world 两套结论**。全项目口径是"文件里没写这个键 ⇒ 结算；
        #    显式 `false` 才关"（`IdleToDormConfig.enabled` 缺省也是 `True`）。
        self.idle_to_dorm = getattr(idle, "enabled", None) is not False
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

    # ---------------------------------------------------------------- 导入原样
    def _capture_import_layouts(self) -> None:
        """把**"导入那一刻的布局"**固化成 `imported_layouts`（任务 A）。

        **时机**（三条 `load_*` 在 `_sync_from_schedule(from_import=True)` **之后**、`recompute()`
        之前调它）：用户说的"导入时"＝**他刚导入看到的样子** —— 而导入层有一条会改布局的
        规则（`_resolve_imported_detached`：文件里同时写了 `detached` 与占位 ⇒ **名单优先**，
        把人从位次上摘掉）。摘人**属于"导入原样"的一部分**（那正是他打开文件后第一眼看到的
        布局），所以快照取在它**之后**；`initial_moods` / 心情锚点等设置不在这份快照里
        （它们不是布局，各有各的"恢复导入值"入口）。

        ⚠️ **必须深拷贝**：`loaded.schedule` 与 `session.schedule` 曾经是**同一个对象**
        （12 条编辑入口都返回新 `Schedule`，所以它"顺带"还留着导入原样 —— 那是**不设防的
        巧合**，任何就地改都会毁掉它）。这里显式拷一份，从此与 `schedule` **不共享对象**。
        """
        if self.schedule is None:
            self.imported_layouts = []
            return
        self.imported_layouts = [deepcopy(list(shift.facilities))
                                 for shift in self.schedule.shifts]

    def imported_seat_of(self, shift_index: int, name: str) -> Optional[Tuple[int, int]]:
        """**导入时**她在这个班次里的 `(设施下标, 位次)`；查不到 ⇒ `None`。

        `None` 的语义是**"导入时她不在本班"**（那才是"本班未排班"）—— 与"她坐在第 0 位"
        必须分得开，所以**不返回 `(-1, -1)` 之类的哨兵**。
        ⚠️ 同一份导入数据里同名出现在多处（本不该有的数据）时取**竖向最先**的一处。
        """
        i = int(shift_index)
        if not name or not (0 <= i < len(self.imported_layouts)):
            return None
        for fi, fac in enumerate(self.imported_layouts[i]):
            for si, who in enumerate(_seat_values(fac)):
                if who and who == str(name):
                    return (fi, si)
        return None

    def imported_seat_occupant(self, shift_index: int, facility_index: int, slot: int) -> str:
        """**导入时这一格坐的是谁**；本来是空（或越界）⇒ `""`。"""
        i, fi, si = int(shift_index), int(facility_index), int(slot)
        if not (0 <= i < len(self.imported_layouts)):
            return ""
        layout = self.imported_layouts[i]
        if not (0 <= fi < len(layout)):
            return ""
        return _seat_at(layout[fi], si)

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
            # ⚠️ **导入名册**也要进指纹：它决定"心情表里有谁"（`operator_names()`），
            # 名册一变（新导入 / 换了排班）⇒ 轨迹的成员集合就变了，前缀**一段都不能复用**
            # （复用会让 `continue_from` 的 `names` 与旧轨迹对不上）。一律走整条重算。
            tuple(getattr(sch, "roster", None) or ()),
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

        ⚠️ **只动名册**（2026-10 修根因后）：本方法**不碰**任何自动化设置（换心情那 6 个字段 /
        闲置入宿那一组）—— 它们只写在 `Session` 上、从不回写排班快照，所以这里若再
        `_sync_from_schedule()` 一次，文件里写过的旧值就会把面板设置覆盖回去。详见方法末尾注释。

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
        # 把会话里那两份**权威**的自动化设置写回刚重建出来的快照（只在"本来就要重建"的
        # 这条路上做，不额外付代价）⇒ 快照与面板**逐项一致**，不再是"会话 9 / 快照 2"。
        self._push_automation_settings()
        # ⚠️ **这里不再 `_sync_from_schedule()`**（用户口径：碰名单只动名册）。
        #    `_sync_from_schedule` 是"**导入时**把文件里的设置读进会话"的那一次同步；
        #    在编辑路径上再调一次，语义就变成"用排班快照覆盖面板设置" —— 而面板上那两组
        #    自动化设置（换心情 / 闲置入宿）**只写在 Session 上、从不回写排班快照**
        #    （`ui/app.py::apply_idle_to_dorm` / `apply_entry_event`、
        #    `api/ops.py::op_set_idle_to_dorm` 都只写 `session.*`）⇒ 文件里写过 `idle_to_dorm`
        #    或 `entry_events` 时，碰一次名单就把面板设置整组打回文件旧值（实测：
        #    面板 `(True, 9, ['丙'])` + `(any, dorm, immediate)` → 碰名单后变回文件的
        #    `(True, 2, ['甲'])` + `(缪尔赛思, anywhere, wait)`）。
        #    `with_detached` 已经把名单写回 `Schedule` 与每个班的 `world`（这就是这次要同步的
        #    全部内容），会话里那份 `self.detached` 上面也已更新 ⇒ 没有别的要同步的。
        if recompute:
            self.recompute()

    def _push_automation_settings(self) -> None:
        """把会话里那两组自动化设置（**换心情 / 闲置入宿**）写进排班快照。

        ⚠️ 为什么要这一步：这两组设置**只写在 `Session` 上**（面板与 API 都只改 `session.*`），
        而排班快照里带着一份"导入时读到的"副本。快照一旦与面板不同（用户改过面板），
        两者就会**长期分叉**：随后任何一次 `Schedule` 重建（`with_detached` / `replaced_shift` …）
        搬的都是**快照里那份旧值** ⇒ 导出、`_world_digest`、下一次导入回填看到的都不是面板值。
        这里把会话那份写回快照（每个班次各一份深拷贝），让两边逐项一致。

        调用时机：只在"**本来就要重建 `Schedule`**"的编辑路径上（`set_detached`）——
        不额外付代价，也不改变任何现有调用点的语义（引擎照旧只读 `Session`，
        `recompute_inputs` 传的还是 `Session` 的值）。
        """
        sch = self.schedule
        if sch is None:
            return
        entries = [IdleToDormEntry(name=n, enabled=bool(use))
                   for n, use in (self.idle_globals or {}).items()]
        entries += [IdleToDormEntry(name=n, enabled=bool(use), cycle=c, shift=s)
                    for (c, s, n), use in (self.idle_entries or {}).items()]
        for sh in sch.shifts:
            idle = IdleToDormConfig(enabled=bool(self.idle_to_dorm),
                                    protected_slots=int(self.idle_protected_slots),
                                    blacklist=[str(n) for n in self.idle_blacklist],
                                    per_operator=deepcopy(entries))
            sh.idle_to_dorm = idle
            sh.world.idle_to_dorm = idle
            cfg = build_entry_event_config({
                "enabled": bool(self.entry_events), "swap_with": self.entry_swap_with,
                "scope": self.entry_scope, "restore_back": bool(self.entry_restore_back),
                "when": self.entry_when,
                "per_shift": [asdict(o) for o in (self.entry_per_shift or ())]})
            sh.entry_events = cfg
            sh.world.entry_events = cfg

    def _remove_from_slots(self, names: Sequence[str]) -> int:
        """把这些人从**所有班次**的进驻位上摘掉（返回动过几个班次）。

        ⚠️ 布局里的占位有两种写法：`operators`（紧凑）与 `slots`（有位次空洞）——
        **必须按位次读**（`_seat_specs` 认 `slots` 优先），只读 `operators` 会在
        `slots` 型设施上**摘不动人**（她一边在名单里、一边还在岗，破坏不变式）。
        ⚠️ 干员 spec 可能是**名字字符串**，也可能是**带心情/练度的对象**
        （`{"name": "泡泡", "mood": 10}`，场景 JSON 允许）—— 两种都要认。
        曾经直接 `n not in wanted`，遇到对象写法会 `TypeError: unhashable type: 'dict'`
        （2026-09 由"面板行序"用例暴露出来）。

        ⚠️ **摘人留空洞、不左移**（2026-10 修，A3 工单）—— 与
        `mood_soc.models.remove_occupant`、"导入层名单优先"（`_resolve_imported_detached`）
        以及 `_strip_from_other_facilities` **同口径**。旧实现是
        `kept = [s for s in specs if …]` 之后直接 `_write_seats(f, kept)`，把过滤后的
        **紧凑**列表写回去 ⇒ 后面的人整体前移，两种症状都实测过：
          · **锁漂到别人身上**：`["甲","乙","丙"]` 里乙钉在第 2 位（台账 `slots:[1]`），
            摘掉第 1 位的甲之后布局变 `["乙","丙"]`、台账仍是 `slots:[1]` ⇒ **丙**从此
            被当成"手动钉住"、自动入宿再也不换她；
          · **永久空锁**：`["甲","乙"]` 同样操作 ⇒ 布局 `["乙"]`、`slots:[1]` 指向一个
            不存在的位次 ⇒ `_seat_verdict(dorm,1)` 判 `keep`、`next_open_slot()==2`
            ⇒ **第 2 位永远填不进人**。

        ⚠️ **被摘空的那一格若在手动台账里 ⇒ 一并解掉**（照抄导入层那一条：先
        `_set_lock(f, i, False)` 再留洞）—— 她都不在那儿了，留一把"锁住这一格"的锁
        只会让自动入宿永远填不进它，语义上也说不通。
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
                hit_idx = [j for j, s in enumerate(specs)
                           if _spec_name(s) in wanted]
                if not hit_idx:
                    continue
                # ① 先按"摘人之前"的占位解掉被摘空那几格的锁（`_set_lock` 要读`who`）
                for j in hit_idx:
                    _set_lock(f, j, False)
                # ② 再把那几格**留成空洞**（列表长度不变 ⇒ 后面的人不左移）
                kept = list(specs)
                for j in hit_idx:
                    kept[j] = ""
                _write_seats(f, kept)
                hit = True
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
                  operators: Sequence[str], manual: bool = True) -> None:
        """改某个班次某间房的**进驻干员**（`operators` 里的空串 = 该位留空）。

        ⚠️ 位次语义：`operators[i]` 写的就是**第 i+1 位**。空串**留空洞、不左移**
        （"清空第 1 位"不会把后面的人往前挪），导出时写成 `slots: ["", …]`。

        **手动编辑逻辑（2026-10 口径＝摆位即上锁、清空即解锁）**：调用方传进来的整份列表里，
        **摆了人的位次**与被写进去的人一起记进手动台账（`_write_manual`，原来那份台账整份作废）；
        **被清空的位次不进台账** ⇒ 那一位交还自动入宿（自动入宿可以再占它、也可以换里面的人）。
        自动入宿从此不占"摆着人"的位次、不换那些人。导入**不打标**
        （只有这个入口与 `set_facility_slots` 会打）。见 `04-特殊机制.md` 第 30 条。

        ⚠️ **本入口的粒度是"整段"（不传 `touched_indices`）**：它把传进来的整份列表里
        **有名字的位次全算人写的** —— 这是**既有 API 契约**，界面那两条路
        （`set_facility_slots(touched=…)` / `apply_manual_shifts` 的逐位差异）走的是
        **累积**粒度（只锁碰过的那一格）。要收窄粒度请用 `set_facility_slots`。

        `manual=False`：**只改布局、不打手动标**（API 用；默认 `True` 保持既有行为）——
        "改布局"与"上锁"是两件事，这个开关是它们的解耦口。

        ⚠️ 传空列表（`[]`）或整份都清空＝把整间房交还给自动入宿。
        要"锁住一个空位/一整间空房"，请用 `set_seat_lock`（那是"预留空位"的能力）。
        """
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        names = [str(n) if n else "" for n in operators]
        fac = dict(facs[facility_index])
        if manual:
            # ⚠️ 2026-10 口径再改（「锁定入宿」工单 §3.2）：名单里的人**先剔名单、再写入**
            #    —— 旧行为是抛 `ValueError` 拒绝，用户拍板改成"把她从名单里剔掉再执行"。
            self._detach_guard(names)
        _write_seats(fac, names)
        if manual:
            # ⚠️ 2026-10 口径＝**摆位即上锁、清空即解锁**：只把"有名字的位次"记进台账，
            #    被清空的那一位**不进** `slots` ⇒ 交还自动入宿（旧实现是 `touched=len(names)`
            #    ⇒ 清空即上锁，用户 2026-10 拍板反转，见 `_write_manual` 的说明）。
            #    `touched` 仍传，用作位次的上界校验（别让越界下标进台账）。
            _write_manual(fac, raw_slots=[i for i, n in enumerate(names) if n],
                          raw_names=[n for n in names if n], touched=len(names))
        facs[facility_index] = fac
        self.replace_facilities(shift_index, facs)

    def set_facility_slots(self, shift_index: int, facility_index: int,
                           slots: Sequence[Optional[str]], manual: bool = True,
                           touched: Optional[Sequence[int]] = None,
                           pin: bool = False) -> None:
        """**带空洞的逐位写入**（界面的看板/表格走这条）：`slots[i]` = 第 i+1 位的干员名或 `None`。

        与 `set_slots` 的区别是**不动没提到的位次**：传进来的每一位按原样写
        （`None`/空串 = 该位留空），后面的位次与它们的手动标记保持原样。
        手动台账（2026-10）：**摆了人的位次**进台账（＝摆位即上锁）、写了名字的连人一起标；
        **被清空的位次不进台账**（＝清空即解锁，那一位交还自动入宿）。

        `touched` ＝ **调用方明确说"这些位次是我这次真的碰过的"**（可选，0 基下标）：
        · **不传（`None`）⇒ 行为一字不变**：传进来的整段里**有名字的位次**全记进台账
          —— 这是既有 API 契约（`api/` 与 `documents/11-程序接口.md` 都按这个写），
          留作兼容口；
        · **传了** ⇒ **累积**语义：只把**这些位次**里"现在还有人的"记进 `slots`、
          把**这些位次上现在坐着的人**记进 `names`，并**保留**上一次已经记过的
          （旧的 `∩ 现在还在的`）—— 于是"只放一个人"不会连坐同房间导入进来的人，
          以前手动放过的格子继续保持锁，清空某一格只让那一格退出台账。
          越界的下标按容量裁掉。细节见 `_write_manual`。

        `manual=False`：只改布局、不打手动标（与 `set_slots` 同一个开关）。

        `pin=True`（**可选开关，默认关 ⇒ 既有调用方行为一字不变**）：每格**先把她从本班
        其它设施里摘掉**再写（工单 §3.1「指定时清原位」）。「锁定入宿」矩阵与「干员与心情」
        位置列都开它 —— 这样同一个动作在两个入口表现一致。
        """
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        # ⚠️ `_write_manual` 的上界＝**传进来的那一段的长度**（不是 `len(merged)`）：
        #    旧契约里 `set_facility_slots([None, "丁"])` 的台账是 `slots=[1]`，
        #    改成 `len(merged)` 会把它变成 `[1, 2]`（既有断言与 API 契约都被改坏）。
        bound = len(slots)
        values = [str(spec) if spec else "" for spec in slots]
        if manual:
            # ⚠️ 工单 §3.2：名单里的人**先剔名单、再写入**（旧行为是抛错拒绝）。
            self._detach_guard(list(_seat_values(facs[facility_index])) + values)
        if not pin:
            fac = dict(facs[facility_index])
            merged = _seat_values(fac)
            for i, value in enumerate(values):
                while len(merged) <= i:
                    merged.append("")
                merged[i] = value
            _write_seats(fac, merged)
            if manual:
                _write_manual(fac, raw_slots=[i for i, n in enumerate(merged) if n],
                              raw_names=[n for n in merged if n], touched=bound,
                              touched_indices=None if touched is None else
                              [int(i) for i in touched])
            facs[facility_index] = fac
            self.replace_facilities(shift_index, facs)
            return
        # —— `pin=True`：逐格"先摘后写"（工单 §3.1）——
        #    ⚠️ 顺序是 **① 把 `values` 摊成一份"按位次对齐的 spec 副本" → ② 逐格"先摘本班
        #    别处的她、再写这一格" → ③ 收尾重建台账**。
        #    反过来（先摘、再按面板整段回填）会把刚摘掉的人又写回来（实测踩到）。
        #    台账放最后：`_write_one_seat(pin=True)` 里"摘空该格"那一手会顺手解掉台账，
        #    而摘的正是目标格，所以最后必须**从最终布局重建**它。
        #    ⚠️ **写的是 spec 不是名字**（`_seat_specs` 的对象写法要保住）：用纯名字写回会把
        #    这一房所有人的练度悄悄拍回默认 E2/Lv30 —— 那是 `ui/batch.py` 修过的老 bug，
        #    走 `pin` 这条路时**同样**会中招（实测：改「卡夫卡 E1」写回后引擎里又变 E2）。
        merged = _seat_specs(facs[facility_index])
        for i, value in enumerate(values):
            while len(merged) <= i:
                merged.append("")
            merged[i] = value
        if manual:
            for i, value in enumerate(values):
                if value:
                    _write_one_seat(facs, facility_index, i, value, pin=True)
                else:
                    stripped = dict(facs[facility_index])
                    _write_manual(stripped, touched=bound, touched_indices=[i])
                    facs[facility_index] = stripped
            keep = {int(k) for k in touched} if touched is not None \
                else {i for i, v in enumerate(values) if v}
            _rebuild_ledger(facs[facility_index], extra_slots=keep)
        # ③ 收尾：把 ② 那几步摘掉/清空的位次按 `merged` 收口 —— 目标格留住她、
        #    其它位次回到**摘之前**的样子（练度对象原样保留）。台账在**这之后**重算一次。
        fac = dict(facs[facility_index])
        _write_seats(fac, merged)
        if manual:
            _rebuild_ledger(fac, extra_slots=keep)
        facs[facility_index] = fac
        self.replace_facilities(shift_index, facs)

    def place_operator(self, shift_index: int, facility_index: int, slot: int,
                       name: str, replace_in_shift: bool = True) -> None:
        """**「锁定入宿」的唯一语义入口**：把某人钉到「本班 · 某设施 · 某位次」。

        与 `set_facility_slots` 的差别只有一条，但它是必需的：

        > **先把她从本班其它设施里摘掉**（留空洞、**不左移**），再写这一格。

        为什么必需（工单 §1 两条实测）：手动指定过去**不摘人** ⇒ 她会在同一班里
        **同时在控制中枢和宿舍#4**，而技能计数是**逐设施**数的（`len(facility.operators)`、
        `variables.basis_count`）⇒ 把「能天使」塞进「芳汀」（独处，`basis=dorm_others`）
        的宿舍后，芳汀的心情从 −5.00 变成 **−5.05**；`evaluate_base` 还会给她**输出两行**
        （`all_operators()` 不去重）。副作用（用户已知并接受）：她的**工作设施位次真空出来**。

        `replace_in_shift`（默认 `True`）：**她把原来待的那一处交出去**。
        ⚠️ 这条正是工单 §3.3「同一班次里把同一人指定到两个位次 ⇒ **后者覆盖**」——
        「锁定入宿」矩阵按班次逐格写，用户第二次点的是"改主意"，首次那一格必须腾出来。
        只有"允许同一人占两格"的老调用方需要显式传 `False`。

        ⚠️ **只动 `shift_index` 这一个班次**（逐班写入）—— 每班各自一份布局与台账。
        ⚠️ 摘人**留空洞、不左移**（与 `models.remove_occupant` 一致；**不走**
        `set_detached` 那条会紧凑化的路）。
        ⚠️ 她在「不在基建」名单里 ⇒ **先剔名单再写入**（工单 §3.2），不抛错。
        """
        self.place_operators({int(shift_index): [(int(facility_index), int(slot), name)]},
                             replace_in_shift=replace_in_shift)

    def place_operators(self, pins: Dict[int, List[Tuple[int, int, str]]],
                        detach_note: Optional[list] = None,
                        replace_in_shift: bool = True) -> int:
        """`pins` = `{班次下标: [(设施下标, 位次, 人名或空串), …]}` —— 批量"先摘后写"。

        每个班次**只写回一次**布局；同一次调用里同一格被写两次 ⇒ **后写的那次说了算**
        （工单 §3.3 的"后者覆盖"）。**不重算**（与 `set_facility_slots` 一样由
        `replace_facilities` 触发；界面走异步重算），返回落下去的格数。

        `replace_in_shift`（默认 `True`）：**她把原来待的那一处交出去**（工单 §3.3）。
        传 `False` ＝ 只写这一格、**不摘她在本班别处的位置**（老的"允许两处"语义；
        `apply_manual_shifts` 内部就是自己扫一遍再摘，走 `False` 免得重复摘）。

        `detach_note`（可选 `list`）：被**剔出「不在基建」名单**的人名会追加进去
        （工单 §3.2 的回执要用）。
        """
        if self.schedule is None:
            return 0
        written = 0
        for i, cells in pins.items():
            if not (0 <= int(i) < len(self.schedule.shifts)):
                continue
            # ⚠️ 工单 §3.2：名单里的人**先剔名单、再写入**（旧行为是抛错拒绝）。
            hits = self._detach_guard([str(name or "") for _fi, _s, name in cells])
            if detach_note is not None:
                for n in hits:
                    if n not in detach_note:
                        detach_note.append(n)
            facs = self.facilities_of(int(i))
            hit = False
            for fac_index, slot, name in cells:
                if not (0 <= int(fac_index) < len(facs)):
                    continue
                _write_one_seat(facs, int(fac_index), int(slot), str(name or ""),
                                pin=True, replace_in_shift=replace_in_shift)
                hit = True
                written += 1
            if hit:
                self.schedule = self.schedule.replaced_shift(int(i), facs)
        return written

    def _detach_guard(self, values: Sequence[str]) -> List[str]:
        """把 `values` 里**在「不在基建」名单里**的人**先剔出名单**，返回被剔掉的名字。

        ⚠️ **2026-10 口径改动（工单 §3.2「三条里改了一条」）**：本方法原来叫
        `_reject_detached_seats`，遇到名单里的人**抛 `ValueError` 拒绝**。用户拍板改成
        「**先把那个干员从名单里剔掉，再执行操作**」—— 因为「锁定入宿」的主语就是
        "把她放回基建"，拒绝只会逼用户先去另一个页面手动摘名单。

        ⚠️ **另外两处拒绝仍然保留**（只改了"放人"这一条路），「名单 ∧ 在位 ∧ 被锁」
        这个不变式照旧守得住：
          · `set_detached`：名单里的人**已被手动锁**在某个位次 ⇒ 拒绝；
          · `set_seat_lock(locked=True)`：要锁的那一位坐着的人**在名单里** ⇒ 拒绝。
        ⚠️ 名单是**会话级**的（不分班次）：剔掉一次，她在**所有班次**都不再是"不在基建"。
        ⚠️ 只改 `self.detached` 与 `schedule.detached`，**不摘位置**（她马上要被写到目标格上；
        走 `set_detached(remove_from_slots=True)` 会先把她从所有班次摘一遍，多绕一圈）。
        """
        wanted = set(self.detached)
        hits: List[str] = []
        for n in values:
            name = str(n or "")
            if name and name in wanted and name not in hits:
                hits.append(name)
        if not hits:
            return []
        rest = [n for n in self.detached if n not in set(hits)]
        self.detached = rest
        if self.schedule is not None:
            # 名单与各班的 `world` 必须一致（引擎读的是 `world.detached`）；
            # ⚠️ 用 `with_detached`（**不动位置**）—— 她马上要被写到目标格上，这里只摘名单。
            self.schedule = self.schedule.with_detached(rest)
        return hits

    def set_seat_lock(self, shift_index: int, facility_index: int, slot: int,
                      locked: bool = True) -> None:
        """给某个班次某间房的**某一位**单独上锁 / 解锁（**不动**这一位坐的是谁）。

        这是**唯一只改锁、不改布局**的入口。⚠️ 2026-10 起**界面上不再有锁控件**
        （逐位 `☑ 锁` 与「全部解锁…」都已删除，界面口径＝**摆位即上锁、清空即解锁**）——
        本方法**只剩 `api/` 与测试走**，它保留的价值是能把一个**空位**单独锁住
        （"预留空位"，界面上做不到）。

        · `locked=True`（上锁）：该位次进手动台账的 `slots` ⇒ 自动入宿**既不占它、也不换
          里面的人**；若该位是空的，效果就是"这一格**保持空着**"。
        · `locked=False`（解锁）：把该位次从 `slots` 摘掉，**并且**把这一位的人从 `names`
          摘掉（否则裁决点第 1 层会继续按"这个人被钉住"把她护着，解锁看起来没生效）。

        ⚠️ 位次必须在**容量**内（越界抛 `ValueError`）；容量为 0 的设施（活动室）没有可锁的位。
        ⚠️ 与"改动布局"无关：本方法**不碰** `slots`/`operators` 的占位（谁坐在哪不变）。
        ⚠️ **上锁那一位的人若已在「不在基建」名单里 ⇒ 拒绝**（与 `set_detached` 的拒绝对称）：
        "既在名单、又坐在位上、还被锁住"是自相矛盾的状态，两边都不许把它造出来
        （可达路径：`set_detached(names, remove_from_slots=False)` 之后再锁那一位）。
        """
        facs = self.facilities_of(shift_index)
        if not (0 <= facility_index < len(facs)):
            raise ValueError(f"第 {shift_index + 1} 班没有第 {facility_index + 1} 间房")
        fac = dict(facs[facility_index])
        cap = _capacity_of_dict(fac)
        if not (0 <= int(slot) < cap):
            raise ValueError(f"该设施没有第 {int(slot) + 1} 位（容量 {cap}）")
        if locked:
            seats = _seat_values(fac)
            who = seats[int(slot)] if int(slot) < len(seats) else ""
            if who and who in set(self.detached):
                raise ValueError(
                    f"无法上锁：{who} 已在「不在基建」名单里（第 {int(slot) + 1} 位）。"
                    "请先把她移出名单，或先把这一位的人换掉。")
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

    def manual_dorm_editor_state(self, shift_index: int) -> dict:
        """「手动入宿」编辑器要的**只读**数据：宿舍 × 位次（谁在、锁没锁）。

        → `{"shift": 下标, "label": 班次名, "shifts": [(下标, 标签), …],
            "dorms": [{"index": 设施下标, "name": 显示名, "capacity": 容量,
                       "seats": [{"slot": 0 基, "name": 人名或空串, "locked": bool}, …]}, …]}`

        谁在用：设置中心「闲置入宿」页的**手动入宿**子面板（选班次 → 选宿舍 → 逐格选人 + ☑ 锁）。
        ⚠️ `locked` 的判据与 `rules._seat_verdict` 第 1 层**逐字一致**（位次被钉 **或** 人被钉）；
        `name` 是**补名之后**的 `display_name`（如 `宿舍#1`）。
        """
        if self.schedule is None:
            return {"shift": 0, "label": "", "shifts": [], "dorms": []}
        n = len(self.schedule.shifts)
        i = min(max(int(shift_index), 0), n - 1)
        shift = self.schedule.shifts[i]
        dorms: List[dict] = []
        for fi, fac in enumerate(shift.world.facilities):
            if fac.ftype != FacilityType.DORMITORY:
                continue
            led = read_manual(fac)
            mapping = fac.slot_map()
            seats: List[dict] = []
            for slot in range(int(fac.capacity)):
                op = mapping.get(slot)
                who = op.name if op is not None else ""
                seats.append({"slot": slot, "name": who,
                              "locked": bool(led.pins_slot(slot)
                                             or (who and led.pins_name(who)))})
            dorms.append({"index": fi, "name": fac.display_name,
                          "capacity": int(fac.capacity), "seats": seats})
        return {"shift": i, "label": shift.label,
                "shifts": [(k, s.label) for k, s in enumerate(self.schedule.shifts)],
                "dorms": dorms}

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

    # ------------------------------------------------- 「取消指定 ⇒ 回导入原位」一族
    def release_seat(self, shift_index: int, facility_index: int, slot: int) -> str:
        """**取消「本班 · 某设施 · 某位次」的「我的指定」** ⇒ 被挪进来的人**回导入原位**。

        用户原话：「锁定入宿当选择的是别的设施中的干员后，再取消应该让对应干员回到自己
        原来的位置上」。步骤（工单 §2 任务 B）：

          ① 这一格**退出台账**（＝解锁，沿用「清空该位置」的既有语义：位次留空、**不左移**）；
          ② 若这一格上坐着的人**是我挪进来的**（本班台账 `names` 里有她）⇒ 把她送回
             **导入原位**；原位被别人占着 ⇒ **换回去**：占位者去她刚空出来的那一格；
          ③ 她的导入原位**查不到**（导入时她不在本班）⇒ 她不回原位、**本班不再占位**，
             回执里写明（这就是"她变成本班未排班"）。

        ⚠️ **只动 `shift_index` 这一个班次**（逐班写入；每班各自一份布局与台账）。
        ⚠️ **留洞、不左移** —— 别学 `set_detached`（`_remove_from_slots`）那条会紧凑化的路。
        ⚠️ **回退不打标**：被送回去的人**不进 `names`**、换回来的那一格也**不进 `slots`**
          （与"导入不打标"同口径）—— 否则"取消"会顺手把她重新钉住，用户会以为取消失败了。
        ⚠️ **不动自动入宿的临时安排**：那只在引擎副本里、每次重算都会重来；本方法只对
          **快照里被挪动过的人**生效。
        ⚠️ **不重算**（与 `place_operators` 一致）：界面走异步重算，直接调用的测试自己
          `recompute()`。

        返回一句**回执**（"送回原位"那一段；没有可送的人 ⇒ `""`）。
        """
        if self.schedule is None:
            return ""
        i = int(shift_index)
        if not (0 <= i < len(self.schedule.shifts)):
            return ""
        facs = self.facilities_of(i)
        fi = int(facility_index)
        if not (0 <= fi < len(facs)):
            return ""
        sl = int(slot)
        who = _seat_at(facs[fi], sl)
        moved_by_me = bool(who) and who in _shift_ledger_names(facs)
        # ① 清空这一格 + 这一格退出台账（`_write_manual` 顺带把她的名字从 names 摘掉）
        fac = dict(facs[fi])
        bound = max(_capacity_of_dict(fac), len(_seat_values(fac)), sl + 1)
        _set_seat_value(fac, sl, "")
        _write_manual(fac, touched=bound, touched_indices=[sl])
        facs[fi] = fac
        # ② 她是我挪进来的 ⇒ 送回导入原位（原位被占 ⇒ 换回去）
        note = ""
        if moved_by_me:
            note = self._return_to_origin(facs, i, who, vacated=(fi, sl))
        self.schedule = self.schedule.replaced_shift(i, facs)
        return note

    def restore_seat(self, shift_index: int, facility_index: int, slot: int) -> str:
        """**「恢复默认（回到导入时）」**：把这一格恢复成导入时的样子，并撤销相关的挪动。

        用户口径（工单 §3 任务 C，一次点下去全做）：

          ① 解除这一格的「我的指定」（该位次退出台账）；
          ② 把**导入时原本坐这一格的人**放回这一格（她若被我挪到别处，先摘出来）；
          ③ 把这一格上**我锁进来的那位**送回她的**导入原位**（原位被占 ⇒ 同任务 B 的
             「换回去」）；原位查不到 ⇒ 她不回原位、本班不再占位；
          ④ 这一格导入时本来就空 ⇒ **保持空着**（不凭空冒出一个人；被动作 ③ 的"换回去"
             带进来的人不算 —— 那是用户拍板的换位口径）。

        ⚠️ **动作 ② 优先于动作 ③ 的换位**：若"换回去"把占位者送进了这一格、而这一格导入时
        本来有人 ⇒ 先把那位占位者也送回她的导入原位，再放回导入原主；她若没有原位，就
        留在这一格并在回执里写明（**不静默顶掉**）。
        ⚠️ **只动这一个班次**；**留洞、不左移**；**回退不打标**（同 `release_seat`）；
        **不做批量恢复**（用户明确：恢复只能一格一格来）。
        ⚠️ **不重算**（同 `release_seat`）。
        """
        if self.schedule is None:
            return ""
        i = int(shift_index)
        if not (0 <= i < len(self.schedule.shifts)):
            return ""
        facs = self.facilities_of(i)
        fi, sl = int(facility_index), int(slot)
        if not (0 <= fi < len(facs)):
            return ""
        cur = _seat_at(facs[fi], sl)
        want = self.imported_seat_occupant(i, fi, sl)
        bits: List[str] = []
        # ① 解除这一格的指定（位次退出台账）＋ 把这一格**空出来**（她的去路由 ③ 决定）
        fac = dict(facs[fi])
        bound = max(_capacity_of_dict(fac), len(_seat_values(fac)), sl + 1)
        _set_seat_value(fac, sl, "")
        _write_manual(fac, touched=bound, touched_indices=[sl])
        facs[fi] = fac
        # ③ 我锁进来的那位回她的导入原位（同一格上的人就是她；导入原主不算"我锁的"）
        if cur and cur != want:
            note = self._return_to_origin(facs, i, cur, vacated=(fi, sl))
            if note:
                bits.append(note)
        # ② 导入时原本坐这一格的人放回这一格
        if want:
            holder = _seat_at(facs[fi], sl)
            if not holder:
                self._put_back_local(facs, i, want, (fi, sl))
                if cur != want:
                    bits.append(f"{want} 已放回导入原位"
                                f"（{self._room_label(i, fi)} 第 {sl + 1} 位）")
            elif holder != want:
                # 换位把别人带进了这一格 ⇒ 动作 ② 优先：先让那位也回她的导入原位（不再二次换位）
                extra = self._return_to_origin(facs, i, holder, vacated=None)
                if extra:
                    bits.append(extra)
                if not _seat_at(facs[fi], sl):
                    self._put_back_local(facs, i, want, (fi, sl))
                    bits.append(f"{want} 已放回导入原位"
                                f"（{self._room_label(i, fi)} 第 {sl + 1} 位）")
                else:
                    bits.append(f"⚠ 导入原主 {want} 暂时放不回这一格"
                                f"（{self._room_label(i, fi)} 第 {sl + 1} 位 仍被 "
                                f"{_seat_at(facs[fi], sl)} 占着）")
        self.schedule = self.schedule.replaced_shift(i, facs)
        return "；".join(bits)

    def _put_back_local(self, facs: List[dict], shift_index: int, name: str,
                        cell: Tuple[int, int]) -> None:
        """把 `name` 放回**本班**的某一格（先把她从别处摘掉，留洞不左移），**不打标**。"""
        fi, sl = int(cell[0]), int(cell[1])
        _strip_from_other_facilities(facs, name, keep=(fi, sl))
        fac = dict(facs[fi])
        _set_seat_value(fac, sl, name)
        _prune_ledger(fac, drop_names=[name])
        facs[fi] = fac
        for k, other in enumerate(facs):
            if k != fi and other.get("manual"):
                _prune_ledger(other, drop_names=[name])

    def _return_to_origin(self, facs: List[dict], shift_index: int, name: str,
                          vacated: Optional[Tuple[int, int]]) -> str:
        """把 `name` 送回她的**导入原位**（原位有人 ⇒ 换回去）；**就地改 `facs`**，返回回执。

        `vacated` ＝ "她刚空出来的那一格"（原位被占时，占位者换到这里）；传 `None` ＝
        **不换位**（原位被占就让她留在原地，调用方自己收尾）。
        ⚠️ **不打标**（回退到导入时；见 `release_seat`）—— 她不再是我手动放的人。
        """
        origin = self.imported_seat_of(shift_index, name)
        if origin is None:
            if vacated is None:
                return f"{name} 导入时本班未排班 ⇒ 没有原位可回（留在原处）"
            return f"{name} 导入时本班未排班 ⇒ 不回原位（本班不再占位）"
        ofi, osl = int(origin[0]), int(origin[1])
        if not (0 <= ofi < len(facs)):
            if vacated is None:
                return f"{name} 的导入原位已不存在 ⇒ 没有原位可回（留在原处）"
            return f"{name} 的导入原位已不存在 ⇒ 不回原位（本班不再占位）"
        holder = _seat_at(facs[ofi], osl)
        if holder == name:                              # 已经在原位
            return ""
        if holder and vacated is None:
            return f"{name} 的导入原位（{self._room_label(shift_index, ofi)} 第 {osl + 1} 位）" \
                   f"被别人占着 ⇒ 她留在原处"
        # 放回原位：`_put_back_local` 会先把她在**别处**的那一份摘掉（留洞、不左移）
        self._put_back_local(facs, shift_index, name, (ofi, osl))
        there = f"{self._room_label(shift_index, ofi)} 第 {osl + 1} 位"
        if not holder:
            return f"{name} 已回导入原位（{there}）"
        vfi, vsl = int(vacated[0]), int(vacated[1])
        self._put_back_local(facs, shift_index, holder, (vfi, vsl))
        return (f"{name} 已回导入原位（{there}），"
                f"{holder} 换到 {self._room_label(shift_index, vfi)} 第 {vsl + 1} 位")

    def _room_label(self, shift_index: int, facility_index: int) -> str:
        """某班某设施下标 → 显示名（回执用；宿舍拿到补名后的 `宿舍#1`）。"""
        try:
            return str(self.schedule.shifts[int(shift_index)].world.facilities[
                int(facility_index)].display_name)
        except (AttributeError, IndexError, TypeError):
            return f"第 {int(facility_index) + 1} 间"

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

    def apply_manual_shifts(self, changes: Dict[int, List[dict]],
                            recompute: bool = True, detach_note=None) -> int:
        """**整批**落地"按班次的布局改动"，并把**占位真的变了**的设施记进手动台账。

        `changes` = `{班次下标: 布局列表}`；每个布局项是**设施描述**（`ui/batch.py` 给的
        "按位次对齐的名字列表"放在 `operators` 里，空串＝空槽）。返回**改动过的设施数**。

        **Q15=(a) 的落点**："任何界面摆位都算手动入宿" —— 所以这条路的台账语义与
        `set_facility_slots` 一致（2026-10 口径）：**摆了人的位次**进台账（＝摆位即上锁）、
        **被清空的位次不进台账**（＝清空即解锁，那一位交还自动入宿）。

        ⚠️ **粒度＝"这次真的改了的那几位"（2026-10 第三次收敛）**：逐位比对新旧占位，
        把**值不同的位次**作为 `touched_indices` 交给 `_write_manual`（**累积**：旧台账里
        仍然成立的部分保留）—— 面板交来的是一整班布局，但"我只改了一格"就**只锁那一格**，
        同房间导入进来的人不受影响。不这么做就会连坐（实测只碰 1 格却锁上 `[0, 1, 4]`，
        导入进来的人从此再不能被自动入宿换出）。

        ⚠️ 为什么不是让调用方逐间调 `set_facility_slots`：
        ① **只动"占位真的变了"的设施** —— 面板给的是一整班布局，未动的房间不该被"手动钉住"
           （否则"改一行"会把整班都锁上，自动入宿从此再也进不来）；
        ② **可只重算一次** —— 逐间调会各自 `recompute()`，一次粘贴改 8 间房就是 8 次重算
           （界面明显卡）；`recompute=False` 时由调用方统一触发（界面走异步重算）。
        """
        if self.schedule is None or not changes:
            return 0
        written = 0
        for i in sorted(changes):
            if not (0 <= i < len(self.schedule.shifts)):
                continue
            facs = self.facilities_of(i)
            # —— ① 先算"这一班谁最后落在哪一格"（面板给的整班布局）：摘人要放在
            #    **写完整班布局之后**（面板那份布局里往往还写着"她在原来那处"，
            #    反过来的话会被 `_write_seats` 又写回来）。
            #    ⚠️ 同一人出现两处时**由"面板交来的先后"决定谁赢**（后写的那一处留下
            #    ＝工单 §3.3）。**不能按坐标 `(设施, 位次)` 比大小**：面板是按"改过哪几间房"
            #    给的（`ui/batch.py` 的 `_fac_names` 按设施下标），可制造站的下标比宿舍大
            #    ⇒ 按坐标取大值会把"她还在制造站上班"那份**旧**布局当成最新意图，
            #    宿舍那一处就腾不出来了（实测踩到）。
            pins: Dict[str, Tuple[int, int]] = {}
            for fi, item in enumerate(changes[i]):
                if fi >= len(facs) or not isinstance(item, dict):
                    continue
                for k, n in enumerate(_seat_values(item)):
                    if n:
                        pins[str(n)] = (fi, k)
            written_before = written
            # —— ② 落面板给的那份整班布局（逐位差异 → 累积台账）——
            #    ⚠️ `snapshot` 存的是**摘之前的 spec**（保住练度对象写法），另给一份"名字版"
            #    用来逐位比对；只落 `diff` 那几位 —— 整段照抄既会把 ③ 摘掉的人写回来，
            #    也会把这一房所有人的练度拍回默认（`ui/batch.py` 修过的老 bug）。
            spec_snapshot = {fi: _seat_specs(facs[fi]) for fi in range(len(facs))}
            snapshot = {fi: [_spec_name(s) for s in spec_snapshot[fi]]
                        for fi in range(len(facs))}
            for fi, item in enumerate(changes[i]):
                if fi >= len(facs):
                    break
                if not isinstance(item, dict):
                    continue
                # 比较用**名字**（位次对齐），写回用**spec**（保留 `{"elite":…}` 对象写法 ——
                # 用名字写回会把这一房所有人的练度悄悄拍回默认 E2/Lv30）。
                new_values = _seat_values(item)
                item_specs = _seat_specs(item)
                old_values = snapshot[fi]
                fac = dict(facs[fi])                  # 以**当前**那份为底（别丢 ③ 的摘人结果）
                fac.update({k: v for k, v in item.items() if k != "manual"})
                width = max(len(new_values), len(old_values))
                diff = [k for k in range(width)
                        if (new_values[k] if k < len(new_values) else "")
                        != (old_values[k] if k < len(old_values) else "")]
                # 以**摘之前**那份 spec 为底、**只改"确实变了的位次"**：没碰过的位次保持原样。
                # ⚠️ **写不写**看 spec 差异 —— 面板给的可能是"把纯名字升级成练度对象"而名字没变
                #    （练度就是从纯名字改成 `{"elite": 1}` 的，名字一模一样）；只看名字就写不进去。
                # ⚠️ **打不打标**仍只看**名字**差异（`diff`）：那才是"碰过哪一格"的口径，不能因为
                #    "面板这次把对象一起交上来了"就把整房连坐锁上。
                seats = list(spec_snapshot[fi])
                for k in range(max(len(item_specs), len(seats))):
                    new_spec = item_specs[k] if k < len(item_specs) else ""
                    old_spec = seats[k] if k < len(seats) else ""
                    if _spec_name(new_spec) != _spec_name(old_spec):
                        if k not in set(diff):
                            continue          # 名字差异由 `diff` 说了算（别越权改没碰过的位次）
                    elif new_spec == old_spec:
                        continue              # 完全一样：保持"摘之前"那份（别整房重写）
                    while len(seats) <= k:
                        seats.append("")
                    seats[k] = new_spec
                _write_seats(fac, seats)
                if new_values != old_values:
                    _write_manual(fac, raw_slots=[k for k, n in enumerate(new_values) if n],
                                  raw_names=[n for n in new_values if n],
                                  touched=width, touched_indices=diff)
                facs[fi] = fac
                written += 1
            # —— ③ 「指定时清原位」（工单 §3.1）：把每个人从**除最后那一格以外**的
            #    所有位次上摘掉（留空洞、不左移），并解掉被摘空那一格的手动标记。
            if pins:
                for n, cell in pins.items():
                    _strip_from_other_facilities(facs, n, keep=cell)
                hits = self._detach_guard(list(pins))
                if detach_note is not None:
                    for n in hits:
                        if n not in detach_note:
                            detach_note.append(n)
            # ⚠️ 只要写过就装这份布局 —— **不能只看"有没有打标"**：面板只改房间等级时
            #    占位一个都没变，但那份 `level` 必须落进 `schedule`（否则改等级静默失效）。
            if written > written_before:
                self.schedule = self.schedule.replaced_shift(i, facs)
        if written and recompute:
            self.recompute()
        return written

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


# ---------------------------------------------------------------- 位次小工具
# （「锁定入宿 ⇒ 取消 / 恢复默认」那一族共用；都**只动布局**，台账由调用方显式处理）
def _seat_at(fac: dict, slot: int) -> str:
    """这一格坐的是谁（空槽 / 越界 ⇒ `""`）。"""
    values = _seat_values(fac)
    slot = int(slot)
    return values[slot] if 0 <= slot < len(values) else ""


def _set_seat_value(fac: dict, slot: int, name) -> None:
    """把某一格写成 `name`（空串＝留空）—— 越界补空槽、**不左移**，**不碰台账**。"""
    values = _seat_values(fac)
    slot = int(slot)
    while len(values) <= slot:
        values.append("")
    values[slot] = str(name or "")
    _write_seats(fac, values)


def _ledger_names(fac: dict) -> set:
    """这一间的手动台账里"我手动放进去的人"（`manual.names`）。"""
    return {str(n) for n in ((fac.get("manual") or {}).get("names") or []) if str(n)}


def _shift_ledger_names(facs: List[dict]) -> set:
    """**一个班次全部设施**的台账人名并集 —— 判"她是不是我挪进来的"用它。"""
    out: set = set()
    for f in facs:
        out |= _ledger_names(f)
    return out


def _prune_ledger(fac: dict, drop_names: Sequence[str] = ()) -> None:
    """把 `drop_names` 从这一间的台账里摘掉，并清掉"已经不成立"的条目（**不改布局**）。

    用在"系统把人挪回去了"之后：那位已经不是我手动放的人了（回退**不打标**，与导入同口径），
    留着她的名字会让 `_seat_verdict` 第 1 层继续把她钉住 —— "取消指定"看起来没生效。
    ⚠️ `slots` 只保留"现在仍然坐着人"的位次（与 `_write_manual` 同一套累积口径）。
    """
    led = fac.get("manual")
    if not led:
        return
    values = _seat_values(fac)
    cap = max(_capacity_of_dict(fac), len(values))
    live = {v for v in values if v}
    live_slots = {i for i, v in enumerate(values) if v and i < cap}
    old_slots = {int(i) for i in (led.get("slots") or [])}
    old_names = {str(n) for n in (led.get("names") or [])}
    _put_manual(fac, old_slots & live_slots,
                (old_names - {str(n) for n in drop_names}) & live)


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


def _write_manual(fac: dict, *, raw_slots=(), raw_names=(), touched: Optional[int] = None,
                  touched_indices: Optional[Sequence[int]] = None) -> None:
    """写设施描述的 `manual` 台账（`store.session.set_slots` / `set_facility_slots` 用）。

    规则（Q2/Q12/Q13；2026-10 口径反转，见 `04-特殊机制.md` 第 30 条）：
      · **摆了人的位次**进 `slots`＝"这一位归人管"（＝**摆位即上锁**）；
      · **被清空的位次不进 `slots`**＝"这一位交还自动入宿"（＝**清空即解锁**）；
      · 写了名字的进 `names`＝"这个人是人放的"；
      · 同一份数据里**已经被写掉的人**（不再出现在 `values` 里）从 `names` 摘掉；
      · 台账空了就把 `manual` 键删掉（导出保持干净）。

    ⚠️ **上一轮这里是反的**：`slots |= set(range(limit)) if touched is not None else set()`
    把"传进来的整段位次"（含清空的）全锁上＝「清空即上锁」。用户 2026-10 拍板改成
    **「摆位即上锁、清空即解锁」**（原话：「放上去之后自动上锁。**不需要手动上锁**」
    「解锁时只需要**将该位置空**就可以了」）⇒ 那一行删掉。要"锁住一个空位"请走
    `set_seat_lock`（`_set_lock`，**"预留空位"的能力，没动过**）。

    ⚠️ **粒度（2026-10 第三次收敛）**：光有"摆位即上锁、清空即解锁"还不够 —— 界面过去交来的
    是"按位次对齐的**整段**"，于是"我只放了一个人"也会把同一间宿舍里**导入进来的人**一起
    记进台账（实测 `slots` 从 `[]` 变成 `[0, 1, 4]`），他们从此再不能被自动入宿换出。
    所以新增 `touched_indices`＝**调用方明确说"这些位次是我这次碰过的"**：

      · `touched_indices is None`（**旧契约，不传就保持原样**）：把传进来的整段里
        **有名字的位次**全算人写的 —— 这是 `set_slots` 的既有 API 口径，别改；
      · `touched_indices` 给定 ⇒ 按**累积**算：
        `slots = (旧 slots ∩ 现在仍有人的位次) ∪ (T ∩ 现在仍有人的位次)`、
        `names = (旧 names ∩ 现在仍在本设施里的人) ∪ (T 位上现在坐着的人)`。
        于是"放一个人到空格只多锁那一格"、"以前手动放过的格子继续保持锁"、
        "清空某一格只让那一格退出台账"；`T` 里的越界下标按**容量**裁掉。
        ⚠️ 读**旧台账**就是这里读的 `fac["manual"]` —— `_write_seats` 只摘
        `slots`/`operators`、**不动** `manual`，所以进本函数时它还是旧值
        （`apply_manual_shifts` 的 `fac = dict(item)` 也把旧 `manual` 带过来）。

    ⚠️ **`touched` / `limit` 仍留作位次的上界校验**（不是"锁哪些位次"）：
    `_seat_values` 会**裁掉尾部空槽**（`["甲","乙",""]` → `["甲","乙"]`），不夹上界的话
    越界的 `raw_slots`（比如同一批里第 3 位被清空、而前面几位的下标还在）会被写进台账。
    上界取**容量**而不是"占位数组的长度"（`_capacity_of_dict` 与 `Facility.capacity`
    同一套规则）。
    """
    cap = max(_capacity_of_dict(fac), len(_seat_values(fac)))
    limit = cap if touched is None else min(int(touched), cap)
    values = _seat_values(fac)
    live = {v for v in values if v}
    if touched_indices is None:
        slots = {int(i) for i in raw_slots if 0 <= int(i) < limit}
        names = {str(n) for n in raw_names if n}
    else:
        old = dict(fac.get("manual") or {})                   # ← 旧台账（`_write_seats` 不碰它）
        old_slots = {int(i) for i in (old.get("slots") or [])}
        old_names = {str(n) for n in (old.get("names") or [])}
        mine = {int(i) for i in touched_indices if 0 <= int(i) < cap}
        live_slots = {i for i, v in enumerate(values) if v and i < cap}
        slots = (old_slots & live_slots) | (mine & live_slots)
        names = (old_names & live) | {values[i] for i in sorted(mine)
                                      if i < len(values) and values[i]}
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


def _strip_from_other_facilities(facs: List[dict], name: str,
                                 keep: Optional[Tuple[int, int]] = None) -> int:
    """把 `name` 从这一班里**除 `keep` 那一格以外的所有位次**上摘掉（留空洞、**不左移**）。

    `keep` ＝ `(设施下标, 位次)`：**只有这一格不动**（它是这次要保留/写进去的那一格）。
    ⚠️ 别写成"跳过 `keep[0]` 那一整间设施"：同一个人**在同一间房的另一个位次**上也得摘
    （工单 §3.3「同一班次里把同一人指定到两个位次 ⇒ 后者覆盖」就是这条）。

    「指定时清原位」的语义核心（工单 §3.1）：手动指定过去**不摘人** ⇒ 她会在同一班里
    同时在控制室与宿舍，而技能计数是**逐设施**数的（`len(facility.operators)`、
    `variables.basis_count`）⇒ 实测把「能天使」塞进「芳汀」（独处，`basis=dorm_others`）
    的宿舍后，芳汀从 −5.00 变 **−5.05**；`evaluate_base` 还会给她输出两行。

    ⚠️ **不走** `set_detached` 那条路：`Session._remove_from_slots` 摘完人会**紧凑化**，
    位次整体前移，与"位次不左移"直接冲突。
    ⚠️ 被摘空的那一格若在手动台账里 ⇒ **一并解掉**（那一格已经空了，留着"钉住一个空位"
    会让 `_seat_verdict` 第 1 层把它**保持空着**，而用户这次要的是"把她挪到这一格"）。
    ⚠️ **只写回真的动过的设施**（`hit` 才 `facs[k] = fac`）：否则会把每个设施都重写成
    `slots` 写法（导出形态跟着变），而这条路上大多数设施本来就没人。
    """
    if not name:
        return 0
    keep_fi, keep_slot = (int(keep[0]), int(keep[1])) if keep is not None else (-1, -1)
    hit = 0
    for k, f in enumerate(facs):
        old_specs = _seat_specs(f)
        old_values = _seat_values(f)
        idx = [i for i, v in enumerate(old_values) if v == name
               and not (k == keep_fi and i == keep_slot)]
        if not idx:
            continue
        for i in idx:
            while len(old_specs) <= i:
                old_specs.append("")
            old_specs[i] = ""
        fac = dict(f)
        _write_seats(fac, old_specs)
        _write_manual(fac, raw_slots=[k2 for k2, n in enumerate(old_values) if n],
                      raw_names=[n for n in old_values if n],
                      touched=len(old_values), touched_indices=idx)
        facs[k] = fac
        hit += len(idx)
    return hit


def _write_one_seat(facs: List[dict], fac_index: int, slot: int, spec, *,
                    pin: bool = False, replace_in_shift: bool = True) -> None:
    """**把一格位次写成 `spec`**（`store.session` 的「锁定入宿」一族共用这一处），并维护台账。

    `facs` ＝ 某一班次的**可改副本列表**（`Session.facilities_of(i)` 的产物），
    本函数就地改 `facs[...]`（调用方自己决定何时 `replaced_shift` 写回）。

    · `pin=False`：只写这一格；写名字 ⇒ 这一格进台账、名字进 `names`（＝**摆位即上锁**）、
      被清空 ⇒ 这一格**退出**台账（＝**清空即解锁**，交还自动入宿）。与
      `set_facility_slots`（不传 `touched` 的那条旧契约）逐格等价。
    · `pin=True`：先**把她从本班其它设施里摘掉**（留空洞、**不左移**）再写这一格
      —— 这是「锁定入宿」的核心语义（工单 §3.1）。**不走** `set_detached` 那条会紧凑化的
      路（`_remove_from_slots` 摘完人不留洞）。被摘掉的那一格若在台账里也**一并解掉**：
      那一格已经空了，留着"钉住一个空位"会把它**保持空着**（`_seat_verdict` 第 1 层），
      而用户这次的意图是"把她挪到这一格"。
    · `replace_in_shift=False`：**只摘别的设施、不摘宿主的别处**（`apply_manual_shifts`
      自己扫一遍再摘，用它免得重复摘；老调用方也用它保留"允许两处"的旧语义）。

    ⚠️ 写进去的是**纯名字**（`_spec_name(spec)`）：练度由面板给的那份布局（`_seat_specs`）
    保留，用对象写回会把旁边几位的练度拍回默认 E2/Lv30。
    ⚠️ `pin=True` 时 `names` 会把写进来的这位**重新加回**（她刚被从别处摘掉、`names`
    里那份记录也被摘过一次）。
    """
    name = _spec_name(spec)
    if pin and name and replace_in_shift:
        _strip_from_other_facilities(facs, name, keep=(fac_index, int(slot)))
    fac = dict(facs[fac_index])
    seats = _seat_values(fac)
    bound = max(_capacity_of_dict(fac), len(seats), int(slot) + 1)
    while len(seats) <= int(slot):
        seats.append("")
    seats[int(slot)] = name
    _write_seats(fac, seats)
    if name:
        _write_manual(fac, raw_slots=[int(slot)], raw_names=[name], touched=bound,
                      touched_indices=[int(slot)])
    else:
        _write_manual(fac, touched=bound, touched_indices=[int(slot)])
    facs[fac_index] = fac


def _rebuild_ledger(fac: dict, *, extra_slots: Sequence[int] = ()) -> None:
    """按设施**当前**的占位重建手动台账：`slots` ＝ "这一格归人管" 的位次。

    规则（沿用 `_write_manual` 的**累积**语义，但把它一次算清）：
      `slots = (旧 slots ∩ 现在仍有人的位次) ∪ (extra_slots ∩ 现在仍有人的位次)`、
      `names = (旧 names ∩ 现在仍在本设施里的人) ∪ (那几格上现在坐着的人)`。

    `extra_slots`＝**这次明确碰过**的位次（工单 §3.1 的"指定时清原位"这条路上，
    目标格必须进来 —— 写它的过程会把台账摘掉一次）。空集合＝"不额外加锁、
    只把旧台账里已经不成立的条目清掉"。

    ⚠️ 为什么不用 `_write_manual(touched_indices=…)` 就地算：那条路的**顺序**是
    "先写布局、再算台账"，而「指定时清原位」必须先摘后写 ⇒ 中间态会把目标格的
    名字摘掉。这里改成**最后**从最终布局重建，一次算清、不留中间态。
    """
    seats = _seat_values(fac)
    cap = max(_capacity_of_dict(fac), len(seats))
    old = dict(fac.get("manual") or {})
    old_slots = {int(i) for i in (old.get("slots") or [])}
    old_names = {str(n) for n in (old.get("names") or [])}
    live_slots = {i for i, v in enumerate(seats) if v and i < cap}
    live = {v for v in seats if v}
    mine = {int(i) for i in extra_slots if 0 <= int(i) < cap}
    slots = (old_slots & live_slots) | (mine & live_slots)
    names = (old_names & live) | {seats[i] for i in sorted(mine)
                                 if i < len(seats) and seats[i]}
    _put_manual(fac, slots, names)


def _trim_to_capacity(fac: dict, capacity: int) -> dict:
    """容量变小 ⇒ **越界的空位次与它的手动标记一起丢掉**（Q37 的裁决）。

    ⚠️ **只丢空位、不丢人**：越界段里**只要还有人就不截断**（那正是"超容量"这种布局问题，
    要交给自检报出来，不能在用户没看见的时候把人悄悄删掉 —— 曾经的实现就是那样）。
    只有越界段**全空**时才把它连同里面的手动标记一起丢掉。

    ⚠️ **判据是"越界段里还有没有人"，不是"越界段里有没有空位"**（2026-10 修，A2 工单）：
    旧实现是 `drop = [越界段里的空位]; if not drop: return fac` —— 于是
    `["甲","乙",null,"丁"]` 缩到 2 位时，越界段里**有一个空位**就通过了那道检查，
    接着 `keep = values[:capacity]` 把整段（**连带里面的人**）截断 ⇒ **静默删掉「丁」**，
    而且 `validate()` 干净、0 告警。这与它自己的 docstring 以及相邻分支
    （"越界段全是人 ⇒ 4 人全留 + 报 2 条超容量"）直接冲突。
    ⚠️ 判据必须按**位次**看（`values[capacity:]` 里有没有非空格），不能按"紧凑列表长度"：
    空洞也算位次，`capacity` 是**位次**上界。
    ⚠️ 用 `_seat_specs`（带对象写法）而不是 `_seat_values`（只有名字）：否则裁一次容量
    就把这批人的练度 `{"elite": 1, "level": 30}` 悄悄丢成纯名字。
    """
    values = _seat_specs(fac)
    if len(values) <= capacity:
        return fac
    # ⚠️ 越界段里还有人 ⇒ 一律保留（交给 `validate()` 报超容量）；只有**全空**才截断
    if any(values[capacity:]):
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
