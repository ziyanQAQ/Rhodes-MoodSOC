"""ui/app.py —— 主窗口：工具栏 + 基建看板 + 心情曲线 + 时间滑块 + 状态栏。

五个功能的落点：

| 需求 | 在哪 |
|---|---|
| ① 导入多班（12h/6h/6h 三个文件或一个含多 plan 的文件）+ 自设周期/班数/每班时长 | 「导入排班…」「班次设置…」 |
| ② 逐个位置手动设干员与心情 | 看板：**左键**位置选人/更换/清空、**右键**设该位置干员的心情 |
| ③ 时间滑动 → 各位置心情实时变化 | 底部滑块（只做插值，不重算，跟手） |
| ④ 对点：输入干员名 → 整周期心情曲线 | 右侧曲线面板（下拉/搜索选人 + 精确读数与关键数值） |
| ⑤ 简洁明了 | `ui/theme.py` 一套扁平令牌；界面只有"看板 / 曲线 / 滑块"三块 |

引擎（`ui/schedule.py`）与界面严格分离：**只有"结构变了"才重算**（改布局/改时长/改周期数），
拖动滑块只做 O(位置数) 的插值刷新。
"""
from __future__ import annotations

import sys
import time
import tkinter as tk
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

# 允许**直接运行本文件**（`python ui/app.py` / IDE 的 Run）：直接跑时 `ui` 不是包，
# 相对导入会失败；把仓库根目录放进 sys.path 后用绝对导入，与 `python -m ui` 等价。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import theme  # noqa: E402
from ui.board import BaseBoard, elite_badge, facility_tag  # noqa: E402
from ui.chart import MoodChart  # noqa: E402
from ui.dialogs import ask_operator, ask_level, ask_mood  # noqa: E402
from ui.roster import RosterStrip  # noqa: E402
from ui.schedule import (Schedule, Trajectory, all_operator_names,  # noqa: E402
                         default_initial_moods, load_schedule, simulate_schedule)
from mood_soc import entry_event_holders, entry_target_kind  # noqa: E402
from mood_soc import mood_skill_summary  # noqa: E402
from mood_soc.config import (MOOD_MAX, FacilityType, facility_max_level,  # noqa: E402
                             facility_slots)
from mood_soc.models import IdleToDormEntry, normalize_entry_when  # noqa: E402

SAMPLE = ROOT / "resources" / "arknights-infra-schedule-maa.json"
STEP_FINE = Decimal("0.25")      # 方向键/微调步长（15 分钟）


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

# 播放速度：单位是 **模拟秒 / 真实秒（s/s）** —— `1x` 就是实时（1 秒推进 1 模拟秒）。
# 24h 周期在 1x 下要放 24 小时，所以档位往上给到"4 小时/秒"（＝14400x）。
PLAY_SPEEDS = ("1x", "60x", "600x", "3600x", "14400x")
PLAY_BASE_SECONDS_PER_SEC = Decimal("1")
PLAY_SPEED_HINTS = {
    "1x": "实时",
    "60x": "1 分/秒",
    "600x": "10 分/秒",
    "3600x": "1 小时/秒",
    "14400x": "4 小时/秒",
}
PLAY_TICK_MS = 60


class MoodSocApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Rhodes-MoodSOC · 基建心情排班")
        self.geometry("1560x950")
        # 最小宽度按"工具栏放得下"来定（实测工具栏需要 ~1370px），否则最右侧按钮会被裁掉
        self.minsize(1400, 780)
        self.configure(bg=theme.BG)

        self.schedule: Schedule | None = None
        self.traj: Trajectory | None = None
        self.initial_moods: dict = {}          # 手动设过的心情（覆盖布局里的值）
        self.cycles = 1
        # 周期数是**设置中心**里的一个控件；变量挂在 app 上（唯一真源），
        # 设置窗口只是把它接到下拉框上——工具栏时代它挂在工具栏里。
        self.cycles_var = tk.StringVar(value="1")
        self.settings_dlg = None               # 「设置」中心的窗口（唯一设置入口）
        self.entry_events = tk.BooleanVar(value=False)
        self.entry_swap_with: Optional[str] = None     # None = 默认「前一位进驻」；"any" = 自动挑最累的
        self.entry_scope = "dorm"                      # "dorm" 仅同宿舍 / "anywhere" 基建任意位置
        self.entry_restore_back = True                 # 界面固定：只换心情、两人留原位
        self.entry_when = "full"                       # wait（勾了强制切换：等她满）/ full（没满就不换）
        self.entry_per_shift: list = []                # 按班次覆盖（EntryShiftOverride 列表）
        # 闲置入宿（未满的闲置干员进宿舍）：总开关 + 逐人设置 {名字: (参与, 换谁 或 None)}
        self.idle_to_dorm = tk.BooleanVar(value=False)
        self.idle_entries: dict = {}      # {(周期, 班次, 干员): (参与, 目标标签)}
        self.idle_globals: dict = {}      # {干员: (参与, 目标标签)} 不限班次/周期（来自 JSON）
        self.play_speed = Decimal("1")
        self.current_t = Decimal("0")
        self.curve_operator = ""
        self._playing = False
        self._play_job = None
        self._layout_sig = None
        self._setting_scale = False
        self._pending_t = None
        self._refresh_job = None
        self._settle_job = None
        self._play_last = 0.0
        self._last_refresh = 0.0
        self._roster_dirty = False

        self._init_style()
        self._build_toolbar()
        self._build_body()
        self._build_bottom()
        self._bind_keys()
        self._space_only_plays(self)          # 空格永远＝播放/暂停（别去"按下"聚焦的按钮）

        self._autoload_job = self.after(60, self._autoload_sample)
    # ================================================================== 样式
    def _init_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=(theme.FONT_FAMILY, theme.FS_BODY),
                     background=theme.BG, foreground=theme.TEXT)
        st.configure("TButton", padding=(10, 5), relief="flat",
                     background=theme.PANEL, bordercolor=theme.BORDER, focuscolor=theme.PANEL)
        st.map("TButton", background=[("active", theme.PANEL_ALT), ("disabled", theme.BG)],
               foreground=[("disabled", theme.MUTED)])
        st.configure("Accent.TButton", background=theme.ACCENT, foreground="#ffffff")
        st.map("Accent.TButton", background=[("active", "#245ccb"), ("disabled", "#9db8f5")],
               foreground=[("disabled", "#f0f4ff")])
        st.configure("TCheckbutton", background=theme.BG, focuscolor=theme.BG)
        st.map("TCheckbutton", background=[("active", theme.BG)])
        st.configure("TCombobox", padding=3)
        st.configure("TScale", background=theme.BG)
        st.configure("Vertical.TScrollbar", background=theme.PANEL, troughcolor=theme.BG,
                     bordercolor=theme.BORDER, arrowcolor=theme.MUTED)

    # ================================================================== 工具栏
    def _build_toolbar(self):
        """工具栏＝**4 组**：导入 / 设置（按钮 + 一行状态）/ 播放 / 回到起点。

        设置入口只有一颗「设置…」（`ui/settings.py`）：班次与周期数、房间等级与干员、
        换心情、闲置入宿全在里面，**改动立即生效**。这里不再并列那 5 个入口——
        重复的（重置心情＝批量表里的"恢复导入值"）与只增加步骤的一律去掉。
        """
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, theme.GAP))
        self.toolbar = bar

        ttk.Button(bar, text="导入排班…", style="Accent.TButton",
                   command=self.import_files).pack(side="left")

        ttk.Button(bar, text="设置…", command=self.open_settings).pack(side="left",
                                                                       padx=(6, 6))
        # 状态摘要＝原来的两行状态合成一句（点它也能开设置）。
        # ⚠️ 两个 `_detail` 标签的名字与文案保持原样：`_sync_entry_label` / `_sync_idle_label`
        #    是它们的唯一写入点，测试也按这两句话断言。
        self.entry_detail = tk.Label(bar, text="", bg=theme.BG, fg=theme.MUTED,
                                     cursor="hand2",
                                     font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.entry_detail.pack(side="left")
        tk.Label(bar, text=" · ", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.idle_detail = tk.Label(bar, text="", bg=theme.BG, fg=theme.MUTED,
                                    cursor="hand2",
                                    font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.idle_detail.pack(side="left")
        for w in (self.entry_detail, self.idle_detail):
            w.bind("<Button-1>", lambda _e: self.open_settings())

        play = tk.Frame(bar, bg=theme.BG)
        play.pack(side="left", padx=(16, 0))
        self.play_btn = ttk.Button(play, text="▶ 播放", command=self.toggle_play)
        self.play_btn.pack(side="left")
        tk.Label(play, text="速度", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(8, 3))
        self.speed_var = tk.StringVar(value=PLAY_SPEEDS[0])
        speed_box = ttk.Combobox(play, textvariable=self.speed_var, width=7, state="readonly",
                                values=PLAY_SPEEDS)
        speed_box.pack(side="left")
        speed_box.bind("<<ComboboxSelected>>", lambda _e: self._on_speed())
        self.speed_hint = tk.Label(play, text="", bg=theme.BG, fg=theme.MUTED,
                                   font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.speed_hint.pack(side="left", padx=(3, 0))
        ttk.Button(play, text="回到起点", command=lambda: self.set_time(Decimal("0"))
                   ).pack(side="left", padx=(8, 0))
        # 工具栏按钮**不参与 Tab 焦点**（takefocus=False）：免得 Tab 停到某颗按钮上之后，
        # 空格/回车把它"按下"却又开一次设置框 —— 空格在本窗口里只该管播放/暂停。
        for w in (bar, play):
            for child in w.winfo_children():
                if isinstance(child, ttk.Button):
                    child.configure(takefocus=False)
        self._on_speed()
        self._sync_entry_label()
        self._sync_idle_label()

    # ================================================================== 设置中心
    def open_settings(self, page: Optional[str] = None):
        """打开「设置」中心（唯一设置入口；已开着就切到指定分区并提到前面）。"""
        if self.schedule is None:
            self.status.configure(text="先导入一份排班，再来改设置")
            return None
        from .settings import SettingsDialog
        if self.settings_dlg is not None and self.settings_dlg.winfo_exists():
            if page:
                self.settings_dlg.open_page(page)
            self.settings_dlg.deiconify()
            self.settings_dlg.lift()
            return self.settings_dlg
        self.settings_dlg = SettingsDialog(self, self, page=page)
        return self.settings_dlg

    # —— 设置中心用的只读口子（窗口里不出现引擎细节，一律经这里取值）——
    def editing_shift_index(self) -> int:
        return self._editing_shift_index()

    def imported_moods(self) -> dict:
        return default_initial_moods(self.schedule) if self.schedule else {}

    def moods_now(self) -> dict:
        return self.moods_at_now()

    def entry_candidates(self):
        return self._entry_candidates()

    def idle_groups(self, cycles: Optional[int] = None):
        return self._idle_groups(cycles=cycles)

    def on_cycles_changed(self) -> None:
        """设置中心里改「周期数」→ 与工具栏时代同一个函数。"""
        self._on_cycles()
    # ================================================================== 主体
    def _build_body(self):
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD)

        self.board = BaseBoard(body, on_slot_click=self.on_slot_left,
                               on_slot_right=self.on_slot_right,
                               on_room_click=self.on_room_left)
        self.board.pack(side="left", fill="both", expand=True)

        right = tk.Frame(body, bg=theme.PANEL, highlightbackground=theme.BORDER,
                         highlightthickness=1, width=600)
        right.pack(side="left", fill="both", padx=(theme.GAP, 0))
        right.pack_propagate(False)

        head = tk.Frame(right, bg=theme.PANEL)
        head.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 4))
        tk.Label(head, text="对点查询 · 整周期心情曲线", bg=theme.PANEL, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_TITLE)).pack(side="left")
        ttk.Button(head, text="设置心情…", command=self.set_curve_mood).pack(side="right")

        sel = tk.Frame(right, bg=theme.PANEL)
        sel.pack(fill="x", padx=theme.PAD)
        tk.Label(sel, text="干员", bg=theme.PANEL, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.op_var = tk.StringVar()
        self.op_box = ttk.Combobox(sel, textvariable=self.op_var, state="readonly")
        self.op_box.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self.op_box.bind("<<ComboboxSelected>>", lambda _e: self._on_operator_pick())
        ttk.Button(sel, text="◀", width=3, command=lambda: self._step_operator(-1)).pack(side="left")
        ttk.Button(sel, text="▶", width=3, command=lambda: self._step_operator(1)).pack(side="left",
                                                                                         padx=(4, 0))

        self.chart = MoodChart(right, height=300)
        self.chart.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.GAP)
        self.chart.info_provider = self._facility_at

        self.stats = tk.Label(right, text="", bg=theme.PANEL, fg=theme.TEXT, justify="left",
                              anchor="w", font=(theme.FONT_MONO, theme.FS_SMALL))
        self.stats.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))

        # 全员一览（横条，铺在下方）：把整个周期出现过的干员一次全部摆出来
        self.roster = RosterStrip(self, on_pick=self.on_roster_pick,
                                 on_set_mood=self.on_roster_set_mood)
        self.roster.pack(fill="x", pady=(theme.GAP, 0))

    # ================================================================== 底部
    def _build_bottom(self):
        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, 4))

        self.shift_bar = tk.Frame(bottom, bg=theme.BG)
        self.shift_bar.pack(fill="x")
        self.shift_buttons: list = []

        slide = tk.Frame(bottom, bg=theme.BG)
        slide.pack(fill="x", pady=(4, 0))
        # 左右按钮只画箭头（原来写 "◀ 15min" 容易被当成"15 分钟前/时长"，看不懂）；
        # 步长与快捷键写在右边的一句提示里。
        self.back_btn = ttk.Button(slide, text="◀", width=3,
                                   command=lambda: self.nudge(-STEP_FINE))
        self.back_btn.pack(side="left")
        self.scale = ttk.Scale(slide, from_=0.0, to=24.0, orient="horizontal",
                               command=self._on_scale)
        self.scale.pack(side="left", fill="x", expand=True, padx=theme.GAP)
        self.fwd_btn = ttk.Button(slide, text="▶", width=3,
                                  command=lambda: self.nudge(STEP_FINE))
        self.fwd_btn.pack(side="left")
        self.time_label = tk.Label(slide, text="00:00", bg=theme.BG, fg=theme.TEXT, width=16,
                                   font=(theme.FONT_MONO, theme.FS_BIG, "bold"))
        self.time_label.pack(side="left", padx=(theme.GAP, 0))
        self.slider_hint = tk.Label(
            slide,
            text=f"拖动＝查看该时刻心情　◀▶/←→＝{theme.fmt_mins(STEP_FINE)}一档　空格＝播放/暂停",
            bg=theme.BG, fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.slider_hint.pack(side="left", padx=(theme.GAP, 0))

        self.status = tk.Label(self, text="就绪", bg=theme.BG, fg=theme.MUTED, anchor="w",
                               font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.status.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))

    def _bind_keys(self):
        self.bind("<Left>", lambda _e: self.nudge(-STEP_FINE))
        self.bind("<Right>", lambda _e: self.nudge(STEP_FINE))
        self.bind("<Home>", lambda _e: self.set_time(Decimal("0")))
        self.bind("<End>", lambda _e: self.set_time(self._total_hours()))
        self.bind("<space>", lambda _e: self.toggle_play())

    # ------------------------------------------------------------ 空格只管播放
    def _space_only_plays(self, root) -> None:
        """让**空格只控制播放/暂停**：给主窗口里的控件挂 widget 级 `<space>`（先于类绑定执行、并
        `break`），把 `ttk.Button` 自带的 `<Key-space>`（＝"按下"当前聚焦的按钮）等类绑定挡掉。

        为什么需要：`ttk::button` 的类绑定把空格绑成"按下按钮"，只要焦点落在工具栏按钮上
        （点过它、或 Tab 停上去），按空格就会又打开一次那个对话框（比如「换心情设置」），
        而窗口级的"空格＝播放/暂停"也照旧触发 —— 一次按键两件事。

        ⚠️ 只挂主窗口里的控件：对话框是独立 toplevel，里面"空格＝按下当前按钮"是正常行为，不动它。
        输入框（Entry/Text）**不挂**，那里空格得当字符用。
        """
        def handler(_e):
            self.toggle_play()
            return "break"

        stack = [root]
        while stack:
            w = stack.pop()
            if w.winfo_class() in ("TButton", "Button", "TCombobox", "TScale", "Canvas"):
                w.bind("<space>", handler)
            stack.extend(w.winfo_children())

    # ================================================================== 数据流
    def _autoload_sample(self):
        if not self.winfo_exists():
            return
        if SAMPLE.exists():
            try:
                self.load_paths([SAMPLE])
                self.status.configure(
                    text=f"已载入示例排班：{SAMPLE.name}（可用「导入排班…」换成你的）"
                         f"　｜　{self._entry_status()}　｜　{self._idle_status()}")
            except Exception as exc:                       # noqa: BLE001 —— 启动兜底
                self.status.configure(text=f"示例排班载入失败：{exc}")

    def load_paths(self, paths):
        """按文件集合装配排班（可能抛 ValueError，调用方展示原因）。"""
        sch = load_schedule(paths)
        self.schedule = sch
        self.initial_moods.clear()
        self.current_t = Decimal("0")
        # 场景 JSON 顶层可以带进驻事件配置（换不换 / 换谁）→ 同步到界面的开关与下拉
        cfg = sch.entry_config()
        self.entry_events.set(bool(cfg.enabled))
        self.entry_swap_with = cfg.swap_with
        self.entry_scope = getattr(cfg, "scope", "dorm")
        # 「位置也一起互换」按用户要求从界面收掉：界面固定"只换心情、两人留原位"。
        # 场景 JSON 里写 restore_back: false 会被这条界面口径覆盖（CLI / API 不受影响）。
        self.entry_restore_back = True
        self.entry_when = normalize_entry_when(getattr(cfg, "when", None)) or "full"
        self.entry_per_shift = list(getattr(cfg, "per_shift", []) or [])
        self._sync_entry_label()
        # 场景 JSON 顶层的 idle_to_dorm 也同步过来
        idle_cfg = getattr(sch.shifts[0].world, "idle_to_dorm", None) if sch.shifts else None
        self.idle_to_dorm.set(bool(getattr(idle_cfg, "enabled", False)))
        self.idle_entries = {}
        self.idle_globals = {}
        for e in (getattr(idle_cfg, "per_operator", None) or []):
            label = _idle_label_of(e)
            if e.cycle is None and e.shift is None:
                self.idle_globals[e.name] = (bool(e.enabled), label)   # 不限班次/周期
            else:
                self.idle_entries[(e.cycle or 1, e.shift or 1, e.name)] = (bool(e.enabled), label)
        self._sync_idle_label()
        self._build_shift_buttons()
        self._sync_operator_box()
        self.recompute(fit_slider=True)

    def import_files(self):
        paths = filedialog.askopenfilenames(
            title="选择排班文件（可多选：12h / 6h / 6h 三个文件，或一个含多班的文件）",
            initialdir=str(ROOT / "resources"),
            filetypes=[("排班 / 场景 JSON", "*.json"), ("全部文件", "*.*")])
        if not paths:
            return
        try:
            self.load_paths(list(paths))
        except ValueError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)

    def recompute(self, fit_slider: bool = False):
        """结构变化后重算轨迹（改布局 / 改时长 / 改周期数 / 改换心情设置）。"""
        if self.schedule is None:
            return
        t0 = time.perf_counter()
        self.status.configure(text="计算中…")
        self.update_idletasks()
        self.traj = simulate_schedule(self.schedule, cycles=self.cycles,
                                      initial_moods=self.initial_moods,
                                      entry_events=self.entry_events.get(),
                                      entry_swap_with=self.entry_swap_with,
                                      entry_scope=self.entry_scope,
                                      entry_restore_back=self.entry_restore_back,
                                      entry_when=self.entry_when,
                                      entry_per_shift=self.entry_per_shift or None,
                                      idle_to_dorm=self.idle_to_dorm.get(),
                                      idle_entries=self._idle_entry_list())
        total = self._total_hours()
        if fit_slider or self.current_t > total:
            self.current_t = Decimal("0")
        self.scale.configure(to=float(total))
        self.roster.set_operators(self.traj.names)
        self.roster.set_badges(self._elite_badges())
        self._refresh_layout()
        self._sync_operator_box()
        self._sync_entry_label()
        self._sync_idle_label()
        self.refresh_view()
        ms = (time.perf_counter() - t0) * 1000
        self.status.configure(
            text=f"{len(self.schedule.shifts)} 班 / 周期 {theme.fmt_hours(self.schedule.cycle_hours)}"
                 f"　干员 {len(self.traj.names)} 名　轨迹节点 {len(self.traj.times)}"
                 f"　重算耗时 {ms:.0f} ms　｜　{self._entry_status()}　｜　{self._idle_status()}")

    def _refresh_layout(self, quick: bool = False):
        """看板只在"当前时刻所在班次的布局"变化时刷新（房间结构没变则只换内容）。

        `quick=True`（拖动中）时**先只换看板**，把「全员一览」的位置标记推迟到停手后补——
        否则拖过班次边界时要额外重画 57 个芯片，会噎一下。
        """
        idx = self.schedule.index_at(self.current_t)
        shift = self.schedule.shifts[idx]
        # 签名里带上练度：精英化一变，芯片上的 `E0/E1` 角标要跟着变
        # （否则结构"看起来没变"，看板就不会刷新内容）
        sig = (idx, tuple((f.display_name, tuple((o.name, o.elite) for o in f.operators))
                          for f in shift.world.facilities))
        if sig != self._layout_sig:
            self.board.set_layout(shift, sub_title=self._shift_span_text(idx))
            self._layout_sig = sig
        if quick:
            self._roster_dirty = True
        else:
            self.roster.set_context(self._room_tags(shift))
            self._roster_dirty = False

    def _flush_roster_context(self) -> None:
        """补上拖动期间推迟的「全员一览」位置标记。"""
        if self._roster_dirty and self.schedule is not None:
            self.roster.set_context(self._room_tags(self.schedule.shift_at(self.current_t)))
            self._roster_dirty = False

    def _room_tags(self, shift) -> dict:
        """干员 → 当前班次所在房间的标记（`制1`/`宿3`/`中`…）；不在本班次的不在表里。"""
        tags = {}
        counts = {}
        for f in shift.world.facilities:
            counts[f.ftype] = counts.get(f.ftype, 0) + 1
        seen = {}
        for f in shift.world.facilities:
            seen[f.ftype] = seen.get(f.ftype, 0) + 1
            tag = facility_tag(f, seen[f.ftype] if counts[f.ftype] > 1 else 0)
            for op in f.operators:
                tags[op.name] = tag
        return tags

    def refresh_view(self, quick: bool = False):
        """只更新随时间变化的部分（滑块拖动走这里，O(位置数)）。

        `quick=True`：拖动中的快路径（数值 + 色条跟手，底色等停手后再补）。
        """
        if self.traj is None:
            return
        moods = self.traj.moods_at(self.current_t)
        self.board.update_moods(moods, quick=quick)
        self.roster.update_moods(moods, quick=quick)
        self.chart.set_cursor(self.current_t)
        self.time_label.configure(text=theme.fmt_clock(self.current_t, self.schedule.cycle_hours))
        self._highlight_shift_button()
        # 「此刻速率 / 本班平均」也随时间走（实测 `_stats_text` 只要 0.04ms，相对滑块那次
        # 21ms 的刷新可以忽略）——否则拖滑块时图下那两行一直是旧值（曾经就是这个问题）。
        if self.curve_operator:
            self.stats.configure(text=self._stats_text(self.curve_operator))

    def _on_scale(self, value):
        """滑块回调：只记录目标时刻，刷新做"前沿 + 尾部"节流（拖动时约 30fps）。

        - 距上次刷新 ≥30ms → 立刻刷新（跟手，不延迟）；
        - 否则排队一次 30ms 后的刷新 —— **末位一定落地**，不会丢最后一帧。
        """
        if self._setting_scale:          # 程序设置滑块位置时不要再触发一轮 set_time
            return
        try:
            self._pending_t = Decimal(str(round(float(value), 4)))
        except (InvalidOperation, ValueError):
            return
        if time.perf_counter() - self._last_refresh >= 0.03:
            self._flush_pending()
        elif self._refresh_job is None:
            self._refresh_job = self.after(30, self._flush_pending)

    def _flush_pending(self):
        self._refresh_job = None
        self._last_refresh = time.perf_counter()
        t, self._pending_t = self._pending_t, None
        if t is None:
            return
        # 拖动中走"跟手"快路径（只改数值与色条），停手 200ms 后补一次完整上色
        self.set_time(t, quick=True)
        if self._settle_job is not None:
            self.after_cancel(self._settle_job)
        self._settle_job = self.after(200, self._settle_refresh)

    def _settle_refresh(self):
        """拖动结束后补一次完整刷新（底色/红脸描边/全员一览标记/最危险提示都到位）。"""
        self._settle_job = None
        if self.winfo_exists():
            self._flush_roster_context()
            self.refresh_view(quick=False)

    def set_time(self, t: Decimal, quick: bool = False):
        if self.traj is None:
            return
        total = self._total_hours()
        t = max(Decimal("0"), min(Decimal(str(t)), total))
        changed_shift = self.schedule.index_at(t) != self.schedule.index_at(self.current_t)
        self.current_t = t
        self._setting_scale = True
        self.scale.set(float(t))
        self._setting_scale = False
        if changed_shift:
            self._refresh_layout(quick=quick)
        self.refresh_view(quick=quick)

    def nudge(self, delta: Decimal):
        self.set_time(self.current_t + delta)

    def _total_hours(self) -> Decimal:
        return self.schedule.cycle_hours * self.cycles if self.schedule else Decimal("0")

    def _shift_span_text(self, idx: int) -> str:
        s = self.schedule.shifts[idx]
        start = self.schedule.starts[idx]
        return f"{theme.fmt_clock(start, self.schedule.cycle_hours)} – " \
               f"{theme.fmt_clock(start + s.hours, self.schedule.cycle_hours)}"

    # ================================================================== 编辑
    def _editing_shift_index(self) -> int:
        return self.schedule.index_at(self.current_t)

    def on_slot_left(self, fac_index: int, slot_index: int):
        """左键：选人 / 更换 / 清空该位置。"""
        if self.schedule is None:
            return
        idx = self._editing_shift_index()
        shift = self.schedule.shifts[idx]
        facility = shift.world.facilities[fac_index]
        current = facility.operators[slot_index].name if slot_index < len(facility.operators) else ""
        names = all_operator_names(self.schedule.operator_names())
        picked = ask_operator(self, names, current,
                              title=f"{facility.display_name} · 第 {slot_index + 1} 位")
        if picked is None:
            return
        facs = self._facilities_of(idx)
        ops = [o.name for o in facility.operators]
        while len(ops) <= slot_index:
            ops.append("")
        ops[slot_index] = picked                      # "" = 清空
        facs[fac_index]["operators"] = [o for o in ops if o]
        self._apply_facilities(idx, facs)

    def on_slot_right(self, fac_index: int, slot_index: int):
        """右键：设置该位置干员的心情（周期起点）。"""
        if self.schedule is None:
            return
        idx = self._editing_shift_index()
        facility = self.schedule.shifts[idx].world.facilities[fac_index]
        if slot_index >= len(facility.operators):
            return
        who = facility.operators[slot_index].name
        self._ask_and_set_mood(who)

    def on_room_left(self, fac_index: int):
        """点房间卡头：改这间房的**等级**（容量随之变化）。

        上游依据：`rooms[].phases[lv].maxStationedNum` —— 制造站/贸易站 1/2/3 人、发电站 1/1/1、
        宿舍 5、控制中枢 1~5、会客室/训练室 2、加工站/办公室 1。宿舍等级还决定基础回复；
        中枢等级决定中枢能站几个人（→ 全基建减免）。
        """
        if self.schedule is None:
            return
        idx = self._editing_shift_index()
        facility = self.schedule.shifts[idx].world.facilities[fac_index]
        ftype = facility.ftype
        new_lv = ask_level(self, facility.display_name, facility.level,
                           facility_max_level(ftype),
                           lambda lv: facility_slots(ftype, lv))
        if new_lv is None or new_lv == facility.level:
            return
        facs = self._facilities_of(idx)
        facs[fac_index]["level"] = int(new_lv)
        self._apply_facilities(idx, facs)
        self.status.configure(
            text=f"{facility.display_name} 已设为 Lv{new_lv}"
                 f"（可放 {facility_slots(ftype, new_lv)} 人）"
                 + (f"　⚠ {self._layout_issues()}" if self._layout_issues() else ""))

    def set_curve_mood(self):
        """右侧面板：给当前曲线选中的干员设心情。"""
        if not self.curve_operator:
            return
        self._ask_and_set_mood(self.curve_operator)

    # ------------------------------------------------------------ 全员一览联动
    def on_roster_pick(self, who: str):
        """全员一览左键：对点看该干员的曲线，并在本班次看板上高亮他的位置。"""
        if who not in self.op_box.cget("values"):
            return
        self.curve_operator = who
        self.op_var.set(who)
        self._update_chart()
        if not self.board.highlight(who):
            self.status.configure(text=f"{who} 不在当前班次（点上方班次按钮可切换）")

    def on_roster_set_mood(self, who: str):
        """全员一览右键：设该干员心情（周期起点）。"""
        self._ask_and_set_mood(who)

    def _ask_and_set_mood(self, who: str):
        current = self.initial_moods.get(who, self._current_start_mood(who))
        v = ask_mood(self, who, current)
        if v is None:
            return
        self.initial_moods[who] = Decimal(v)
        self.recompute()

    def _current_start_mood(self, who: str) -> Decimal:
        if self.schedule:
            for op in self.schedule.shifts[0].world.all_operators():
                if op.name == who:
                    return op.mood
        return Decimal("24")

    def moods_at_now(self) -> dict:
        """滑块所在时刻的实际心情（设置中心用它做「按当前时刻回填」）。"""
        return self.traj.moods_at(self.current_t) if self.traj is not None else {}

    # ------------------------------------------------------------ 闲置入宿
    def _idle_entry_list(self):
        """把界面的逐次设置转成 `[IdleToDormEntry, ...]`——**只列改过默认的**
        （勾掉不参与的、或指定了目标的人）；没人改过就返回 `None`（＝全都参与、全自动）。

        目标有两类，界面用同一个下拉表达：
        - `宿舍01`/`宿舍02`…（**放进那间宿舍的空位**，不动任何人）→ `dorm=序号`
        - 干员名（**与这位满心情的宿舍干员互换**，他换出来闲置）→ `swap_with=名字`
        """
        out = []
        for name, (use, target) in self.idle_globals.items():          # 不限班次/周期的
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
                                       dorm=dorm,
                                       swap_with=(None if dorm else target) or None))
        return out or None

    def _idle_groups(self, cycles: Optional[int] = None, entries: Optional[dict] = None,
                     traj=None):
        """给设置框算**按时间排序的逐次表** → `[group, ...]`。

        `group = (标题, [(干员, 心情, 位置, 参与, 换谁, [(可选目标…)]), ...])`。

        为什么按"周期 → 班次"展开：心情跨班跨周期连续，所以**每次**"谁没满、谁在宿舍且满"
        都不一样 —— 候选与「换谁」的可选项都得按那一刻算（实测 3 个周期的事件分别落在
        12/18h、24/42h、66h）。只列出**真的有候选**的那几次。
        """
        cycles = int(cycles if cycles is not None else self.cycles)
        entries = self.idle_entries if entries is None else entries
        traj = traj if traj is not None else self.traj
        if self.schedule is None or traj is None:
            return []
        groups = []
        n_shifts = len(self.schedule.shifts)
        dorms = [f for f in (self.schedule.shifts[0].world.facilities if n_shifts else [])
                 if f.ftype == FacilityType.DORMITORY]
        for k in range(max(1, cycles)):
            for i, shift in enumerate(self.schedule.shifts):
                t0 = self.schedule.cycle_hours * k + self.schedule.starts[i]
                cands, targets = [], []
                # 空位选项：按"第几间宿舍"编号（01 起），只列**那一刻还有空位**的
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
                    rows.append((name, theme.fmt_mood(mood), where, use, target,
                                 [t for t in targets if t != name]))
                end = t0 + shift.hours
                title = (f"第 {k + 1} 周期 · 第 {i + 1} 班"
                         f"（{theme.fmt_clock(t0, self.schedule.cycle_hours)}"
                         f"–{theme.fmt_clock(end, self.schedule.cycle_hours)}）")
                groups.append((title, (k + 1, i + 1), rows))
        return groups

    def _idle_count(self) -> int:
        """当前设置下会有多少次"有人入宿"、共涉及多少人。"""
        groups = self._idle_groups()
        return sum(len([r for r in rows if r[3]]) for _title, _scope, rows in groups)

    def _sync_idle_label(self):
        """工具栏右侧的当前状态：`未开启` / `已开启 · 6 次（自动）`。"""
        if not self.idle_to_dorm.get():
            self.idle_detail.configure(text="未开启", fg=theme.MUTED)
            return
        self.idle_detail.configure(fg=theme.TEXT)
        has_target = any(t for _u, t in self.idle_entries.values())
        self.idle_detail.configure(
            text=f"已开启 · {self._idle_count()} 次"
                 + ("（含指定）" if has_target else "（自动）"))

    def _idle_status(self) -> str:
        """状态栏那一句口径。"""
        if not self.idle_to_dorm.get():
            return "闲置入宿：未开启"
        return (f"闲置入宿：已开启（每班开始时把未满的闲置干员安排进宿舍："
                f"{self._idle_count()} 次入宿；空位优先，"
                f"没空位就与宿舍里心情满的那位互换）")

    def edit_idle_to_dorm(self):
        """（旧入口，现等价于）打开设置中心的「闲置入宿」分区。"""
        return self.open_settings("idle")

    def apply_idle_to_dorm(self, enabled: bool, entries: dict):
        """「闲置入宿」设置落地（设置中心里**每次改动**都会调它）。

        返回**新的分组表**：改动会影响后面每一次的候选，所以面板要按新表重建。
        """
        self.idle_to_dorm.set(bool(enabled))
        self.idle_entries = dict(entries)
        self.recompute()                           # 看板/曲线跟着刷新
        self._sync_idle_label()
        return self._idle_groups()

    # ------------------------------------------------------------ 批量设置
    def batch_edit(self):
        """（旧入口，现等价于）打开设置中心的「干员与心情」分区。"""
        return self.open_settings("batch")

    def apply_batch(self, changes: dict, moods: dict) -> None:
        """「干员与心情」落地：干员改动按班次写回布局，心情整份替换周期起点。

        干员改动按"改过哪几班"返回（面板里可切班次，未改的不会丢）；
        心情是**周期起点**（全排班共用），面板只给"与导入值不同的那些"，
        所以这里整份替换 `initial_moods`（`恢复导入值` ⇒ 空差集 ⇒ 手动心情清空）。
        """
        n_ops = 0
        for i in sorted(changes):
            facs = changes[i]
            n_ops += sum(len(f.get("operators", [])) for f in facs)
            self.schedule = self.schedule.replaced_shift(i, facs)
        self.initial_moods = dict(moods)
        self._layout_sig = None
        self.recompute()
        which = ("第 " + "、".join(str(i + 1) for i in sorted(changes)) + " 班"
                 if changes else "未改动布局")
        self.status.configure(
            text=f"设置已生效：{which}"
                 + (f"（{n_ops} 个位置）" if changes else "")
                 + f"　｜　手动起点心情 {len(moods)} 名，其余用导入值")

    # ------------------------------------------------------------ 进驻事件（换心情）
    def _entry_candidates(self):
        """返回 `(触发者名单, 可交换对象名单)`。

        触发者 = 排班里可能触发 M15a 的干员（如菲亚梅塔）；
        可交换对象 = **同宿舍的其他干员排前面**，后面跟上排班里的其他干员
        （因为"基建任意位置"模式下任何位置的干员都能换）。
        """
        holders: list = []
        mates: list = []
        if self.schedule is None:
            return holders, mates
        for s in self.schedule.shifts:
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
        # 再补上"其它位置的干员"（任意位置模式用得上）
        others = [n for n in self.schedule.operator_names()
                  if n not in holders and n not in mates]
        return holders, mates + others

    def _when_token(self, when: str) -> str:
        """「③ 强制切换」的紧凑说法（不勾＝"没满就不换"是默认口径，不写，省工具栏宽度）。"""
        return "等她满" if when == "wait" else ""

    def _sync_entry_label(self):
        """工具栏右侧的**当前状态**：`未开启` / `已开启 · 换塞雷娅 · 等她满`。

        开关本身只有设置框里那一个（工具栏不再放第二个），所以"开没开"必须在这里写出来
        —— 关着时也写「未开启」，而不是留空。

        | 状态 | 文字 |
        |---|---|
        | 关 | `未开启` |
        | 开·默认口径 | `已开启 · 换前一位进驻` |
        | 开·自动挑 | `已开启 · 换最累的` |
        | 开·指定干员 | `已开启 · 换塞雷娅`（后面再跟 ` · 等她满`，勾了强制切换时） |
        | 开·按班次 | `已开启 · 按班次` |
        """
        if not self.entry_events.get():
            self.entry_detail.configure(text="未开启", fg=theme.MUTED)
            return
        self.entry_detail.configure(fg=theme.TEXT)
        if self.entry_per_shift:
            self.entry_detail.configure(text="已开启 · 按班次")
            return
        kind = entry_target_kind(self.entry_swap_with, self.entry_scope)
        who = {"auto": "换最累的", "named": f"换{self.entry_swap_with}",
               "default": "换前一位进驻"}[kind]
        token = self._when_token(self.entry_when)
        self.entry_detail.configure(text=f"已开启 · {who}" + (f" · {token}" if token else ""))

    def _entry_status(self) -> str:
        """状态栏里的换心情状态（工具栏那串是紧凑状态，这里给完整口径）。"""
        if not self.entry_events.get():
            return "换心情：未开启（按你写的初始心情开始；点工具栏「换心情设置」打开）"
        if self.entry_per_shift:
            return "换心情：按班次"
        kind = entry_target_kind(self.entry_swap_with, self.entry_scope)
        who = {"auto": "全基建最累的", "named": f"「{self.entry_swap_with}」",
               "default": "同宿舍前一位进驻者"}[kind]
        token = self._when_token(self.entry_when)
        return f"换心情：与{who}互换" + (f"·{token}" if token else "·没满就不换")

    def _per_shift_brief(self) -> str:
        """按班次的紧凑摘要：`（按班次：1巫恋·强等·3不用）`。"""
        labels = self.schedule.shift_labels() if self.schedule else []
        parts = []
        for i, label in enumerate(labels):
            ov = next((o for o in self.entry_per_shift if o.matches(i, label)), None)
            if ov is None:
                continue
            if ov.enabled is False:
                parts.append(f"{i + 1}不用")
                continue
            # 覆盖没写对象时用全局的写法判断（口径判定只有一处：entry_target_kind）
            raw = ov.swap_with if ov.swap_with is not None else self.entry_swap_with
            scope = ov.scope or self.entry_scope
            kind = entry_target_kind(raw, scope)
            who = {"auto": "最累", "named": raw, "default": "默认"}[kind]
            when = ov.when or self.entry_when
            token = self._when_token(when)
            parts.append(f"{i + 1}{who}" + (f"·{token}" if token else ""))
        return "（按班次：" + "·".join(parts) + "）" if parts else "（前一位）"

    def _entry_summary(self) -> str:
        """一句话说清当前配置（状态栏用）。"""
        if not self.entry_events.get():
            return "进驻事件：不结算（按你写的初始心情开始）"
        kind = entry_target_kind(self.entry_swap_with, self.entry_scope)
        who = {"auto": "全基建最累的那位", "named": f"「{self.entry_swap_with}」",
               "default": "同宿舍的前一位进驻者"}[kind]
        where = "仅同一宿舍" if kind == "default" else "基建任意位置"
        when = ("；勾了强制切换：到点没满就等她回满再换" if self.entry_when == "wait"
                else "；没勾强制切换：判定时她没满心情就不换")
        text = f"进驻事件：每班开始时结算——与{who}互换心情（{where}，只换心情、位置不动）{when}"
        if self.entry_per_shift:
            text += f"　｜　按班次覆盖：{self._per_shift_brief()[1:-1]}"
        return text

    def edit_entry_events(self):
        """（旧入口，现等价于）打开设置中心的「换心情」分区。"""
        return self.open_settings("entry")

    def apply_entry_event(self, picked) -> None:
        """「换心情」落地（设置中心里**每次改动**都会调它）。

        `picked` 是面板给的 6 元组 `(enabled, swap_with, scope, restore_back, when, per_shift)`。
        """
        if picked is None:
            return
        enabled, swap_with = picked[0], picked[1]
        scope = picked[2] if len(picked) > 2 else self.entry_scope
        restore_back = picked[3] if len(picked) > 3 else self.entry_restore_back
        when = normalize_entry_when(picked[4]) if len(picked) > 4 else self.entry_when
        per_shift = picked[5] if len(picked) > 5 else self.entry_per_shift
        self.entry_events.set(enabled)
        self.entry_swap_with = swap_with
        self.entry_scope = scope
        self.entry_restore_back = restore_back
        self.entry_when = when or "full"      # 界面口径：缺省＝"没满就不换"
        self.entry_per_shift = list(per_shift or [])
        self._sync_entry_label()
        self.recompute()                      # 先重算（recompute 会写状态栏）
        if not self._entry_candidates()[0]:
            self.status.configure(text="本排班里没有能触发进驻事件的干员（如菲亚梅塔），"
                                        "这个开关暂时不会有任何效果")
        else:
            self.status.configure(text=self._entry_summary())   # 再用配置摘要盖上去

    def _layout_issues(self) -> str:
        """当前班次布局的自检问题（上游约束：单类型上限 / 建造位总量 9 / 人数 ≤ 等级容量）。"""
        if self.schedule is None:
            return ""
        issues = self.schedule.shifts[self._editing_shift_index()].world.validate()
        return "；".join(issues)

    def _facilities_of(self, idx: int):
        return [dict(f, operators=list(f.get("operators", [])))
                for f in self.schedule.shifts[idx].facilities]

    # ------------------------------------------------------------ 练度（精英化）
    def _elite_badges(self) -> dict:
        """`{干员: "E1"}`——取该干员在各班次里**最低**的精英化（只有非 E2 才进表）。

        为什么取最低：同一个人在不同班次可以有不同练度（少见但合法），
        用最低那档才不会漏掉"某一班他的技能其实没生效"。
        """
        out: dict = {}
        if self.schedule is None:
            return out
        for shift in self.schedule.shifts:
            for op in shift.world.all_operators():
                badge = elite_badge(op)
                if badge and badge < out.get(op.name, "E9"):
                    out[op.name] = badge
        return out

    def _operator_obj(self, name: str):
        """找这个干员的 `Operator`（优先当前班次，其次第一个有他的班次）。"""
        if self.schedule is None or not name:
            return None
        idx = self._editing_shift_index()
        for shift in [self.schedule.shifts[idx]] + list(self.schedule.shifts):
            op = shift.world.get_operator(name)
            if op is not None:
                return op
        return None

    def _elite_text(self, name: str) -> str:
        """练度摘要（对点查询用）：`练度 E1 · 已解锁 6 条心情技能；因未满练少 1 条（「手工艺品·β」）`。"""
        op = self._operator_obj(name)
        if op is None:
            return ""
        unlocked, locked = mood_skill_summary(op)
        text = f"练度 E{op.elite}（等级 {op.level}）· 已解锁 {unlocked} 条心情技能"
        if locked:
            names = "、".join(f"「{n}」（E{e}）" for n, e, _lv in locked[:3])
            more = f" 等 {len(locked)} 条" if len(locked) > 3 else ""
            text += f"；⚠ 因未满练少 {len(locked)} 条：{names}{more}"
        return text

    def _apply_facilities(self, idx: int, facs):
        """改完某个班次的布局 → 重建 Schedule → 重算。"""
        self.schedule = self.schedule.replaced_shift(idx, facs)
        self._layout_sig = None
        self.recompute()

    def edit_shifts(self):
        """（旧入口，现等价于）打开设置中心的「时间轴」分区。"""
        return self.open_settings("timeline")

    def apply_shift_hours(self, hours) -> None:
        """「时间轴」落地：改各班时长（面板已保证"各班长之和 == 周期"）。"""
        if self.schedule is None or not hours:
            return
        self.schedule = self.schedule.with_hours(hours)
        self._build_shift_buttons()
        self.recompute(fit_slider=True)

    def _on_cycles(self):
        self.cycles = int(self.cycles_var.get())
        self.recompute(fit_slider=True)

    # ================================================================== 曲线面板
    def _sync_operator_box(self):
        if self.schedule is None:
            return
        names = self.schedule.operator_names()
        self.op_box.configure(values=names)
        if self.curve_operator not in names:
            self.curve_operator = names[0] if names else ""
        self.op_var.set(self.curve_operator)
        self._update_chart()

    def _on_operator_pick(self):
        self.curve_operator = self.op_var.get()
        self._update_chart()

    def _step_operator(self, delta: int):
        names = list(self.op_box.cget("values"))
        if not names:
            return
        i = names.index(self.curve_operator) if self.curve_operator in names else 0
        self.curve_operator = names[(i + delta) % len(names)]
        self.op_var.set(self.curve_operator)
        self._update_chart()

    def _update_chart(self):
        if self.traj is None or not self.curve_operator:
            self.chart.clear()
            self.stats.configure(text="")
            return
        self.chart.set_data(self.traj, self.curve_operator)
        self.stats.configure(text=self._stats_text(self.curve_operator))
        self.roster.set_selected(self.curve_operator)

    def _stats_text(self, name: str) -> str:
        traj = self.traj
        lo, lo_t, hi, hi_t = traj.bounds(name)
        spans = traj.red_face_spans(name)
        total_red = sum((b - a for a, b in spans), Decimal("0"))
        idx = self.schedule.index_at(self.current_t)
        lines = [
            # 速率：此刻（滑块所在时刻）与"本班平均"——看曲线时最想知道的两个数
            f"此刻 {theme.fmt_rate(traj.rate_at(name, self.current_t))}"
            f"　本班平均 {theme.fmt_rate(traj.shift_average_rate(name, idx))}"
            f"（{self.schedule.shifts[idx].label}）",
            f"起点 {theme.fmt_mood(traj.mood_at(name, 0))}　"
            f"周期末 {theme.fmt_mood(traj.mood_at(name, traj.total_hours))}",
            f"最低 {theme.fmt_mood(lo)} @ {theme.fmt_clock(lo_t, traj.schedule.cycle_hours)}"
            f"　最高 {theme.fmt_mood(hi)} @ {theme.fmt_clock(hi_t, traj.schedule.cycle_hours)}",
            f"红脸 {len(spans)} 段，合计 {theme.fmt_hours(total_red)}" if spans else "红脸 无",
        ]
        per = traj.min_mood_at_each_shift(name)
        lines.append("各班最低：" + "　".join(f"{l} {theme.fmt_mood(v)}" for l, v in per))
        elite = self._elite_text(name)          # 练度：技能能不能生效就看它
        if elite:
            lines.append(elite)
        return "\n".join(lines)

    def _facility_at(self, t: Decimal) -> str:
        if self.schedule is None:
            return ""
        idx = self.schedule.index_at(t)
        fac = self.schedule.shifts[idx].world.facility_of(self.curve_operator)
        return fac.display_name if fac else "未排班"

    # ================================================================== 班次条
    def _build_shift_buttons(self):
        for b in self.shift_buttons:
            b.destroy()
        self.shift_buttons.clear()
        if self.schedule is None:
            return
        tk.Label(self.shift_bar, text="班次", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(0, 6))
        for i, s in enumerate(self.schedule.shifts):
            b = ttk.Button(self.shift_bar,
                           text=f"{i + 1}. {s.label}（{theme.fmt_hours(s.hours)}）",
                           command=lambda k=i: self.set_time(self.schedule.starts[k]))
            b.pack(side="left", padx=(0, 4))
            self.shift_buttons.append(b)
        self._space_only_plays(self.shift_bar)     # 新建的班次按钮也要"空格＝播放/暂停"

    def _highlight_shift_button(self):
        idx = self.schedule.index_at(self.current_t) if self.schedule else -1
        for i, b in enumerate(self.shift_buttons):
            b.configure(style="Accent.TButton" if i == idx else "TButton")

    # ================================================================== 播放
    def toggle_play(self):
        if self.traj is None:
            return
        self._playing = not self._playing
        self.play_btn.configure(text="■ 停止" if self._playing else "▶ 播放")
        if self._playing:
            self._play_last = time.perf_counter()      # 真实流逝时间的起点
            self._play_tick()
        else:
            if self._play_job:
                self.after_cancel(self._play_job)
                self._play_job = None
            self.refresh_view(quick=False)      # 停播后补一次完整上色

    def _on_speed(self):
        """播放速度：单位是 **模拟秒 / 真实秒（s/s）**——`1x` 就是实时。

        例：`3600x` = 每真实秒推进 3600 模拟秒 = **1 小时/秒**（24h 周期 24 秒放完）。
        工具栏只放短提示（宽度有限），完整解释写进状态栏。
        """
        text = self.speed_var.get().strip().rstrip("xX")
        try:
            self.play_speed = Decimal(text)
        except (InvalidOperation, ValueError):
            self.play_speed = PLAY_BASE_SECONDS_PER_SEC
        label = self.speed_var.get().strip()
        hint = PLAY_SPEED_HINTS.get(label, "")
        if hasattr(self, "speed_hint"):
            self.speed_hint.configure(text=f"＝{hint}" if hint else "")
        if hasattr(self, "status"):
            self.status.configure(
                text=f"播放速度 {label}＝每真实秒推进 {theme.fmt_mood(self.play_speed, 0)} 模拟秒"
                     + (f"（{hint}）" if hint else "")
                     + f"，速度单位是「模拟秒/真实秒」；1x 即实时（24h 周期要放 24 小时），"
                       f"想看完整一天用 3600x")

    def _play_tick(self):
        if not self._playing or self.traj is None or not self.winfo_exists():
            return
        total = self._total_hours()
        # 推进量按**真实流逝时间**算（不是名义间隔）：一次 tick 里还要做刷新，
        # 用名义 60ms 会让实际速度比标称慢 ~20%。
        # 速度单位是 s/s：step(小时) = 基准 × 倍率 × 真实秒数 ÷ 3600。
        now = time.perf_counter()
        dt = min(max(now - self._play_last, 0.0), 0.5)     # 卡顿后不跳帧
        self._play_last = now
        step = (PLAY_BASE_SECONDS_PER_SEC * self.play_speed
                * Decimal(str(round(dt, 6))) / Decimal(3600))
        nxt = self.current_t + step
        if nxt > total:
            nxt = Decimal("0")
        self.set_time(nxt, quick=True)
        self._play_job = self.after(PLAY_TICK_MS, self._play_tick)

    # ================================================================== 收尾
    def destroy(self):
        """退出前取消所有挂起的 `after` 回调。

        否则窗口销毁后回调仍会触发，Tk 会打印
        `invalid command name "..._autoload_sample"`（测试里尤其吵）。
        """
        for attr in ("_refresh_job", "_settle_job", "_play_job", "_autoload_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        super().destroy()


def main() -> int:
    app = MoodSocApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
