"""ui/batch.py —— 「干员与心情」面板：**当前布局里的所有干员 + 练度 + 心情，一次改完**。

## 为什么需要它

看板上改一个人要「左键选人 / 右键设心情」各点一次；一份 3 班排班有 57 个位置、
57 名干员，逐个点完要上百次点击。这个面板把同一件事摊成一张表：

| 区域 | 能做什么 |
|---|---|
| **时刻** | 指定"看哪一周期、哪一刻"（`周期 1~周期数` + `HH:MM`，可 ±15 分钟，也可「跟随滑块」） |
| **心情** | 改表格里的心情 = **在那一刻给她指定这个值**（锚点）；另有全部满 / 全部 0 / 统一设为 X / 按这一刻回填 / 恢复导入值 |
| **干员** | **粘贴一份名单**按房间顺序填入 / 清空本班次 / 逐行点开搜索选人 |
| **表格** | 房间 · 位次 · 干员 · 练度 · **该时刻的心情**，一次看全、一次改完 |

## 四条口径（写清楚免得被当 bug）

1. **心情列永远是"指定时刻那一刻的实际心情"**：时刻由上面的「周期 + 时刻」定
   （缺省＝第 1 周期 0:00，也就是老口径的"周期起点心情"）。换时刻 ⇒ 列里的数值
   按轨迹重算，**不是**重新输入一遍。
2. **改某一格 ⇒ 在那一刻给她一个「心情指定事件」（锚点）**：`(周期, 时刻, 干员) → 值`，
   引擎在那一刻把她的心情直接置成该值，之后按正常速率演化（见 `ui/schedule.py`
   的 `MoodSetEvent`）。**第 1 周期 0:00** 那一格写的是 `initial_moods`（周期起点心情），
   与老口径完全一致。锚点只对**指定的那个周期**生效；锚点一览写在心情区，可一键清空。
3. **干员列只改「时刻所在的那一班」**：改时刻就会自动切到那一刻所在班次的名单
   （也可直接在班次下拉里选，选完时刻会挪到该班起点）。不做"一键套用到所有班次"
   ——3 班（12/6/6）的人员本来就不同，一键套容易误伤。
4. **返回值**：`(changes, moods, mood_events)` —— `moods` 只带"和导入值不同的起点心情"
   （差集，调用方整份替换 `initial_moods`）；`mood_events` 是全部锚点（整份替换）。

界面只做"收集结果"，算仍然在 `ui/schedule.py` / `mood_soc` 里——本模块不引入新的数值逻辑。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import ttk
from typing import Dict, List, Optional, Sequence, Tuple

from mood_soc.battery import to_decimal
from mood_soc.config import (MOOD_MAX, MOOD_MIN, OUTPUT_ROOM_TYPES, OUTPUT_SLOT_TOTAL,
                            facility_max_level, facility_slots)
from mood_soc.scenario import DEFAULT_OPERATOR_LEVEL
from store.session import seat_specs, seat_values, write_seats   # 位次口径只有一套：`slots` 优先

from ui import theme
from ui.board import facility_tag
from ui.dialogs import ask_operator, parse_mood
from ui.schedule import MoodSetEvent, all_operator_names
from ui.scroll import VScroll

# 表格默认可视高度（独立使用时的值；设置中心里传 `"auto"` → 吃掉内容区的剩余高度）
TABLE_H = 420
# 「房间等级」区的网格列数：内容区约 810px 宽，每格 ≈ 150px → 5 列不会超宽
LEVEL_COLS = 5
LEVEL_NAME_W = 9        # 房间名那一列的字符宽（如「制造站#1」）
LEVEL_GAP = 14
# 表格可视高度的下限（自动模式下再挤也要留这么多）
TABLE_H_MIN = 96
#: 自校正的**让步下限**：整页超出内容区时，表格最多被压到这里。
#: 为什么允许压到 96 以下：房间极多时（22 间 → 等级区 5 行）真的没有余量了 ——
#: 宁可表格矮一点（有滚动条，照样能用），也不要让整页顶出 `PAGE_H`（会被裁、切页时窗口跳）。
TABLE_H_FLOOR = 64
#: 表格区内部**不参与伸缩**的高度：搜索栏 + 表头（两行）。`_table_height()` 是在建表格
#: **之前**跑的，那时搜索栏/表头还没建出来、量不到，只能按这个估计值先扣一次；
#: 不够的部分由 `_fit_table_height()` 的**自校正**兜底（见它的说明）。
TABLE_CHROME = 55
# 心情输入的防抖（毫秒）：一次落地＝宿主那边一整轮重算（0.23~1.2s），别设得太短
NOTIFY_DEBOUNCE_MS = 500
# 「不在基建」那一段在表格里的**设施下标哨兵**：该段的行统一用这个值当 key 的第一项。
# 真设施的合法下标是 `0 ≤ fi < len(facilities)`，所以 `-1` 不会与之冲突。
DETACHED_FI = -1
DETACHED_ROOM = "不在基建"          # 该段的行首标签（表格「房间」列）
DETACHED_TITLE = "不在工作设施、也不在宿舍（本班未排班）"     # 段首那一行
#: 段首那句口径（写清楚"这一刻"与"整段"的区别，免得被当 bug）
DETACHED_HINT = ("══ 本班没排到位置：这一刻她不消耗也不回复（心情不变）；"
                 "若她在别的班有活，那些班照常算 ══")
# 说明文字的最大换行宽度：**必须给**，否则一条长 tk.Label 会把设置中心的内容区撑宽
# （实测「干员与心情」因此从 816px 涨到 1247px，超宽被裁）。口径说明都走这个值。
HINT_WRAP = 700

# 表格的**列定义**（表头与每一行共用，保证等宽对齐）：
# `(标题, 像素宽, 是否拉伸, 对齐)`。`宽 = 0` 表示"拉伸列"（吃剩余宽度）。
#
# ⚠️ 为什么用**像素**而不是字符宽（`Label(width=N)`）：字符宽要按字体度量换算，
#    而表头是**粗体**、数据格是常规体 → 同一个 `width=5` 算出的像素不一样，
#    列就歪了（实测：练度列 45 vs 52px、心情列 50 vs 68px、干员列 15 种宽度）。
#    现在列宽完全由 grid 的 `minsize` 说了算，控件一律 `sticky="nsew"` 不再自己撑。
TABLE_COLUMNS = (
    ("房间", 96, False, "w"),
    ("位次", 40, False, "center"),
    ("干员", 0, True, "w"),        # 拉伸：吃剩余宽度
    ("练度", 56, False, "center"),
    ("心情", 72, False, "e"),
    ("说明", 0, True, "w"),        # 拉伸：「不在基建」那段的"她在别的班在哪"
)
#: 拉伸列的最小宽度（内容再窄也不塌）
COL_MIN_STRETCH = 110

#: 行高（**固定**，让滚动位移恒定、列表看起来整齐）
ROW_H = 24
CARD_H = 22
# 单元格文字的**请求宽度上限**：不给的话长名字会把那一列撑宽 → 看起来没对齐。
# 值取"列宽 + 一点余量"：短内容不换行，长内容（如 `其他班：2中 3宿`）被行高裁掉尾巴。
ELITE_PAD = 4
MOOD_PAD = (2, 2)
DASH_WRAP = 130
OP_WRAP = 200
CARD_WRAP = 560

# 滚轮的接线在 `ui/scroll.py`（本实例专属 bindtag；见那里的注释）
ROW_PAD = 1
#: 分组卡片上方的留白（让"每一间房"成为视觉上一块，而不是一长条流水）
PAD_CARD_TOP = 6


def split_names(text: str) -> List[str]:
    """把「粘贴的名单」切成人名列表：换行 / 逗号 / 顿号 / 空格 / 制表符都算分隔符。"""
    out: List[str] = []
    buf = ""
    for ch in text:
        if ch in "\r\n,，、;；\t ":
            if buf:
                out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


class BatchMixin:
    """「干员与心情」的**全部控件与逻辑**：干员 + 练度 + **按时刻显示/指定的心情**，一张表改完。

    宿主只有 `BatchPanel`（设置中心的「干员与心情」分区）；这一层单独抽出来是为了能直接测。

    - `changes`：**改过干员的班次** → `{班次下标: 布局}`（场景格式，可逐班喂 `replaced_shift`）
    - `moods`：要写进 `initial_moods` 的**周期起点**心情（只含与导入值不同的项）
    - `mood_events`：**锚点**（`MoodSetEvent` 列表，整份替换）——在 (周期, 时刻) 指定心情

    `on_change(changes, moods, mood_events)`：改完任一处就回调（设置中心用它做"改动立即生效"）。
    """

    def _init_batch_body(self, parent, schedule, shift_index: int = 0,
                         initial_moods: Optional[Dict[str, Decimal]] = None,
                         imported_moods: Optional[Dict[str, Decimal]] = None,
                         moods_now: Optional[Dict[str, Decimal]] = None,
                         current_t=Decimal("0"), on_change=None, pool=None,
                         table_height=TABLE_H, page_height=0,
                         cycles: int = 1, mood_events=None, moods_at=None,
                         follow_var=None, detached=None, world_at=None):
        """装好状态 + 建出整块控件（宿主的 `__init__` 里调用；`self` 必须是 tk 容器）。

        `pool`：**干员池**（`[{"name","elite","level","own"}]`，来自「导入 v4 蓝图」这类
        文件——那种文件里房间是空的，只给了"我有谁"）。池里的人：
          · 会出现在"选人"搜索窗里（否则空布局里一个人都选不到）；
          · 各自的练度作为默认值（不再是清一色 E2）；
          · 可以一键「从池中依次填入」按顺序铺满当前班次的位置。

        `cycles` / `mood_events`：周期数（时刻行的「周期」下拉有 1~cycles 项）与**已有锚点**；
        `moods_at`：`(绝对时刻) -> {干员: 心情}` 的取值口子（app 给的是轨迹的 `moods_at`），
        心情列"该时刻的实际值"就是它算出来的；`moods_now` 是它不可用时的兜底（测试里用）。
        `world_at`：`(绝对时刻) -> BaseLayout`（app 给的是 `world_at_abs`）——
        **位置列读它**（引擎实际用的那份布局副本）。给不了就退回排班快照。
        ⚠️ 两者会不一样：进驻事件的位置互换与**闲置入宿的换人只改副本**，
        快照里她可能还写着"在宿舍"，而引擎里她已经被换出去（速率为 0）。
        `follow_var`：「跟随滑块」的变量——**由宿主给**（`app.follow_slider`，唯一真源），
        否则这一页被标脏重建时勾选就丢了（切个页回来勾选没了，实测过）。

        `detached`：**「不在基建」名单**（既不在工作设施、也不在宿舍的人；场景 JSON 顶层
        `"detached": [...]`）。表格里会多出**一段**「不在基建」——
        自动列出「本班未排班」的人（含名单里的人），也可以点「＋ 添加干员…」手动加。
        这些人的心情**整段恒定**（不消耗、不回复），而且**不参与任何技能计数**。
        """
        self._on_change = on_change
        self.follow = follow_var if follow_var is not None else tk.BooleanVar(value=False)
        # 表格可视高度：显式数字（独立使用）或 "auto"（设置中心：吃内容区剩余高度）
        self._table_h = None if table_height == "auto" else int(table_height)
        self._page_h = int(page_height) or 0
        #: 上一次量到的"整页超出了内容区多少"（`_fit_table_height()` 用它自校正；见那个方法）
        self._fit_over = 0
        self._pool = [dict(p) for p in (pool or [])]
        self._pool_elite = {p["name"]: int(p["elite"]) for p in self._pool
                            if p.get("elite") is not None}
        self._pool_level = {p["name"]: int(p["level"]) for p in self._pool
                            if p.get("level") is not None}
        self._schedule = schedule
        self._labels = schedule.shift_labels()
        self._shift_index = max(0, min(int(shift_index), len(schedule.shifts) - 1))
        self._current_t = Decimal(str(current_t))
        self._imported: Dict[str, Decimal] = dict(imported_moods or {})
        self._now: Dict[str, Decimal] = dict(moods_now or {})
        self._moods_at = moods_at                  # (绝对时刻) -> {干员: 心情}；可为 None
        self._world_at = world_at                  # (绝对时刻) -> 引擎那份布局；可为 None
        self._where_mismatch: List[str] = []       # 位置与引擎不符的行说明（表头那句提示用）
        self._cycles = max(1, int(cycles or 1))
        self._events: List[MoodSetEvent] = [self._as_event(e) for e in (mood_events or [])]
        # —— 视图（表格在看哪一周期、哪一刻）——
        # 缺省＝"现在这一刻"（滑块在哪就开在哪）；`current_t` 是 0 时退回"选中班次的起点"
        # ——这样 `shift_index` 这个参数仍然有意义（测试与"只看某一班"都靠它）。
        cyc = int(self._current_t // schedule.cycle_hours) + 1
        self._view_cycle = max(1, min(cyc, self._cycles))
        if self._current_t > 0:
            self._view_t = self._current_t - schedule.cycle_hours * (self._view_cycle - 1)
        else:
            self._view_t = schedule.starts[self._shift_index]
        # 干员列**跟着时刻**（时刻在谁那一班就显示谁），不再各指一个时间
        self._shift_index = schedule.index_at(self._view_abs())
        # 全排班的起点心情（一个干员一个值，跨班共用；只在"第 1 周期 0:00"那一格编辑）
        self._moods: Dict[str, Decimal] = {
            n: Decimal(str(self._imported.get(n, MOOD_MAX))) for n in schedule.operator_names()}
        for n, v in (initial_moods or {}).items():
            self._moods[n] = Decimal(str(v))
        self._fac_names: List[dict] = []
        self._level_vars: Dict[int, tk.StringVar] = {}   # 房间下标 → 等级下拉
        self._elite: Dict[str, int] = {}                 # 干员 → 精英化（0/1/2），默认 2
        self._elite_vars: Dict[str, tk.StringVar] = {}   # 干员 → 练度下拉
        self._draft: Dict[int, List[dict]] = {}      # 改过的班次：下标 → 工作副本
        self._fac_index: int = -1                    # 当前载入 `_fac_names` 的是哪一班
        # ——「不在基建」名单（既不在工作设施、也不在宿舍）——
        # `_detached`：用户**显式**加的（写进排班 JSON 顶层 `detached`）；
        # `_detached_auto`：自动列出来的「本班未排班」的人（不进 JSON，只是让人看得见、能设心情）。
        self._detached: List[str] = [str(n) for n in (detached or []) if str(n).strip()]
        self._detached_edited = False                # 用户动过名单没有（没动就不往结果里塞）
        self._detached_auto: List[str] = []
        self._rows: List[dict] = []                  # 行控件（结构没变时复用）
        self._hover_row = None                       # 指针当前所在的行（悬停高亮）
        self._keep_scroll_next = False               # 下一次重建是否保留滚动位置
        self._scroll_pos_save = 0.0                  # 重建前记下的滚动位置
        self._mood_vars: Dict[str, tk.StringVar] = {}
        # 「这一格刚才是我们写的什么值」——`_collect_moods` 靠它区分"用户改过"与"只是刷新过"
        self._shown: Dict[str, Optional[Decimal]] = {}
        self._cells: List[Tuple[int, int]] = []      # 表格里的 (设施下标, 位次)
        self._notify_job = None                      # 心情输入的防抖任务（改动即时生效用）
        self._last_sent: Optional[str] = None        # 上次通知出去的结果（一样就不再通知）

        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", pady=(0, 2))
        tk.Label(head, text="　班次", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.shift_var = tk.StringVar(value=self._labels[self._shift_index])
        self._shift_box = ttk.Combobox(head, textvariable=self.shift_var, state="readonly",
                                      values=self._labels, width=20)
        self._shift_box.pack(side="left")
        self._shift_box.bind("<<ComboboxSelected>>", lambda _e: self._on_shift_change())
        tk.Label(head, text="干员改动只作用于这一班（跟着上面「时刻」走）",
                 bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(8, 0))

        self._sync_facilities()          # 先把工作副本准备好（等级/容量区要用）
        self._build_mood_bar()           # ① 心情区（行数固定）
        self._build_level_bar()          # ② 房间等级区（**行数随房间数变**）
        self._build_op_bar()             # ③ 干员区（固定）
        # ⚠️ 表格**必须最后建**：它是唯一伸缩的一块，高度＝`PAGE_H` − 上面三块 − 报错行
        #    （`_table_height()` 量的就是"已经建出来的兄弟控件总高"）。若放在等级区之前建，
        #    量到的"已用高度"就少了整个等级区（多房间时最多差 ~200px）→ 整页超 `PAGE_H`
        #    被裁、切页时窗口一跳（踩过）。
        self._build_table()              # ④ 表格（吃掉剩余高度）

        self.err = tk.Label(self, text="", bg=theme.BG, fg=theme.DANGER, anchor="w",
                            justify="left", wraplength=HINT_WRAP,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.err.pack(fill="x")
        # 房间数一变（换排班 / 改等级）等级区行数就变 → 表格高度要跟着重算
        self.bind("<Configure>", lambda _e: self._fit_table_height(), add="+")
        self._rebuild_rows()
        # ⚠️ 几何落定前 `winfo_reqheight()` 还是旧值，自校正第一次可能算不出差；
        #    空闲时再修一次就够了（真实的 22 间房用例正是差这 3px）。
        self.after_idle(self._fit_table_height)

    def _notify(self) -> None:
        """改动 → 通知宿主（设置中心＝立即生效）。

        ⚠️ **结果与上次一模一样就不再通知**：宿主那边一次通知＝一整轮重算
        （实测 cycles=1 约 230ms、周期数 3 约 1.2s）。"改回原值""连点同一个按钮"
        以及"程序自己刷新"都不该触发重算。
        """
        if self._notify_job is not None:
            try:
                self.after_cancel(self._notify_job)
            except tk.TclError:
                pass
            self._notify_job = None
        if self._on_change is None:
            return
        res = self.value()
        if res is None:
            return
        sig = repr(res)
        if sig == self._last_sent:
            return
        self._last_sent = sig
        self._on_change(*res)

    def _on_cell_write(self, name: str) -> None:
        """心情输入框被写：**只有"用户改的"才算改动**。

        ⚠️ `StringVar.trace_add("write")` 对**程序自己**的 `var.set()` 一样会触发，而面板
        每次刷新都会把一整列写一遍（`_refresh_mood_cells`）。不区分就是一个自激回路：
        刷新 → 当成用户改动 → 通知 → 宿主重算 → 推送回来 → 又刷新 → ……（实测空转 2 秒
        重算 6 次 / 2336ms，界面一直在烧 CPU —— 就是"设置干员与心情卡顿严重"的根源）。
        判据用 `self._shown`：那是"我们刚写进去的值"，相等就说明这次写不是用户改的。
        """
        var = self._mood_vars.get(name)
        if var is None:
            return
        if parse_mood(var.get()) == self._shown.get(name):
            return                        # 程序写的（数值没变）→ 不算改动
        self._notify_later()

    def _notify_later(self) -> None:
        """心情输入框的防抖：连续敲键盘只落地一次（每次落地都要重算整周期）。

        `NOTIFY_DEBOUNCE_MS`（500ms，原 250ms）：单次重算本身要 0.23~1.2s，防抖间隔比它
        还短的话，打字过程中每个间隔都要冻一下。停手半秒就落地。
        """
        if self._on_change is None:
            return
        if self._notify_job is not None:
            try:
                self.after_cancel(self._notify_job)
            except tk.TclError:
                pass
        self._notify_job = self.after(NOTIFY_DEBOUNCE_MS, self._notify)

    def _cancel_notify(self) -> None:
        if self._notify_job is not None:
            try:
                self.after_cancel(self._notify_job)
            except tk.TclError:
                pass
            self._notify_job = None

    def has_pending_edit(self) -> bool:
        """有没有"还在 500ms 防抖窗口里"的心情输入？（`app` 的异步重算据此决定先别落地）。

        ⚠️ 不这么判的话：重算（异步，0.3~1.5s）可能比防抖先落地，落地刷新会**按轨迹重写格子**
        —— 用户刚敲进去的值就被盖掉、那次编辑整个丢失（实测过）。
        """
        return self._notify_job is not None

    # ================================================================ 房间等级区
    def _build_level_bar(self) -> None:
        """逐间房改**等级**（容量随之变化）——上游 `rooms[].phases[lv].maxStationedNum`。

        为什么要在这里：MAA 排班文件不带等级（导入时按人数推断最低可行等级，
        见 `mood_soc/maa.py`），而等级决定"这间房能放几个人"，改完表格行数要跟着变。
        制造站/贸易站/发电站共用 9 个建造位（上游 `layouts.v0.slots` 的 OUTPUT 槽位），
        所以这里顺带把"已用 N/9"写出来。

        ⚠️ **排成网格**（每行 `LEVEL_COLS` 间房）：示例排班 17 间房，
        挤成一行要 2131px，而内容区只有 ~810px —— 右边那几间会被**裁掉**
        （看不见、也改不了）。网格化之后行数随房间数长，表格高度会**自动让位**
        （`table_height="auto"`），所以不会把整页撑爆。
        """
        box = tk.LabelFrame(self, text="房间等级（决定这间房能放几个人）", bg=theme.BG,
                            fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                            relief="groove", labelanchor="nw")
        box.pack(fill="x", padx=theme.PAD, pady=(0, 4))
        grid = tk.Frame(box, bg=theme.BG)
        grid.pack(fill="x", padx=theme.GAP, pady=(4, 2))
        for i, fac in enumerate(self._fac_names):
            world = self._schedule.shifts[self._shift_index].world.facilities[i]
            cell = tk.Frame(grid, bg=theme.BG)
            cell.grid(row=i // LEVEL_COLS, column=i % LEVEL_COLS, sticky="w",
                      padx=(0, LEVEL_GAP), pady=1)
            tk.Label(cell, text=f"{world.display_name} Lv", bg=theme.BG, fg=theme.MUTED,
                     anchor="e", width=LEVEL_NAME_W,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
            var = tk.StringVar(value=str(int(fac.get("level", world.level))))
            cb = ttk.Combobox(cell, textvariable=var, state="readonly", width=2,
                              values=[str(lv) for lv in
                                      range(1, facility_max_level(world.ftype) + 1)])
            cb.pack(side="left", padx=(3, 0))
            cb.bind("<<ComboboxSelected>>",
                    lambda _e, k=i, v=var: self._on_level_change(k, v))
            self._level_vars[i] = var
        used = sum(1 for i, f in enumerate(self._fac_names)
                   if self._schedule.shifts[self._shift_index].world.facilities[i].ftype
                   in OUTPUT_ROOM_TYPES)
        self.level_note = tk.Label(box, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                                   font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.level_note.pack(fill="x", padx=theme.GAP, pady=(0, 6))
        self._sync_level_note(used)

    def _sync_level_note(self, used: int = None) -> None:
        if used is None:
            used = sum(1 for i in range(len(self._fac_names))
                       if self._schedule.shifts[self._shift_index].world.facilities[i].ftype
                       in OUTPUT_ROOM_TYPES)
        over = used > OUTPUT_SLOT_TOTAL
        self.level_note.configure(
            text=f"制造站/贸易站/发电站已用 {used}/{OUTPUT_SLOT_TOTAL} 个建造位"
                 + ("（超过上游上限！）" if over else "")
                 + "　｜　改等级会立刻改变下面表格的行数",
            fg=(theme.DANGER if over else theme.MUTED))

    def _on_level_change(self, fac_index: int, var) -> None:
        """改房间等级 → 写进工作副本 → 重建表格（容量变了，行数跟着变）。"""
        self._collect_moods()
        self._fac_names[fac_index]["level"] = int(var.get())
        self._mark_dirty()
        self._rebuild_rows()
        self._sync_level_note()
        self._notify()

    # ================================================================ 心情区
    def _build_mood_bar(self) -> None:
        box = tk.LabelFrame(self, text="心情（指定周期内任意时刻的心情；改哪一格＝那一刻给她这个值）",
                            bg=theme.BG, fg=theme.TEXT,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                            relief="groove", labelanchor="nw")
        box.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, 4))

        # —— 第一行：视图（哪一周期、哪一刻）——
        view = tk.Frame(box, bg=theme.BG)
        view.pack(fill="x", padx=theme.GAP, pady=(4, 2))
        tk.Label(view, text="周期", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.view_cycle_var = tk.StringVar(value=str(self._view_cycle))
        cyc_box = ttk.Combobox(view, textvariable=self.view_cycle_var, state="readonly",
                               width=3, values=[str(i + 1) for i in range(self._cycles)])
        cyc_box.pack(side="left", padx=(3, 10))
        cyc_box.bind("<<ComboboxSelected>>", lambda _e: self._on_view_change())
        tk.Label(view, text="时刻", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.view_time_var = tk.StringVar(value=self._view_clock())
        entry = ttk.Entry(view, textvariable=self.view_time_var, width=7)
        entry.pack(side="left", padx=(3, 2))
        entry.bind("<Return>", lambda _e: self._on_view_change())
        entry.bind("<FocusOut>", lambda _e: self._on_view_change())
        left = ttk.Button(view, text="◀", width=3,
                          command=lambda: self._step_view(Decimal("-0.25")))
        left.pack(side="left")
        right = ttk.Button(view, text="▶", width=3,
                           command=lambda: self._step_view(Decimal("0.25")))
        right.pack(side="left", padx=(2, 0))
        home = ttk.Button(view, text="回到周期起点",
                          command=lambda: self._set_view(self._view_cycle, Decimal("0")))
        home.pack(side="left", padx=(6, 0))
        tk.Checkbutton(view, text="跟随滑块", variable=self.follow, bg=theme.BG,
                       activebackground=theme.BG, highlightthickness=0,
                       command=self._on_follow).pack(side="left", padx=(10, 0))
        ttk.Button(view, text="清空本时刻", command=self._clear_view_anchors).pack(
            side="left", padx=(6, 0))
        # 「跟随滑块」打开时这些控件只读（时刻由主界面滑块说了算）
        self._view_widgets = [cyc_box, entry, left, right, home, self._shift_box]
        self._sync_view_widgets()

        # —— 第二行：一键动作（都作用在"这一刻"）——
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x", padx=theme.GAP, pady=(2, 2))
        ttk.Button(row, text="全部满心情 24", command=lambda: self._set_all(MOOD_MAX)
                   ).pack(side="left")
        ttk.Button(row, text="全部 0", command=lambda: self._set_all(MOOD_MIN)).pack(
            side="left", padx=(6, 0))
        self.uniform = tk.StringVar(value="24")
        ttk.Entry(row, textvariable=self.uniform, width=5).pack(side="left", padx=(10, 2))
        ttk.Button(row, text="全部设为这个值", command=self._set_uniform).pack(side="left")
        ttk.Button(row, text="按这一刻回填", command=self._fill_from_now).pack(
            side="left", padx=(10, 0))
        ttk.Button(row, text="恢复导入值", command=self._restore_imported).pack(
            side="left", padx=(6, 0))

        # —— 第三行 + 提示：锚点一览 ——
        self.anchor_note = tk.Label(box, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                                    justify="left", wraplength=760,
                                    font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.anchor_note.pack(fill="x", padx=theme.GAP)
        # 这一行同时兼职「跟随中」的提示（两句话互斥，不额外占高度 —— 内容区是定高的）
        self.mood_hint = tk.Label(box, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                                  font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.mood_hint.pack(fill="x", padx=theme.GAP, pady=(0, 6))
        self._sync_anchor_note()

    # ---------------------------------------------------------------- 视图（周期 + 时刻）
    def _sync_view_widgets(self) -> None:
        """「跟随滑块」打开时，时刻相关控件**只读**（时刻由主界面滑块说了算）+ 换提示语。

        为什么要禁用而不是"允许手改、手改就取消跟随"：跟随时每一帧都会把时刻框写一遍，
        手动输进去的值立刻被覆盖 —— 与其让人"输了没反应"，不如明确置灰。
        """
        if not hasattr(self, "_view_widgets"):
            return
        following = bool(self.follow.get())
        readonly = {self._shift_box, self._view_widgets[0]}      # 这两个本来就是 readonly
        for w in self._view_widgets:
            try:
                w.configure(state=("disabled" if following else
                                   ("readonly" if w in readonly else "normal")))
            except tk.TclError:
                pass
        if hasattr(self, "mood_hint"):
            self.mood_hint.configure(
                text=("跟随中：时刻跟着主界面滑块走（取消勾选后可以手动指定时刻）。"
                      if following else
                      "「按这一刻回填」= 把此刻的实际心情写成指定值（第 1 周期 0:00 就是"
                      "改写周期起点）；只对上面选中的那个周期生效。"))

    @staticmethod
    def _as_event(ev) -> MoodSetEvent:
        """容忍"字典 / 对象"两种写法（`MoodSetEvent` 是 dataclass，测试里也常直接给）。"""
        if isinstance(ev, MoodSetEvent):
            return ev
        return MoodSetEvent(ev["name"], ev["t"], ev["mood"], ev.get("cycle", 1))

    @property
    def _cycle_hours(self) -> Decimal:
        return self._schedule.cycle_hours

    def _view_abs(self, cycle: int = None, t: Decimal = None) -> Decimal:
        """视图时刻换算成"从轨迹起点算起"的绝对时刻。"""
        cyc = self._view_cycle if cycle is None else int(cycle)
        tt = self._view_t if t is None else Decimal(str(t))
        return self._cycle_hours * (cyc - 1) + tt

    def _view_clock(self) -> str:
        """视图时刻 → `HH:MM`（带「初始时间点」的显示口径，见 `Schedule.start_clock`）。"""
        return theme.fmt_clock(self._view_t, self._cycle_hours, self._schedule.start_clock)

    def _is_start_view(self) -> bool:
        """视图是不是「第 1 周期 0:00」——那一格写的是 `initial_moods`（周期起点心情）。"""
        return self._view_cycle == 1 and self._view_t == Decimal("0")

    def _parse_view_clock(self, text: str) -> Optional[Decimal]:
        """把用户输入的钟点解析成"周期内时刻"（接受 `HH:MM` / `H` / `H.MM`）。"""
        raw = (text or "").strip().replace("：", ":")
        if not raw:
            return None
        try:
            if ":" in raw:
                hh, mm = raw.split(":", 1)
                hours = Decimal(hh or "0") + Decimal(mm or "0") / Decimal(60)
            else:
                hours = to_decimal(raw)
        except (InvalidOperation, ValueError, ArithmeticError):
            return None
        # 输入的是**钟点**（带初始时间点）→ 减掉起点、按周期取模，回到"周期内时刻"
        t = (hours - self._schedule.start_clock) % self._cycle_hours
        return t

    def _set_view(self, cycle, t, follow: bool = False, rebuild: bool = True) -> None:
        """换视图（周期 + 周期内时刻）：必要时**把班次也切到那一刻所在的那一班**。"""
        cycle = max(1, min(int(cycle), self._cycles))
        t = Decimal(str(t)) % self._cycle_hours
        self._view_cycle, self._view_t = cycle, t
        self.view_cycle_var.set(str(cycle))
        self.view_time_var.set(self._view_clock())
        if not follow and self.follow.get():
            # 手动指定时刻 ＝ 明确不要跟随了（不再静默取消：提示行会写出来）
            self.follow.set(False)
            self._sync_view_widgets()
        if rebuild:
            self._sync_shift_from_view(refresh=True)

    def _on_view_change(self) -> None:
        """时刻输入框 / 周期下拉改了：解析 → 换视图。

        ⚠️ **值没变就什么都不做**：这个回调还挂在 `<FocusOut>` 上，而"点一下输入框再点别处"
        会带着**同样的时刻**进来。老实现无条件走 `_set_view`（默认会取消「跟随滑块」），
        于是"什么都没改，勾选却自己掉了"——实测过。这里先比对，相同就直接返回。
        """
        t = self._parse_view_clock(self.view_time_var.get())
        if t is None:
            self._error("时刻要写成 HH:MM（例如 01:30 或 13:00）")
            self.view_time_var.set(self._view_clock())
            return
        try:
            cycle = int(self.view_cycle_var.get())
        except (TypeError, ValueError):
            cycle = self._view_cycle
        cycle = max(1, min(cycle, self._cycles))
        if (cycle, t) == (self._view_cycle, self._view_t):
            return                          # 没改（焦点进出也会走到这里）→ 零副作用
        self._collect_moods()               # 先把旧视图里改过的格子收进来
        self.err.configure(text="")
        self._set_view(cycle, t)

    def _step_view(self, delta: Decimal) -> None:
        """`◀ / ▶`：在**同一周期内**挪 15 分钟（跨过末尾就从周期开头接上）。"""
        self._collect_moods()
        self._set_view(self._view_cycle, self._view_t + delta)

    def _on_follow(self) -> None:
        """「跟随滑块」：勾上就跟着主界面的时刻走（取消＝停在原地自己定）。

        开关状态本身存在 `app.follow_slider` 上（宿主给的变量），所以这一页被重建、
        切到别的分区再回来，勾选都还在。这里只负责"立刻跟到现在这一刻"+ 控件置灰。
        """
        self._sync_view_widgets()
        if not self.follow.get():
            return
        self._collect_moods()
        self._set_view_from_abs(self._current_t)

    def _set_view_from_abs(self, t_abs) -> None:
        """按**绝对时刻**定位视图（跟随滑块用：周期与周期内时刻一起算出来）。"""
        t_abs = Decimal(str(t_abs))
        cyc = int(t_abs // self._cycle_hours) + 1
        cyc = max(1, min(cyc, self._cycles))
        self._view_cycle = cyc
        self._view_t = (t_abs - self._cycle_hours * (cyc - 1)) % self._cycle_hours
        self.view_cycle_var.set(str(cyc))
        self.view_time_var.set(self._view_clock())
        self._sync_shift_from_view(refresh=True)

    def _sync_shift_from_view(self, refresh: bool = False) -> None:
        """干员列跟着视图走：切到"这一刻所在的那一班"，然后重建表格。

        `refresh=True` 时顺带把心情列刷成该时刻的实际值（换时刻的核心动作）。
        ⚠️ "载入的布局是不是这一班"要看 `self._fac_index`，不能只看 `_shift_index`
        ——调用方可能刚把它设成目标值，那样就漏掉 `_sync_facilities()`，
        表格会停留在上一班的布局（曾经就这样：切班次后干员列没跟着换）。
        """
        idx = self._schedule.index_at(self._view_abs())
        if idx != self._fac_index:
            self._shift_index = idx
            self.shift_var.set(self._labels[idx])
            self._sync_facilities()
            self._rebuild_rows()
        if refresh:
            # ⚠️ 结构没变时**只刷心情列**，不要再走一次 `_rebuild_rows()`：它会给 50 行
            #    重新 `configure` + 新建 `StringVar`，而"跟随滑块"每帧都要走这里（实测
            #    每帧多花几十毫秒，播放就掉帧）。只有"换了一班"才需要重建那些行。
            self._refresh_mood_cells()
            self._sync_anchor_note()

    # ---------------------------------------------------------------- 锚点（心情指定事件）
    def _events_at_view(self) -> List[MoodSetEvent]:
        return [e for e in self._events
                if e.cycle == self._view_cycle and e.t == self._view_t]

    def _anchor_of(self, name: str) -> Optional[Decimal]:
        for e in self._events_at_view():
            if e.name == name:
                return e.mood
        return None

    def _set_anchor(self, name: str, value: Decimal) -> None:
        """写（或覆盖）某个干员在**当前视图时刻**的锚点。"""
        if self._is_start_view():
            self._moods[name] = value          # 第 1 周期 0:00 ＝ 周期起点心情
            return
        self._events = [e for e in self._events
                        if not (e.name == name and e.cycle == self._view_cycle
                                and e.t == self._view_t)]
        self._events.append(MoodSetEvent(name, self._view_t, value, self._view_cycle))

    def _write_view_mood(self, name: str, value: Decimal) -> None:
        """把"这一刻的心情"写成指定值（起点视图走 `initial_moods`，其余走锚点）。"""
        self._set_anchor(name, Decimal(str(value)))

    def _drop_anchor(self, name: str) -> None:
        self._events = [e for e in self._events
                        if not (e.name == name and e.cycle == self._view_cycle
                                and e.t == self._view_t)]

    def _clear_view_anchors(self) -> None:
        """清掉**这一刻**的锚点（起点视图＝把手动起点心情退回导入值）。"""
        self._collect_moods()
        if self._is_start_view():
            for n in list(self._moods):
                self._moods[n] = Decimal(str(self._imported.get(n, MOOD_MAX)))
        n = len(self._events_at_view())
        self._events = [e for e in self._events if e not in self._events_at_view()]
        self.err.configure(text=(f"已清空这一刻的 {n} 条锚点" if n else "这一刻本来没有锚点"))
        self._refresh_mood_cells()
        self._sync_anchor_note()
        self._notify()

    def _clear_all_anchors(self) -> None:
        """清掉**全部**锚点（`恢复导入值` 用：连周期起点心情一起退回导入值）。"""
        for n in list(self._moods):
            self._moods[n] = Decimal(str(self._imported.get(n, MOOD_MAX)))
        self._events = []

    def _table_names(self) -> List[str]:
        """表格里出现过的干员名（含「不在基建」那一段）。"""
        return [n for n in self._mood_vars if n]

    def _bulk_names(self) -> List[str]:
        """一键动作的作用范围：**周期起点**对全排班的人都有意义（老口径也是这个范围）；
        其余时刻只对"表格里这些位置"有意义（那一刻她得在基建里）。"""
        return list(self._moods) if self._is_start_view() else self._table_names()
    def _view_moods(self) -> Dict[str, Decimal]:
        """**这一刻的实际心情**。

        - **周期起点（第 1 周期 0:00）**：用显式起点心情（`self._moods`）——这正是引擎在
          周期起点用的那组值（缺省＝导入值），也就是老口径的"周期起点心情"；
        - **其余时刻**：由轨迹算出来的**实际值**（锚点已生效在轨迹里），
          所以换时刻就能看到各人心情按正常演化变成多少。

        ⚠️ 「不在基建」的人**不一定**在轨迹的 `moods_at` 里（例如手工加进来、还没重算过的），
        所以这里按 `self._moods` 兜一层底：他们的心情**整段恒定**，用起点值显示是对的。
        """
        if self._is_start_view():
            return {n: v for n, v in self._moods.items()}
        out: Dict[str, Decimal] = {}
        if self._moods_at is not None:
            try:
                got = self._moods_at(self._view_abs())
            except Exception:                     # 没轨迹 / 还没算完 → 退到兜底值
                got = None
            if got:
                out = {n: Decimal(str(v)) for n, v in got.items()}
        else:
            out = dict(self._now)
        for n in self._detached_all():
            if n not in out:
                out[n] = self._moods.get(n, Decimal(str(self._imported.get(n, MOOD_MAX))))
        return out

    def _sync_anchor_note(self) -> None:
        """锚点一览（按绝对时刻排序）：让人知道"哪一刻被指定过、指定成了多少"。"""
        if not hasattr(self, "anchor_note"):
            return
        if not self._events:
            self.anchor_note.configure(text="锚点：无（列里显示的是一路演化出来的实际心情）",
                                       fg=theme.MUTED)
            return
        items = []
        stale = 0
        for e in sorted(self._events, key=lambda e: (e.cycle, e.t, e.name)):
            if e.cycle > self._cycles:
                stale += 1
                continue
            clock = theme.fmt_clock(e.t, self._cycle_hours, self._schedule.start_clock)
            items.append(f"第{e.cycle}周期 {clock} {e.name}={theme.fmt_mood(e.mood)}")
        text = "锚点：" + "、".join(items) if items else "锚点：无（当前周期数下都不生效）"
        if stale:
            text += f"　⚠ {stale} 条落在周期 {self._cycles} 之外（把周期数调大才生效）"
        self.anchor_note.configure(text=text, fg=theme.MUTED)

    # ---------------------------------------------------------------- 一键动作
    def _set_all(self, value: Decimal) -> None:
        for n in self._bulk_names():
            self._write_view_mood(n, value)
        self._refresh_mood_cells()
        self._sync_anchor_note()
        self.err.configure(text="")
        self._notify()

    def _set_uniform(self) -> None:
        v = parse_mood(self.uniform.get())
        if v is None:
            self._error("「全部设为」需要一个 0 ~ 24 的数字")
            return
        self._set_all(v)

    def _fill_from_now(self) -> None:
        """把**这一刻的实际心情**写成指定值（起点视图＝把这一刻当作新的周期起点）。

        数值本来是"一路演化出来的"，这里只是把它**钉下来**变成显式设定：
        改排班/改时刻之后它就不会跟着飘了。
        """
        moods = self._view_moods()
        if not moods:
            self._error("还没有轨迹可以回填（先导入排班）")
            return
        n = 0
        for name in self._bulk_names():
            if name in moods:
                self._write_view_mood(name, moods[name])
                n += 1
        self._refresh_mood_cells()
        self._sync_anchor_note()
        where = "周期起点" if self._is_start_view() else f"第 {self._view_cycle} 周期 {self._view_clock()}"
        self.err.configure(text=f"已把 {where} 的实际心情写成指定值（{n} 名）")
        self._notify()

    def _restore_imported(self) -> None:
        """退回导入值：**清掉全部锚点 + 手动起点心情**（真正回到刚导入的状态）。"""
        n = len(self._events)
        self._clear_all_anchors()
        self._refresh_mood_cells()
        self._sync_anchor_note()
        self.err.configure(text=f"已恢复导入值（清掉 {n} 条锚点与手动起点心情）"
                                if n else "已恢复导入值")
        self._notify()

    # ---------------------------------------------------------------- 不在基建
    def _detached_all(self) -> List[str]:
        """「不在基建」那一段要列的人：用户**显式**加的 + 自动列出的（去重保序）。"""
        out: List[str] = []
        for n in list(self._detached) + list(self._detached_auto):
            n = str(n or "").strip()
            if n and n not in out:
                out.append(n)
        return out

    def _sync_detached(self) -> None:
        """重算自动名单：**本班次没排到位置**的人（含"整个排班都没排到"的）。

        口径：`schedule.operator_names()`（全排班人员 + 显式「不在基建」名单）
        减去**当前这一班的房间里的位置**。所以"某个班换下来休息的人"会自动出现，
        "整个排班都没安排的板凳干员"要么在名单里、要么用「＋ 添加干员…」加进来。
        """
        if self._schedule is None:
            self._detached_auto = []
            return
        idx = max(0, min(self._shift_index, len(self._schedule.shifts) - 1))
        here = set()
        for f in self._schedule.shifts[idx].world.facilities:
            here.update(o.name for o in f.operators)
            here.update(o.name for o in f.deputies)
        auto = [n for n in self._schedule.operator_names() if n not in here]
        for n in self._detached:                 # 显式加的人一律列出（哪怕本班在岗）
            if n not in auto:
                auto.append(n)
        self._detached_auto = auto

    def _detached_out(self) -> Optional[List[str]]:
        """结果里的「不在基建」名单：用户**动过**才给（否则 `None` = 别覆盖已有的）。"""
        return list(self._detached) if self._detached_edited else None

    def _set_detached_names(self, names: Sequence[str]) -> None:
        """整份替换**显式**名单（去重保序）并刷新表格。"""
        out: List[str] = []
        for n in names:
            n = str(n or "").strip()
            if n and n not in out:
                out.append(n)
        self._detached = out
        self._detached_edited = True
        self._sync_detached()
        self._rebuild_rows()
        self._notify()

    def _detach_operator(self, name: str) -> None:
        """把某人放进「不在基建」：**先从所有班次的位置里摘下来**，再进名单。

        为什么要摘位置：留在位置上她其实在岗（有消耗、能被选中当技能对象），
        与"不在基建"自相矛盾；而且那样她会在表格里出现两次（房间那一段 + 不在基建那一段）。
        """
        name = str(name or "").strip()
        if not name:
            return
        moved = 0
        for i, shift in enumerate(self._schedule.shifts):
            facs = self._draft.get(i)
            if facs is None:
                # 工作副本口径与 `_sync_facilities` 一致：**按位次的名字列表**（空串＝空槽），
                # 且**不留** `slots`（否则两种写法并存、`slots` 优先 ⇒ 摘人静默失效）。
                facs = []
                for f in shift.facilities:
                    fac = dict(f)
                    fac.pop("slots", None)
                    fac["operators"] = seat_values(f)
                    facs.append(fac)
            hit = False
            for f in facs:
                ops = list(f.get("operators", []))
                # ⚠️ 摘人**留洞、不左移**：位次是正式概念 —— 把后面的人往前挪会让
                #    "第 3 位"变成"第 2 位"，而手动台账与锁定区都按位次判。
                kept = ["" if n == name else n for n in ops]
                if kept != ops:
                    hit = True
                    f["operators"] = kept
            if hit:
                self._draft[i] = facs
                moved += 1
        self._collect_moods()
        self._detached = list(dict.fromkeys(list(self._detached) + [name]))
        self._detached_edited = True
        # ⚠️ 工作副本里也要跟着摘掉：`_sync_detached` 是"看布局"算自动名单的，
        #    不摘的话她明明已经在名单里、却仍被当成"本班在岗"（于是上面那段还画着她）。
        if self._shift_index in self._draft:
            self._fac_names = self._draft[self._shift_index]
        self._sync_detached()
        self._rebuild_rows()
        self._notify()
        self.err.configure(text=f"已把 {name} 移到「不在基建」"
                                + (f"（并从 {moved} 个班次的位置上摘下）" if moved else "")
                                + "；她的心情整段不变")

    def _undetach_operator(self, name: str) -> None:
        """把某人移出**显式**名单（房间位置不会自动恢复，要回岗请在上面的表里选人）。"""
        self._collect_moods()
        self._set_detached_names([n for n in self._detached if n != name])
        self.err.configure(text=f"已把 {name} 移出「不在基建」名单"
                                + ("" if name not in self._detached_auto
                                   else "（本班仍未排班，所以她还在这一段的自动名单里）"))

    def _detached_elsewhere(self, name: str) -> str:
        """她**在别的班**在哪（本班没排到 ≠ 整份排班都不在基建）。

        返回紧凑标记（如 `其他班：2中 3宿`）；哪儿都没排到就返回 `""`。
        界面把这一句放在「不在基建」那一段的 `—` 列里：让人一眼看出
        "这一刻她不在基建"与"她其实还有班"的区别。
        """
        if self._schedule is None:
            return ""
        marks: List[str] = []
        for i, s in enumerate(self._schedule.shifts):
            if i == self._shift_index:
                continue
            fac = s.world.facility_of(name)
            if fac is None:
                continue
            marks.append(f"{i + 1}{facility_tag(fac)}")
        return ("其他班：" + " ".join(marks)) if marks else ""

    def _engine_world(self):
        """**引擎这一刻实际用的那份布局**（模拟副本）；拿不到就返回 `None`。

        ⚠️ 位置列要读它，别读 `self._schedule.shifts[i].world`（那是"你导入的排班"）：
        进驻事件的位置互换与**闲置入宿的换人只改副本** —— 换过人之后，快照里她可能还写着
        "在宿舍 / 在贸易站"，而引擎里她已经被换出去（不在基建、速率 0、心情平线）。
        """
        if self._world_at is None:
            return None
        try:
            return self._world_at(self._view_abs())
        except Exception:            # noqa: BLE001 —— 宿主给的口子坏了不该拖垮表格
            return None

    def _where_note(self, name: str, snap_where: str) -> str:
        """她**这一刻引擎把她放在哪**，与快照不一致时返回一句人话；一致就返回 `""`。

        典型两种（实测都出现过）：
          · `本班已被闲置入宿换出（快照写 宿舍#1）` —— 引擎里她不在基建、速率是 0；
          · `本班已进 宿舍#4（快照未写）` —— 引擎把她放进宿舍恢复，快照里她是"未排班"。
        """
        world = self._engine_world()
        if world is None:
            return ""
        fac = world.facility_of(name)
        engine_where = fac.display_name if fac is not None else "不在基建"
        if engine_where == (snap_where or ""):
            return ""
        if engine_where == "不在基建":
            return f"⇄ 本班被闲置入宿换出（快照写 {snap_where}）"
        if not snap_where or snap_where == "不在基建":
            return f"⇄ 本班已进 {engine_where}（快照未写）"
        return f"⇄ 实际在 {engine_where}（快照写 {snap_where}）"

    def _add_detached(self) -> None:
        """「＋ 添加干员…」：从全量名册 / 干员池里选一个人，放进「不在基建」。"""
        self._collect_moods()
        names = list(self._schedule.operator_names()) if self._schedule else []
        for n in self._pool_names():
            if n not in names:
                names.append(n)
        for n in all_operator_names():           # 全量名册（含从没排过班的人）
            if n not in names:
                names.append(n)
        picked = ask_operator(self, names, "",
                              title="添加「不在基建」的干员（不在工作设施、也不在宿舍）")
        if not picked:
            return
        self._detach_operator(picked)
        self._sync_facilities()                  # 位置可能被摘掉了 → 上面那段表格也要刷
        self._rebuild_rows()

    # ================================================================ 干员区
    def _build_op_bar(self) -> None:
        box = tk.LabelFrame(self, text="干员（只改上面选中的这一班）", bg=theme.BG,
                            fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                            relief="groove", labelanchor="nw")
        box.pack(fill="x", padx=theme.PAD, pady=(0, 4))
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x", padx=theme.GAP, pady=(4, 2))
        ttk.Button(row, text="批量粘贴名单…", command=self._paste_names).pack(side="left")
        ttk.Button(row, text="从池中依次填入", command=self._fill_from_pool).pack(
            side="left", padx=(6, 0))
        ttk.Button(row, text="清空本班次", command=self._clear_shift).pack(side="left",
                                                                          padx=(6, 0))
        ttk.Button(row, text="全部设为 E2", command=lambda: self._set_all_elite(2)).pack(
            side="left", padx=(6, 0))
        ttk.Button(row, text="＋ 添加干员…", command=self._add_detached).pack(
            side="left", padx=(6, 0))
        self.show_empty = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="显示空位", variable=self.show_empty, bg=theme.BG,
                       activebackground=theme.BG, highlightthickness=0,
                       command=self._rebuild_rows).pack(side="left", padx=(10, 0))
        self.pool_note = tk.Label(box, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                                  justify="left", wraplength=HINT_WRAP,
                                  font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.pool_note.pack(fill="x", padx=theme.GAP)
        self._sync_pool_note()
        tk.Label(box, text="「批量粘贴名单」＝一行一个（逗号/空格也行），按房间顺序依次填入；"
                           "点表格里的干员名可以搜索更换。",
                 bg=theme.BG, fg=theme.MUTED, anchor="w", justify="left",
                 wraplength=HINT_WRAP,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(fill="x", padx=theme.GAP,
                                                               pady=(0, 6))

    # ---------------------------------------------------------------- 干员池
    def _sync_pool_note(self) -> None:
        """池的状态那一行（没池就说清"为什么这按钮是灰的"）。"""
        if not hasattr(self, "pool_note"):
            return
        if not self._pool:
            self.pool_note.configure(
                text="干员池：空（只有「v4 蓝图 + 干员池」那类文件会带池；"
                     "MAA 排班与 v3 输出本身就带了人员安排）")
            return
        names = "、".join(p["name"] for p in self._pool[:6])
        more = f" 等 {len(self._pool)} 名" if len(self._pool) > 6 else ""
        bench = self._detached_all()
        hint = (f"　｜　不在基建 {len(bench)} 名（表格末尾那一段）" if bench else "")
        self.pool_note.configure(text=f"干员池：{names}{more}（选人时可搜到，"
                                      f"练度用池里的值）{hint}")

    def _pool_names(self) -> List[str]:
        return [p["name"] for p in self._pool]

    def _fill_from_pool(self) -> None:
        """把池里的干员**按顺序**铺满当前班次的位置（可再手改）。

        为什么要有它：v4 那类文件只给"蓝图 + 我有谁"，房间里没有任何人——
        没有这一键，用户就得一个个点位置把人放进去。
        """
        if not self._pool:
            self._error("这份排班没有干员池（只有「v4 蓝图 + 干员池」文件才带池）")
            return
        self._collect_moods()
        names = [p["name"] for p in self._pool]
        self._apply_names(names, clear_first=True)
        self.err.configure(text=f"已按池顺序填入 {min(len(names), len(self._all_slots()))} 人"
                                "（可继续手改；点位置可以换人）")

    # ================================================================ 表格
    def _build_table(self) -> None:
        """表格区 = **搜索栏 + 表头（固定）+ 可滚动主体**（三块共用一个列定义）。

        为什么要表头与等宽列：以前每一行是自己 pack 出来的，数值列歪歪扭扭、也没有表头，
        61 行看下来很累。现在列宽由 `TABLE_COLUMNS` 统一给（grid），表头写一次列名。
        """
        tk.Label(self, text="表格", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL, "bold")).pack(anchor="w",
                                                                        padx=theme.PAD,
                                                                        pady=(theme.PAD, 0))
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x", padx=theme.PAD)
        tk.Label(bar, text="搜索", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.filter = tk.StringVar(value="")
        self.filter_box = ttk.Entry(bar, textvariable=self.filter, width=18)
        self.filter_box.pack(side="left", padx=(6, 4))
        self.filter.trace_add("write", lambda *_a: self._on_filter_change())
        self.filter_box.bind("<Escape>", lambda _e: self.filter.set(""))
        self.filter_note = tk.Label(bar, text="", bg=theme.BG, fg=theme.MUTED,
                                    font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.filter_note.pack(side="left", padx=(2, 0))
        # ⚠️ 这一行别写长：工具栏的**自然宽度**会顶到设置中心的内容区（超了就被裁）。
        #    实测超过 ~816px 就红（2026-09 踩过一次）。完整说明写进 documents/10-图形界面.md。
        tk.Label(bar, text="滚轮＝3 行　Shift＝整页　Ctrl＝10 行　PgUp/PgDn＝翻页",
                 bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="right")

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(2, 0))
        # 表头与主体**用同一套像素列定义**，因此天然对齐（表头不滚动，始终可见）。
        # 「不在基建」那段的**口径说明也放在这里**（一行通栏）：这样段首卡片只有 22px，
        # 与房间卡片一样高 —— 行高统一，滚起来才顺（见 §5 第 20 条）。
        head = tk.Frame(body, bg=theme.HEADER_BG, highlightbackground=theme.BORDER,
                        highlightthickness=1)
        head.pack(fill="x")
        self._setup_columns(head)
        for ci, (title, _w, _stretch, anchor) in enumerate(TABLE_COLUMNS):
            tk.Label(head, text=title, bg=theme.HEADER_BG, fg=theme.MUTED,
                     anchor=anchor, padx=4,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL, "bold")
                     ).grid(row=0, column=ci, sticky="nsew", padx=0, pady=2)
        # 通栏的第二行：口径说明（不参与列对齐，所以单独一个 Label 跨全部列）
        self.head_note = tk.Label(head, text=DETACHED_HINT, bg=theme.HEADER_BG,
                                  fg=theme.MUTED, anchor="w", padx=4,
                                  font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.head_note.grid(row=1, column=0, columnspan=len(TABLE_COLUMNS), sticky="ew")
        self.canvas = tk.Canvas(body, bg=theme.PANEL, highlightthickness=0,
                                height=self._table_height(),
                                highlightbackground=theme.BORDER)
        self.scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=theme.PANEL)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # ⚠️ **列宽只在 `inner` 上配一次**（表头自己配一份）：grid 的列宽是按容器算的，
        #    每行一个独立 grid 只能保证"行内对齐"、跨行还会差几个像素（实测 570 vs 561）。
        #    现在所有行都 `grid(row=N)` 嵌在 `inner` 上 —— 列宽只有一个来源。
        self._setup_columns(self.inner)
        # 滚轮：表格区域 + **每一行** + 滚动条都能滚（见 ui/scroll.py 的接线说明）
        self.vs = VScroll(self.canvas, self.scroll, self.inner, win=self._win)

    def _on_canvas_configure(self, event) -> None:
        """画布宽度变了 → 让内容跟着变宽（列宽由 `inner` 的 grid 分配）。"""
        try:
            self.canvas.itemconfigure(self._win, width=event.width)
        except tk.TclError:
            pass

    def _setup_columns(self, frame: tk.Widget) -> None:
        """配列宽/拉伸（**共享容器 `inner` 配一次**；表头自己配一次）。

        ⚠️ **列宽必须只有一个来源**：grid 的列宽是"按容器"算的。过去每一行都是独立的
        Frame（各自 grid），于是列宽只在行内成立、跨行仍会差（实测空位行的 `—` 在 x=570、
        `不在基建` 行在 x=561，肉眼就是"没对齐"）。现在所有行都嵌在同一个 `inner` 上。
        """
        for ci, (_t, width, stretch, _a) in enumerate(TABLE_COLUMNS):
            if stretch:
                frame.columnconfigure(ci, weight=1, minsize=COL_MIN_STRETCH)
            else:
                frame.columnconfigure(ci, weight=0, minsize=width)

    @staticmethod
    def _text(parent, text: str, bg: str, fg: str, column: int, *,
              anchor: str = "w", bold: bool = False, padx: int = 0,
              wrap: int = 0, span: int = 0, sticky: str = "nsew", grid_row: int = 0):
        """建一个**不会把列撑宽**的文字标签（表格里的统一做法）。

        ⚠️ 关键在 `wraplength`：不给它，一个长名字（实测最长 307px）会让 grid 把整列
        撑到那么宽 —— 于是"某几行的这一列特别宽"，肉眼看就是**没对齐**（只有 `minsize`
        挡不住内容请求）。给一个上限之后，请求宽度就被夹住了（行高固定 24px，
        超出部分由 Tk 直接裁掉）。
        """
        font = (theme.FONT_FAMILY, theme.FS_SMALL, "bold") if bold else \
               (theme.FONT_FAMILY, theme.FS_SMALL)
        lbl = tk.Label(parent, text=text, bg=bg, fg=fg, anchor=anchor,
                       font=font, padx=padx, **({"wraplength": wrap} if wrap else {}))
        lbl.grid(row=grid_row, column=column, sticky=sticky,
                 **({"columnspan": span} if span else {}))
        return lbl

    def _table_height(self) -> int:
        """表格可视高度：显式给了就用它；`"auto"`（设置中心）就**吃掉内容区的剩余高度**。

        为什么要 auto：等级区是网格，房间多的时候会占 2~5 行 —— 固定高度要么撑爆内容区、
        要么把等级区挤掉。让它吃剩余高度，等级区多高都不怕。

        ⚠️ 这里只能**估**（建表格时搜索栏/表头还没建出来），少扣 1px 整页就会超 `PAGE_H`
        （内容被裁、切页时窗口跳 —— 实测 22 间房就因此超了 3px）。所以真正保证"装得下"
        的是 `_fit_table_height()` 的**自校正**：它量完实际高度再补差，不靠常量凑。
        """
        if self._table_h is not None:
            return int(self._table_h)
        self.update_idletasks()
        used = sum(w.winfo_reqheight() for w in self.winfo_children())
        return max(TABLE_H_MIN, int(self._page_h) - used - 30 - TABLE_CHROME)

    def _fit_table_height(self) -> None:
        """`"auto"` 模式下**重新按当前内容**给表格定高，并**自校正到整页装得下**。

        ⚠️ 只在建的时候算一次是不够的：等级区的行数随房间数变（22 间房要 5 行 ≈ +200px），
        `_table_height()` 又是"建表格之前"跑的估计，量不到的东西（搜索栏、表头、报错行、
        各层 pady）一多就会超。所以这里用**实际值**补差：

        1. 先按当前内容算出目标高度；
        2. `update_idletasks()` 之后量面板的真实请求高度，**超了就把表格压回去**，
           直到装得下或压到 `TABLE_H_HARD_MIN`（值不变就不 configure，不会死循环）。

        这样"表格上方又加了一行说明"之类的小改动不会再把整页顶出内容区。
        """
        if self._table_h is not None or not hasattr(self, "canvas"):
            return
        target = self._table_height()
        if int(self._page_h) > 0:
            # ⚠️ 这里**不能** `update_idletasks()`（那会重入正在派发的 `<Configure>`，
            #    在构造期把别的控件搅乱 —— 实测触发 `invalid command name` 连片报错）。
            #    用"上一次的真实高度"来算差：面板每变一次尺寸就再修一次，几帧内收敛。
            over = self._fit_over
            if over > 0:
                target = max(TABLE_H_FLOOR, target - over)
        try:
            if int(self.canvas.cget("height")) != target:
                self.canvas.configure(height=target)
                self.vs.refresh(settle=False)
        except (tk.TclError, ValueError):
            pass
        self._fit_over = max(0, self.winfo_reqheight() - int(self._page_h))

    def _sync_facilities(self) -> None:
        """把选中班次的布局拷成可改的形式。

        改过的班次会留在 `self._draft` 里：切到别的班次再切回来，**未应用的改动不会丢**
        （不然"改完第 1 班顺手去看第 2 班"就把第 1 班的改动冲掉了）。
        """
        idx = self._shift_index
        self._fac_index = idx          # "现在载入的是哪一班的布局"（切班次/换时刻的判据）
        if idx in self._draft:
            self._fac_names = self._draft[idx]
            self._sync_detached()
            return
        shift = self._schedule.shifts[idx]
        self._fac_names = []
        for f in shift.facilities:
            fac = dict(f)
            # 干员可能是字符串，也可能是带练度的对象 `{"name": ..., "elite": ...}`
            # （上一轮改过练度就是这样存的）→ 这里统一成"名字列表 + self._elite"。
            # ⚠️ **位次一律按位次读**（`seat_specs` 里 `slots` 优先）：只读 `operators`
            #    会在"留过空洞"的设施上读成**空房间** —— 显示为空、写回也失效（修过的 bug）。
            #    工作副本统一成"**按位次对齐的名字列表**"（空串＝这一格空着、**不左移**），
            #    并把过时的 `slots` 摘掉；出口 `value()` 用 `write_seats` 收敛回唯一一种
            #    正式写法（紧凑 `operators` / 带空洞 `slots`）。
            names = []
            for spec in seat_specs(f):
                if isinstance(spec, dict):
                    name = str(spec.get("name") or "")
                    if name:
                        self._elite[name] = int(spec.get("elite", 2))
                else:
                    name = str(spec or "")
                names.append(name)
            fac.pop("slots", None)
            fac["operators"] = names
            self._fac_names.append(fac)
        for op in shift.world.all_operators():           # 练度预填（默认 E2 满练）
            self._elite.setdefault(op.name, int(op.elite))
        for p in self._pool:                             # 池里的练度也算缺省（v4 蓝图用得上）
            if p.get("elite") is not None:
                self._elite.setdefault(p["name"], int(p["elite"]))
        self._sync_detached()        # 「不在基建」那一段的自动名单（本班未排班的人）

    def _mark_dirty(self) -> None:
        """记下"这一班的干员被改过"，并把工作副本留给切班次后复用。"""
        self._draft[self._shift_index] = self._fac_names

    def _slot_count(self, fac: dict, fac_index: int) -> int:
        """这一行房画几个位置（容量以外若还有人也画出来，与看板同一口径）。

        ⚠️ 容量按**工作副本里的等级**算（不是模型那一份）——否则在上面改了等级，
        下面表格的行数不会跟着变。上游：`rooms[].phases[lv].maxStationedNum`。
        """
        world = self._schedule.shifts[self._shift_index].world.facilities[fac_index]
        cap = facility_slots(world.ftype, int(fac.get("level", world.level)))
        return max(cap, len(fac.get("operators", [])), 1)

    def _room_name(self, fac_index: int) -> str:
        """房间显示名（用模型侧的 `display_name`，如"制造站#2"）。"""
        return self._schedule.shifts[self._shift_index].world.facilities[fac_index].display_name

    def _room_header(self, fac_index: int) -> str:
        """房间分组卡片的标题：`制造站#1 · Lv3 · 3/3 人`（人数按工作副本）。"""
        fac = self._fac_names[fac_index]
        ops = len([n for n in fac.get("operators", []) if n])
        cap = self._slot_count(fac, fac_index)
        return f"{self._room_name(fac_index)} · Lv{int(fac.get('level', 1))} · {ops}/{cap} 人"

    #: 行计划里的三类行：房间分组标题 / 一个位置 / 一个「不在基建」的人
    KIND_ROOM = "room"
    KIND_SLOT = "slot"
    KIND_BENCH = "bench"

    def _row_plan(self) -> List[tuple]:
        """表格要画哪些行 → `[(kind, 设施下标, 位次, 干员名, 标签)]`。

        - `KIND_ROOM`：**房间分组卡片**（一行通栏，写 `制造站#1 · Lv3 · 3/3 人`）；
        - `KIND_SLOT`：一个位置的干员（`设施下标` / `位次` 有意义）；
        - `KIND_BENCH`：**「不在基建」那一段**的人（`设施下标 = DETACHED_FI`）。
        """
        plan: List[tuple] = []
        for fi, fac in enumerate(self._fac_names):
            ops = list(fac.get("operators", []))
            rows = []
            for si in range(self._slot_count(fac, fi)):
                name = ops[si] if si < len(ops) else ""
                if not name and not self.show_empty.get():
                    continue
                rows.append((self.KIND_SLOT, fi, si, name, ""))
            if not rows:
                continue
            plan.append((self.KIND_ROOM, fi, 0, "", self._room_header(fi)))
            plan.extend(rows)
        detached = self._detached_all()
        rows = [(self.KIND_BENCH, DETACHED_FI, i + 1, n, "") for i, n in enumerate(detached)]
        rows = [r for r in rows if self._match_filter(r[3], "")]
        if rows:
            plan.append((self.KIND_ROOM, DETACHED_FI, 0, "", DETACHED_TITLE))
            plan.extend(rows)
        if not self._filter_text():
            return plan
        return [r for r in plan
                if r[0] == self.KIND_ROOM
                or self._match_filter(r[3], self._row_filter_label(r))]

    # ---------------------------------------------------------------- 过滤
    def _filter_text(self) -> str:
        return (self.filter.get() if hasattr(self, "filter") else "").strip().lower()

    def _match_filter(self, name: str, label: str = "") -> bool:
        """这一行是否匹配搜索框（匹配**干员名**或**房间/说明**；空搜索=全匹配）。"""
        q = self._filter_text()
        if not q:
            return True
        return q in (name or "").lower() or q in (label or "").lower()

    def _row_filter_label(self, row) -> str:
        """过滤时要一并匹配的"行标签"（房间名 / 「不在基建」段名）。"""
        kind, fi, _si, _name, label = row
        if kind == self.KIND_BENCH:
            return f"{DETACHED_ROOM} {DETACHED_TITLE}"
        return f"{label} {self._room_name(fi) if fi >= 0 else ''}"

    def _visible_names(self) -> List[str]:
        """搜索框过滤之后**还看得见**的干员（一键动作只作用于他们。

        ⚠️ 不过滤的话，"搜了某人、点『全部 24』"会把看不见的人一起改掉——违反直觉。
        """
        return [r[3] for r in self._row_plan()
                if r[0] in (self.KIND_SLOT, self.KIND_BENCH) and r[3]]

    def _on_filter_change(self) -> None:
        """搜索框输入 → 只重画表格（**不动心情状态**，所以不需要重算）。

        过滤时**保留滚动位置**（记下再恢复）：正看到一半被拉回顶部很难受。
        """
        self._collect_moods()
        self._keep_scroll_next = True
        if hasattr(self, "canvas"):
            try:
                self._scroll_pos_save = self.canvas.yview()[0]
            except tk.TclError:
                self._scroll_pos_save = 0.0
        self._rebuild_rows()
        self._sync_filter_note()

    def _restore_scroll(self, pos: float) -> None:
        """把滚动位置挪回 `pos`（0.0~1.0；行数变了会自动钳位）。"""
        try:
            self.canvas.yview_moveto(max(0.0, min(1.0, float(pos))))
        except (tk.TclError, ValueError):
            pass

    def _sync_filter_note(self) -> None:
        if not hasattr(self, "filter_note"):
            return
        if not self._filter_text():
            self.filter_note.configure(text="（空＝全部；输名字或房间名即过滤）")
            return
        n = len(self._visible_names())
        self.filter_note.configure(text=f"匹配 {n} 人　（Esc 清空）")

    # ---------------------------------------------------------------- 练度缺省
    def _elite_of(self, name: str) -> int:
        """某干员的练度：面板里改过的 > **干员池里的** > 满练（2）。"""
        return int(self._elite.get(name, self._pool_elite.get(name, 2)))

    def _level_of(self, name: str) -> Optional[int]:
        """池里带的等级（没有就 None → 引擎用缺省等级）。"""
        return self._pool_level.get(name)

    def _op_text(self, name: str) -> str:
        """表格里的干员名（非精英化二时带练度角标，与看板一致）。"""
        if not name:
            return "（空位 · 点这里选人）"
        elite = self._elite_of(name)
        return f"{name} E{elite}" if elite < 2 else name

    def _on_elite_change(self, name: str) -> None:
        """改某个干员的精英化（决定他的心情技能能不能生效）。"""
        var = self._elite_vars.get(name)
        if var is None:
            return
        text = var.get().strip()          # "E0"/"E1"/"E2"
        self._elite[name] = int(text[1:]) if len(text) == 2 and text[1:].isdigit() else 2
        self._mark_dirty()                # 练度也要写回布局（否则这一班不会被提交）
        self._repaint_op_names({name})
        self._notify()

    def _set_all_elite(self, elite: int) -> None:
        """一键把当前班次所有干员设为该精英化（默认口径就是 E2 满练）。"""
        for fac in self._fac_names:
            for n in fac.get("operators", []):
                if n:
                    self._elite[n] = int(elite)
        self._mark_dirty()
        self._refresh_elite_cells()
        self.err.configure(text=f"已把本班次全部干员设为 E{elite}（点「应用」才生效）")
        self._notify()

    def _repaint_op_names(self, names=None) -> None:
        """刷新"干员"那一列的显示名（练度角标变了就要跟着变）。

        `names=None` = 全部；分组卡片行没有 `op` 控件，跳过。
        """
        wanted = None if names is None else set(names)
        for r in self._rows:
            op, who = r.get("op"), r.get("name")
            if op is None or not who:
                continue
            if wanted is not None and who not in wanted:
                continue
            op.configure(text=self._op_text(who))

    def _refresh_elite_cells(self) -> None:
        for name, var in self._elite_vars.items():
            var.set(f"E{self._elite_of(name)}")
        self._repaint_op_names(set(self._elite))

    def _op_spec(self, name: str):
        """写回场景的干员写法：满练（E2 且等级为缺省）就用纯名字，否则写成对象。

        「非满练才写对象」是为了 JSON 简洁；池里带来的 `level` 也要写进去
        （本项目有 4 条技能要求等级 30，等级低了它们不生效）。
        """
        elite = self._elite_of(name)
        level = self._level_of(name)
        if elite == 2 and (level is None or level == DEFAULT_OPERATOR_LEVEL):
            return name
        spec: dict = {"name": name, "elite": elite}
        if level is not None:
            spec["level"] = level
        return spec

    def _rebuild_rows(self) -> None:
        """按计划画表格；**结构没变就只换内容**（切班次/换人是最常见的路径，
        重建 50 行要 ~290ms，复用只要几毫秒——与看板 `_structure_signature` 同一套思路）。

        三种行共用一套列定义（`TABLE_COLUMNS`）：

        | 行 | 长什么样 |
        |---|---|
        | 房间分组卡片 | 通栏底色条：`制1 制造站 · Lv3 · 3/3 人` |
        | 位置的干员 | `制造站#1 | 1 | 泡泡 | E2 | 24` |
        | 「不在基建」的人 | 淡绿底：`不在基建 | 歌蕾蒂娅 | — | 24 | 其他班：2中 ×` |
        """
        plan = self._row_plan()
        sig = [(r[0], r[1], r[2]) for r in plan]
        if sig == [r["key"] for r in self._rows]:
            self._fill_rows(plan)
            self._sync_filter_note()
            return
        # 过滤/重建时**保住滚动位置**（正看到一半被拉回顶部很难受）；
        # 换排班 / 切班次那种"内容真的换了"的调用方会先 `yview_moveto(0)`。
        # ⚠️ 名字别叫 `pos`：循环里有个"位次"单元格也叫 `pos`（Label），会把它覆盖成控件，
        #    于是 `_restore_scroll(Label)` 抛 TypeError、**后面那行 `_sync_filter_note()` 就不执行**
        #    ——表现是"搜索框有过滤、计数提示却还是空搜索的文案"（实测踩过）。
        keep_scroll = self._keep_scroll_next
        scroll_pos = self._scroll_pos_save if (keep_scroll and hasattr(self, "canvas")) else 0.0
        self._keep_scroll_next = False
        for w in self.inner.winfo_children():
            w.destroy()
        self._rows = []
        for row_i, (kind, fi, si, name, label) in enumerate(plan):
            # 「房间分组卡片」＝ 通栏小标题行；「不在基建」的段首行也是通栏的
            card = kind == self.KIND_ROOM
            home = fi == DETACHED_FI                       # 「不在基建」那一段
            bg = (theme.OK_SOFT if home else
                  theme.PANEL_ALT if card else theme.zebra(row_i))
            fg = theme.OK if card else theme.TEXT
            # ⚠️ **所有单元格直接放上 `inner` 的共享 grid**（没有"每行一个 Frame"）。
            #    列宽只有一个来源 ⇒ 严格对齐；行高由 `inner.rowconfigure(row_i, minsize=)`。
            self.inner.rowconfigure(row_i, minsize=(CARD_H if card else ROW_H))
            cells: List[tk.Widget] = []
            r = {"key": (kind, fi, si), "grid_row": row_i, "kind": kind, "bg": bg,
                 "name": name, "op": None,
                 "entry": None, "elite": None, "dash": None, "remove": None,
                 "cells": cells}
            if card:
                # 通栏卡片：`制1 制造站#1 · Lv3 · 3/3 人`（口径说明在表头那一行）
                lbl = self._text(self.inner, f"{'不在基建' if home else self._fac_tag(fi)}"
                                             f"　{label}", bg, fg, 0, bold=True, padx=6,
                                 wrap=CARD_WRAP, span=len(TABLE_COLUMNS), sticky="ew",
                                 grid_row=row_i)
                r["room"] = lbl
                cells.append(lbl)
            else:
                pos = self._text(self.inner, f"{si + 1}" if kind == self.KIND_SLOT else "",
                                 bg, theme.MUTED, 1, anchor=TABLE_COLUMNS[1][3],
                                 wrap=60, grid_row=row_i)
                r["pos"] = pos
                cells.append(pos)
                r["op"] = self._op_label(self.inner, fi, si, name, bg=bg, grid_row=row_i)
                r["op"].grid(row=row_i, column=2, sticky="nsew", padx=2)
                cells.append(r["op"])
                r["elite"] = ttk.Combobox(self.inner, state="readonly", width=1,
                                          values=[f"E{i}" for i in range(3)])
                r["elite"].bind("<<ComboboxSelected>>",
                                lambda _e, n=name: self._on_elite_change(n))
                r["elite"].grid(row=row_i, column=3, sticky="ew", padx=ELITE_PAD)
                cells.append(r["elite"])
                r["entry"] = ttk.Entry(self.inner, width=1)
                r["entry"].grid(row=row_i, column=4, sticky="ew", padx=MOOD_PAD)
                cells.append(r["entry"])
                dash = self._text(self.inner, "—", bg, theme.MUTED, 5, wrap=DASH_WRAP,
                                  padx=2, grid_row=row_i)
                r["dash"] = dash
                cells.append(dash)
                r["remove"] = ttk.Button(self.inner, text="×", width=2,
                                         command=lambda n=name: self._undetach_operator(n))
                cells.append(r["remove"])
            self._bind_hover(r)
            self._rows.append(r)
        if not plan or not any(r["kind"] == self.KIND_SLOT for r in self._rows):
            msg = ("（这一班没有位置）" if not plan
                   else "（没有匹配的行 —— 清空搜索框可看全部）")
            self._text(self.inner, msg, theme.PANEL, theme.MUTED, 0, padx=6,
                       span=len(TABLE_COLUMNS), sticky="w", grid_row=len(plan))
        self._fill_rows(plan)
        if hasattr(self, "vs"):
            self.vs.refresh()          # 行数变了 → 重算滚动区间
        # 回顶部只在"换了一整套内容"时做；**搜索过滤**时要保留位置（正看到一半被拉回顶部很难受）
        if keep_scroll:
            self._restore_scroll(scroll_pos)
        else:
            self.canvas.yview_moveto(0)
        self._sync_filter_note()

    def _fac_tag(self, fac_index: int) -> str:
        """房间分组卡片左侧的短标记（`制1` / `宿3` / `中`…），与看板/全员一览同一套。"""
        fac = self._schedule.shifts[self._shift_index].world.facilities[fac_index]
        counts = sum(1 for f in self._schedule.shifts[self._shift_index].world.facilities
                     if f.ftype == fac.ftype)
        ordinal = 0
        if counts > 1:
            ordinal = 1 + len([f for f in self._schedule.shifts[self._shift_index]
                               .world.facilities[:fac_index] if f.ftype == fac.ftype])
        return facility_tag(fac, ordinal)

    def _bind_hover(self, r: dict) -> None:
        """悬停高亮：指针在哪一行，那一行底色就变浅蓝（"我正指着哪一行"）。

        ⚠️ 要绑**行和它的每个子控件**：指针从行移进子控件时，Tk 会先给行发 `<Leave>`、
        再给子控件发 `<Enter>`；只绑行的话会闪一下。所以 `_unhover` 先问一句
        "指针还在不在这一行的范围里"，在就不撤。
        ⚠️ 程序化重建（销毁子控件）时 `winfo_containing` 会抛 TclError → 延后一帧执行。
        ⚠️ **滚动期间一律不画**（`_scrolling()`）：滚一下会连着触发几十次 Enter/Leave，
        每次都刷一行底色 → 手感"卡壳"。滚完 120ms 由 `VScroll` 通知恢复（见下）。
        ⚠️ 顺手把**这一行的每个单元格**接进滚轮作用域（`vs.join`）：Tk 的 bindtags 链是
        「控件 → 控件类 → toplevel → all」，**不含父控件**，所以光把 tag 挂在 `inner` 上
        挡不住"指针停在行里的 `Label` 上"——实测就是这样滚轮没反应（`bench` 那段曾
        因下面那行 `return` 整段漏挂）。一行 6 个控件 × 78 行，比"每格重画"便宜得多。
        """
        for w in r["cells"]:
            if hasattr(self, "vs"):
                self.vs.join(w)
        if r["kind"] == self.KIND_BENCH:
            return                              # 「不在基建」那段保持淡绿底，不做悬停
        cells = r["cells"]

        def enter(_e=None):
            if self._scrolling():
                return
            self._hover_row = r
            self._paint_row(r, theme.HOVER)

        def leave(_e=None):
            if self._hover_row is r:
                self._hover_row = None
            if self._scrolling():
                return
            # 延后一帧再判断（指针可能只是移到了同一行的另一个单元格上）
            try:
                cells[0].after_idle(lambda: self._unhover(r))
            except (tk.TclError, IndexError):
                pass

        for w in cells:
            w.bind("<Enter>", enter, add="+")
            w.bind("<Leave>", leave, add="+")

    def _scrolling(self) -> bool:
        """当前是不是正在滚动（`VScroll` 在滚动开始/结束时会置位）。"""
        return bool(getattr(self.vs, "scrolling", False)) if hasattr(self, "vs") else False

    def _unhover(self, r: dict) -> None:
        """指针确实不在这一行的任何一个单元格里了，才撤回高亮。"""
        try:
            x, y = self.inner.winfo_pointerxy()
            for w in r["cells"]:
                if not w.winfo_exists():
                    continue
                left, top = w.winfo_rootx(), w.winfo_rooty()
                if (left <= x < left + w.winfo_width()
                        and top <= y < top + w.winfo_height()):
                    return                  # 还在这行里（只是换了单元格）
        except tk.TclError:
            return
        self._paint_row(r, r.get("bg") or theme.PANEL)

    @staticmethod
    def _paint_row(r: dict, bg: str) -> None:
        """把这一行的**所有单元格**刷成某个底色（行不再是控件，只是"同一 grid 行的一组控件"）。"""
        for w in r["cells"]:
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass

    def _fill_rows(self, plan) -> None:
        """把计划写进行控件（心情输入框按干员名重新绑定，空位显示 —）。

        「不在基建」那一段（`kind == KIND_BENCH`）没有练度下拉（练度对他们无意义），
        换成一个「×」按钮：把她移出**显式**名单；最后那列写"她在别的班在哪"。
        """
        self._mood_vars.clear()
        self._elite_vars.clear()
        self._cells = [(fi, si) for kind, fi, si, _n, _l in plan if kind == self.KIND_SLOT]
        view_moods = self._view_moods()
        mismatches: List[str] = []              # 「快照位置 ≠ 引擎位置」那几行
        for r, (kind, fi, si, name, label) in zip(self._rows, plan):
            if kind == self.KIND_ROOM:
                continue                       # 卡片标题在建的时候已经写好了
            bench = kind == self.KIND_BENCH
            elite = self._elite_of(name) if name else 2
            # 位置列的**引擎口径**：这一刻引擎真正把她放在哪（闲置入宿会把人换出宿舍/换进来）
            note = ""
            if name:
                snap = "不在基建" if bench else (self._room_name(fi) if fi >= 0 else "")
                note = self._where_note(name, snap)
                if note:
                    mismatches.append(f"{name}：{note}")
            r["op"].configure(text=(self._op_text(name) + ("　⇄" if note else "")),
                              cursor="hand2",
                              fg=(theme.TEXT if name else theme.MUTED))
            if name:
                shown = view_moods.get(name, self._moods.get(name, MOOD_MAX))
                shown = Decimal(str(shown))
                var = tk.StringVar(value=theme.fmt_mood(shown))
                self._mood_vars[name] = var
                self._shown[name] = parse_mood(theme.fmt_mood(shown))
                # ⚠️ 用 `_on_cell_write` 而不是直接 `_notify_later`：程序化刷新也会触发 write
                var.trace_add("write", lambda *_a, n=name: self._on_cell_write(n))
                r["entry"].configure(textvariable=var)
                if bench:
                    # 练度下拉收掉（对她无意义），换成「×」；末列写"她在别的班在哪"
                    if r["elite"].winfo_manager():
                        r["elite"].grid_remove()
                    if not r["remove"].winfo_manager():
                        # ⚠️ 列内边距必须与练度下拉**完全一致**（`ELITE_PAD`），
                        #    否则同一列的两种行会差几像素（实测 434 vs 437）
                        r["remove"].grid(row=r["grid_row"], column=3, sticky="w", padx=ELITE_PAD)
                    r["dash"].configure(text=(self._detached_elsewhere(name) or "—"))
                    if not r["dash"].winfo_manager():
                        r["dash"].grid(row=r["grid_row"], column=5, sticky="ew")
                else:
                    if r["remove"].winfo_manager():
                        r["remove"].grid_remove()
                    if note:                    # 位置对不上：写在「说明」列（并给个浅黄底提示）
                        r["dash"].configure(text=note, fg=theme.DANGER)
                        if not r["dash"].winfo_manager():
                            r["dash"].grid(row=r["grid_row"], column=5, sticky="ew")
                    elif r["dash"].winfo_manager():
                        r["dash"].grid_remove()
                    ev = tk.StringVar(value=f"E{elite}")        # 练度：决定技能能不能生效
                    self._elite_vars[name] = ev
                    r["elite"].configure(textvariable=ev)
                    if not r["elite"].winfo_manager():
                        r["elite"].grid(row=r["grid_row"], column=3, sticky="ew", padx=ELITE_PAD)
            else:
                for key in ("entry", "elite", "remove", "dash"):
                    if r[key].winfo_manager():
                        r[key].grid_remove()
                if not r["dash"].winfo_manager() and not bench:
                    r["dash"].grid(row=r["grid_row"], column=5, sticky="ew")
        self._where_mismatch = mismatches
        if hasattr(self, "head_note"):
            self._sync_head_note()

    def _sync_head_note(self) -> None:
        """表头第二行的口径说明：有"位置对不上"的行就补一句（`⇄` 是什么意思）。"""
        if self._where_mismatch:
            self.head_note.configure(
                text=DETACHED_HINT + f"　｜　⇄ 位置以引擎为准（{len(self._where_mismatch)} 处，"
                                     f"见「说明」列）", fg=theme.DANGER)
        else:
            self.head_note.configure(text=DETACHED_HINT, fg=theme.MUTED)

    def _op_label(self, parent, fac_index: int, slot_index: int, name: str,
                  bg: str = theme.PANEL, grid_row: int = 0) -> tk.Label:
        """干员单元格：可点的文字（点开搜索窗换人 / 选人 / 清空）。

        `wraplength=OP_WRAP` 是**对齐的关键**：不夹住的话长名字会把"干员"列撑宽
        （实测最长 307px），于是各行的列宽不一致、看起来就是歪的。
        """
        label = tk.Label(parent, text=(name or "（空位 · 点这里选人）"), bg=bg,
                         fg=(theme.TEXT if name else theme.MUTED), anchor="w", cursor="hand2",
                         wraplength=OP_WRAP,
                         font=(theme.FONT_FAMILY, theme.FS_SMALL))
        label.bind("<Button-1>",
                   lambda _e: self._pick_operator(fac_index, slot_index))
        return label

    def _set_cell(self, name: str, text: str) -> None:
        """**程序化**写入某一格：先把 `_shown` 登记好，再 `var.set`。

        ⚠️ 顺序不能反：`var.set()` 会同步触发 `_on_cell_write`，而它是拿"新文本 vs `_shown`"
        判断"是不是用户改的"。先 set 再更新 `_shown`，程序自己的刷新就会被误判成用户改动
        ——非起点视图下每次刷新都会变成一次"改动"（重算）。先登记后写，判据恒等，稳。
        """
        var = self._mood_vars.get(name)
        if var is None:
            return
        self._shown[name] = parse_mood(text)
        if var.get() != text:                  # 值没变就别惊动 Tk（少一次 trace 调用）
            var.set(text)

    def _refresh_mood_cells(self) -> None:
        """把**这一刻的实际心情**刷进表格（起点视图＝周期起点心情）。

        `self._shown` 记下"我们刚写进去的值"，`_collect_moods` / `_on_cell_write`
        靠它区分"用户改过"与"只是刷新"（见 `_on_cell_write` 的说明）。
        """
        moods = self._view_moods()
        for name in list(self._mood_vars):
            v = moods.get(name)
            if v is None:                      # 这一刻她不在基建里（没排班）→ 显示导入/起点值
                v = self._moods.get(name, MOOD_MAX)
            self._set_cell(name, theme.fmt_mood(Decimal(str(v))))

    # ================================================================ 交互
    def _on_shift_change(self) -> None:
        """切班次：把时刻挪到**这一班的起点**（心情列跟着那一刻重算）。

        为什么不是"只换干员、时刻不动"：干员列由时刻驱动（见模块 docstring 第 3 条），
        两处各指一个时间会出现"看的是 13:00 的心情、改的却是第 1 班的干员"这种错位。
        """
        self._collect_moods()
        label = self.shift_var.get()
        if label in self._labels:
            # 时刻挪到这一班的起点 → 干员列自然跟着换（`_fac_index` 会看到差别）
            self._set_view(self._view_cycle, self._schedule.starts[self._labels.index(label)])
            return
        self._sync_facilities()
        self._rebuild_rows()
        self.err.configure(text="")

    def on_view_change(self, t_abs, moods: Optional[Dict[str, Decimal]] = None) -> None:
        """宿主（`ui/app.py`）在"滑块动了 / 重算了"之后调它：刷新这一刻的实际心情。

        没勾「跟随滑块」时**只刷数值、不动视图**（用户自己定的时刻不能被主界面拽走）。
        """
        self._current_t = Decimal(str(t_abs))
        if moods:
            self._now = {k: Decimal(str(v)) for k, v in moods.items()}
        if self.follow.get():
            self._set_view_from_abs(self._current_t)
        else:
            self._refresh_mood_cells()
        self._sync_anchor_note()

    def _pick_operator(self, fac_index: int, slot_index: int) -> None:
        self._collect_moods()
        ops = self._fac_names[fac_index].setdefault("operators", [])
        current = ops[slot_index] if slot_index < len(ops) else ""
        names = [n for n in self._schedule.operator_names()]
        for n in self._pool_names():            # 池里的人也要能选（空布局否则没人可选）
            if n not in names:
                names.append(n)
        others = [n for f in self._fac_names for n in f.get("operators", []) if n != current]
        picked = ask_operator(self, names + [n for n in others if n not in names], current)
        if picked is None:
            return                                  # 取消
        while len(ops) <= slot_index:               # 中间的空位用 "" 占住（不能塌缩）
            ops.append("")
        if picked == "":
            ops[slot_index] = ""
        else:
            # 同一个人不能同时占两个位置：先把他从别的位置摘掉
            for f in self._fac_names:
                f["operators"] = [n for n in f.get("operators", []) if n != picked]
            ops = self._fac_names[fac_index].setdefault("operators", [])
            while len(ops) <= slot_index:
                ops.append("")
            ops[slot_index] = picked
        self._mark_dirty()
        self._rebuild_rows()

    def _clear_shift(self) -> None:
        self._collect_moods()
        for f in self._fac_names:
            f["operators"] = []
        self._mark_dirty()
        self._rebuild_rows()
        self.err.configure(text="已清空本班次（点「应用」才生效）")
        self._notify()

    def _paste_names(self) -> None:
        self._collect_moods()
        dlg = PasteDialog(self, len(self._all_slots()))
        self.wait_window(dlg)
        self.grab_set()                              # 子对话框收走了 grab，这里拿回来
        if dlg.result is None:
            return
        self._apply_names(*dlg.result)

    def _apply_names(self, names: Sequence[str], clear_first: bool = True) -> None:
        """把一份名单按「房间顺序 + 位次」填进当前班次（重复的人只填第一个位置）。

        这是「批量粘贴名单」的真正逻辑，单独抽出来是为了能不开模态框直接测。
        """
        if clear_first:
            for f in self._fac_names:
                f["operators"] = []
        slots = self._all_slots()                   # [(设施下标, 位次)]
        used = {n for f in self._fac_names for n in f.get("operators", []) if n}
        filled = 0
        for name, (fi, si) in zip(names, slots):
            if name in used:                        # 同一个人只留一个位置（先到先得）
                continue
            used.add(name)
            ops = self._fac_names[fi].setdefault("operators", [])
            while len(ops) <= si:                   # 中间的空位用 "" 占住（不能塌缩）
                ops.append("")
            ops[si] = name
            filled += 1
        # ⚠️ 这里**不再"收掉空位"**（旧实现是 `[n for n in ops if n]`）：位次是正式概念
        #    —— "第 3 位"被清空之后后面的人**不左移**；而且工作副本的 `operators` 是
        #    **按位次对齐的名字列表**（空串＝空槽），出口 `value()` 用 `write_seats` 收敛。
        #    塌缩会让位次整体前移，与"位次不左移"直接冲突（也是 `_apply_names` 的老 bug）。
        self._mark_dirty()
        self._rebuild_rows()
        left = max(0, len(names) - len(slots))                 # 位置不够、没排上的
        dup = min(len(names), len(slots)) - filled             # 同名跳过、没填上的
        msg = f"已填入 {filled} 人"
        if dup:
            msg += f"；{dup} 条重名（同名只填一次）"
        if left:
            msg += f"；{left} 人没有位置"
        self.err.configure(text=msg + "（点「应用」才生效）")
        self._notify()

    def _all_slots(self) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for fi, fac in enumerate(self._fac_names):
            for si in range(self._slot_count(fac, fi)):
                out.append((fi, si))
        return out

    # ================================================================ 收结果
    def _collect_moods(self) -> None:
        """把**用户改过**的心情格子收进结果（写锚点 / 起点心情）。

        ⚠️ 只收"和我们刚写进去的不一样"的格子：表格里的值本来就是从轨迹读出来的，
        无差别回收会把"进驻事件在那一刻改过的心情"当成用户的手动设定重复落一遍
        （改个房间等级都会顺手改掉周期起点心情——那是个很隐蔽的 bug）。
        解析不了的值（用户正在输入）跳过，交给 `value()` / `err` 报错。
        """
        for name, var in self._mood_vars.items():
            v = parse_mood(var.get())
            if v is None:
                continue
            if v == self._shown.get(name):
                continue                       # 没动过 → 别当成手动设定
            self._write_view_mood(name, v)
        self._sync_anchor_note()

    def _error(self, text: str) -> None:
        self.err.configure(text=text)
        self.bell()

    def value(self):
        """收成 `(changes, moods, mood_events, detached)`；非法输入时返回 `None`（并写 `err`）。

        - `changes`：改过干员的班次 → `{班次下标: 布局}`
        - `moods`：周期起点心情（只含与导入值不同的项）
        - `mood_events`：心情指定事件（锚点）整份
        - `detached`：**「不在基建」名单**整份（`[]` = 空；只有用户**显式加过/删过**才非 `None`）
        """
        bad = [n for n, var in self._mood_vars.items() if parse_mood(var.get()) is None]
        if bad:
            self._error("心情要在 0 ~ 24 之间（检查：" + "、".join(bad[:4]) +
                        ("…" if len(bad) > 4 else "") + "）")
            return None
        self._collect_moods()
        # 干员改动：所有被改过的班次（下标 → 布局），调用方逐班 `replaced_shift`
        # ⚠️ **出口必须用 `write_seats` 收敛写法**（旧实现有两个真 bug）：
        #    ① `dict(f, operators=[...])` 会把**旧的 `slots` 一起留下**，而
        #       `models.facility_occupancy` 里 `slots` 优先 ⇒ 面板上的改动**静默失效**；
        #    ② `if n` 会把**空洞丢掉**（后面的人往前挪），与"位次不左移"的口径冲突。
        #    工作副本里的 `f["operators"]` 是"按位次对齐的名字列表"（见 `_sync_facilities`）。
        changes = {}
        for i, facs in self._draft.items():
            out = []
            for f in facs:
                fac = dict(f)
                write_seats(fac, [self._op_spec(n) if n else ""
                                  for n in f.get("operators", [])])
                out.append(fac)
            changes[i] = out
        moods = {n: v for n, v in self._moods.items()
                 if Decimal(str(self._imported.get(n, MOOD_MAX))) != v}
        return changes, moods, list(self._events), self._detached_out()

    # ================================================================ 窗口杂务
    def _center(self, parent) -> None:
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), 780)
        h = min(max(self.winfo_reqheight(), 560), 820)
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        self.geometry(f"+{px + max((pw - w) // 2, 0)}+{py + max((ph - h) // 4, 0)}")

    def _modal(self, parent) -> None:
        self.transient(parent)
        self.grab_set()
        self.focus_set()


class BatchPanel(tk.Frame, BatchMixin):
    """「干员与心情」内容本体（设置中心的「干员与心情」分区；`value()` 收结果、`on_change` 即时生效）。"""

    def __init__(self, master, schedule, shift_index: int = 0,
                 initial_moods: Optional[Dict[str, Decimal]] = None,
                 imported_moods: Optional[Dict[str, Decimal]] = None,
                 moods_now: Optional[Dict[str, Decimal]] = None,
                 current_t=Decimal("0"), on_change=None, pool=None,
                 table_height=TABLE_H, page_height=0,
                 cycles: int = 1, mood_events=None, moods_at=None, follow_var=None,
                 detached=None, world_at=None):
        super().__init__(master, bg=theme.BG)
        self._init_batch_body(master, schedule, shift_index=shift_index,
                              initial_moods=initial_moods, imported_moods=imported_moods,
                              moods_now=moods_now, current_t=current_t, on_change=on_change,
                              pool=pool, table_height=table_height, page_height=page_height,
                              cycles=cycles, mood_events=mood_events, moods_at=moods_at,
                              follow_var=follow_var, detached=detached, world_at=world_at)

    def destroy(self) -> None:
        """销毁时取消还没落地的防抖任务（否则会对着已销毁的控件报 invalid command name）。"""
        self._cancel_notify()
        tk.Frame.destroy(self)


class PasteDialog(tk.Toplevel):
    """「批量粘贴名单」：一个多行文本框 + 是否先清空。返回 `(names, clear_first)`。"""

    def __init__(self, parent, slots: int):
        super().__init__(parent, bg=theme.BG)
        self.title("批量粘贴名单")
        self.resizable(False, False)
        self.result = None
        self._slots = slots
        tk.Label(self, text=f"一行一个干员名（逗号 / 空格 / 顿号也行）；本班次共 {slots} 个位置。",
                 bg=theme.BG, fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_BODY)
                 ).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, 2))
        tk.Label(self, text="按房间顺序从上到下填入（控制中枢 → 制造站 → … → 宿舍）；"
                           "多出来的人会被忽略。",
                 bg=theme.BG, fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                 ).pack(anchor="w", padx=theme.PAD)
        self.text = tk.Text(self, width=46, height=12, font=(theme.FONT_FAMILY, theme.FS_SMALL),
                            highlightthickness=1, highlightbackground=theme.BORDER)
        self.text.pack(padx=theme.PAD, pady=theme.GAP)
        self.clear_first = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="先清空本班次再填（不勾＝只覆盖前面的位置）",
                       variable=self.clear_first, bg=theme.BG, activebackground=theme.BG,
                       highlightthickness=0).pack(anchor="w", padx=theme.PAD)
        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, theme.PAD))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="填入", style="Accent.TButton", command=self._ok).pack(
            side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.text.focus_set()
        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        self.geometry(f"+{px + max((parent.winfo_width() - self.winfo_reqwidth()) // 2, 0)}"
                      f"+{py + max((parent.winfo_height() - self.winfo_reqheight()) // 4, 0)}")

    def _ok(self) -> None:
        names = split_names(self.text.get("1.0", "end"))
        if not names:
            return
        self.result = (names, bool(self.clear_first.get()))
        self.destroy()


__all__ = ["BatchPanel", "BatchMixin", "PasteDialog", "split_names"]
