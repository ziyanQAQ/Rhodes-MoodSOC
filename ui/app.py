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

import gc
import queue
import sys
import threading
import time
import tkinter as tk
import weakref
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Sequence

# 允许**直接运行本文件**（`python ui/app.py` / IDE 的 Run）：直接跑时 `ui` 不是包，
# 相对导入会失败；把仓库根目录放进 sys.path 后用绝对导入，与 `python -m ui` 等价。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store.session import Session  # noqa: E402
from store.session import MAX_CYCLES  # noqa: E402
from store.session import seat_values  # noqa: E402
from ui import theme  # noqa: E402
from ui.board import BaseBoard, facility_tag  # noqa: E402
from ui.chart import MoodChart  # noqa: E402
from ui.dialogs import ask_operator, ask_mood  # noqa: E402
from ui.roster import RosterStrip  # noqa: E402
# 视图**只从 store 取计算与状态**：排班引擎、装配、心情查询都在 Session 与 store.schedule 里。
from ui.schedule import all_operator_names  # noqa: E402  （转发自 store.schedule）
from mood_soc import entry_target_kind  # noqa: E402
from mood_soc.battery import to_decimal  # noqa: E402
from mood_soc.config import MOOD_MAX  # noqa: E402
from mood_soc.models import normalize_entry_when  # noqa: E402
from data.paths import MAA_SAMPLE, RES as DATA_RES  # noqa: E402

SAMPLE = MAA_SAMPLE          # 冷启动自载的示例排班（`resources/…`，见 data/paths.py）
STEP_FINE = Decimal("0.25")      # 方向键/微调步长（15 分钟）

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

#: 关窗口时等待"在算的重算工作线程"的上限（秒）。见 `MoodSocApp.destroy()`：
#: 有界 join 是为了让线程不可能在本窗口销毁之后还把 Tk 对象在非主线程里回收。
RECALC_JOIN_TIMEOUT = 5.0

# ---------------------------------------------------------------------------
# 「GC 只在主线程跑」的全局记账
#
# `gc.disable()/enable()` 是**解释器级**的（不是线程局部），所以这里用一个模块级计数器
# 表达"现在有几个重算工作线程活着 + 我们是否替它们关掉了 GC"。
#
# 为什么非要这样：Tk 对象的析构（`tkinter.Variable/Tk.__del__` → Tcl 解释器销毁）**只能在主线程**。
# 只要 GC 有机会在工作线程里跑，就可能把"创建完又被丢掉的 Tk 根"在非主线程里收掉 ⇒
# `Tcl_AsyncDelete: async handler deleted by the wrong thread`（**进程直接 abort**，不是异常；
# 实测全量测试合并跑会偶发崩）。所以：**工作线程里只关不恢复**，恢复一律交给主线程
# （`_gc_on_when_idle()`，在 `_poll_recalc` / `_publish_recompute` / `refresh_view` /
# `wait_recalc` / `destroy` 里调 —— 全都是主线程路径）。
# 万一 `destroy()` 的 join 超时（线程还没收工），GC 就**继续关着**，由下一次主线程路径
# 顺手恢复（测试里＝下一个窗口建起来；真实场景＝进程反正要退了）——宁可晚恢复，不可错线程收。
# ---------------------------------------------------------------------------
_gc_lock = threading.Lock()
_gc_workers = 0          # 还活着的工作线程数（只统计我们自己起的重算线程）
_gc_restore = False      # 我们替它们关掉了 GC ⇒ 等它们都结束、由主线程恢复


def _gc_off_for_worker() -> None:
    """工作线程入口：登记 + 关 GC（**不恢复**，恢复看 `_gc_on_when_idle()`）。"""
    global _gc_workers, _gc_restore
    with _gc_lock:
        _gc_workers += 1
        if gc.isenabled():                 # 环境本来就关着就别抢着开
            gc.disable()
            _gc_restore = True


def _gc_worker_done() -> None:
    """工作线程出口：只减计数（**不在线程里 `gc.enable()`**）。"""
    global _gc_workers
    with _gc_lock:
        _gc_workers = max(0, _gc_workers - 1)


def _gc_on_when_idle() -> None:
    """**主线程**调用：没有工作线程了就把 GC 恢复回去。

    开销只有一次布尔读（`refresh_view` 每帧都会调；只有真的"我们关过 GC"时才取锁复核）。
    """
    global _gc_restore
    if not _gc_restore:                        # 快速路径：正常工作期间都走这里，不取锁
        return
    with _gc_lock:
        if _gc_workers <= 0 and _gc_restore:
            gc.enable()
            _gc_restore = False


def _live_recalc_workers() -> int:
    """当前还活着的工作线程数（测试与诊断用）。"""
    with _gc_lock:
        return _gc_workers


class _ViewVar:
    """**ViewVar** —— 长得像 `tk.BooleanVar` 的轻量视图（没有 .get(_var) 的重载）。"""

    def __init__(self, session, field: str):
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_field", field)

    def get(self) -> bool:
        return bool(getattr(self._session, self._field))

    def set(self, value) -> None:
        setattr(self._session, self._field, bool(value))


class MoodSocApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Rhodes-MoodSOC · 基建心情排班")
        self.geometry("1560x950")
        # 最小宽度按"工具栏放得下"来定（实测工具栏需要 ~1370px），否则最右侧按钮会被裁掉
        self.minsize(1400, 780)
        self.configure(bg=theme.BG)

        # **状态与重算都在 Session**（`store/session.py`）：图形界面只是它的视图。
        # 下面这些 `self.xxx` 都是**别名**（property / ViewVar），没有第二份状态 ——
        # 这样程序接口（`api/`）与界面永远算的是同一件事。
        self.session = Session()
        # 周期数是**设置中心**里的一个控件；变量挂在 app 上（唯一真源），
        # 设置窗口只是把它接到下拉框上——工具栏时代它挂在工具栏里。
        self.cycles_var = tk.StringVar(value="1")
        # 「跟随滑块」（「干员与心情」的时刻跟不跟主界面滑块）也是 app 上的唯一真源：
        # 那一页会被标脏重建（改时长/周期数时），状态放面板里就会"切个页回来勾选没了"。
        self.follow_slider = tk.BooleanVar(value=False)
        self.settings_dlg = None               # 「设置」中心的窗口（唯一设置入口）
        # 「导入排班」读到的附赠信息（见 mood_soc/importer.py）：
        self.entry_events = _ViewVar(self.session, "entry_events")
        self.entry_swap_with: Optional[str] = None     # None = 默认「前一位进驻」；"any" = 自动挑最累的
        self.entry_scope = "dorm"                      # "dorm" 仅同宿舍 / "anywhere" 基建任意位置
        self.entry_restore_back = True                 # 界面固定：只换心情、两人留原位
        self.entry_when = "full"                       # wait（勾了强制切换：等她满）/ full（没满就不换）
        self.entry_per_shift: list = []                # 按班次覆盖（EntryShiftOverride 列表）
        # 闲置入宿（未满的闲置干员进宿舍）：总开关 + 逐人设置 {名字: (参与, 换谁 或 None)}
        self.idle_to_dorm = _ViewVar(self.session, "idle_to_dorm")
        self.play_speed = Decimal("1")
        self.current_t = Decimal("0")
        self.curve_operator = ""
        self._playing = False
        self._play_job = None
        self._layout_sig = None
        #: 看板当前画的是**哪一份引擎世界**（`world_at` 每个换班执行点各自一份深拷贝）。
        #: 判"要不要重画看板"不能只看班次号：**内部换班执行点不改 `shift_index`**，见 `set_time`。
        self._layout_world = None
        self._setting_scale = False
        self._pending_t = None
        self._refresh_job = None
        self._settle_job = None
        self._play_last = 0.0
        self._last_refresh = 0.0
        self._roster_dirty = False
        self._roster_names: list = []     # 「全员一览」当前画的是哪些干员（没变就不重建）
        # 异步重算（P5）：编辑不再冻住界面 —— 工作线程算，主线程轮询收结果。
        self._recalc_gen = 0              # 请求代数：只有"最新那一代"的结果会被采用
        self._recalc_done_gen = 0         # 已经落地的最新一代
        self._recalc_thread = None
        self._recalc_threads: list = []    # 还活着的工作线程（`destroy()` 会逐个有界 join）
        self._recalc_q: "queue.Queue" = queue.Queue()
        self._recalc_fit = False          # 这一轮要不要把滑块拉回起点
        self._recalc_ready = None         # 算好但还没落地的结果（等面板防抖窗口过去）
        self._recalc_poll_job = None
        self._recalc_listeners: list = []  # 结果落地后要刷新的面板（弱引用）
        self._status_after_recalc: Optional[str] = None   # 落地后要盖上去的状态文案

        self._init_style()
        self._build_toolbar()
        self._build_body()
        self._build_bottom()
        self._bind_keys()
        self._space_only_plays(self)          # 空格永远＝播放/暂停（别去"按下"聚焦的按钮）

        self._autoload_job = self.after(60, self._autoload_sample)

    # ================================================================== 状态别名
    # 下面全是 `self.session` 的**别名**（视图读写的还是同一份状态）。
    # 为什么留着它们：`ui/` 其余文件与全部界面测试都按 `app.schedule` / `app.initial_moods`
    # 这套名字取值，别名让"状态搬家"不改变任何调用点的写法。
    @property
    def schedule(self):
        return self.session.schedule

    @schedule.setter
    def schedule(self, value):
        self.session.schedule = value

    @property
    def traj(self):
        return self.session.traj

    @traj.setter
    def traj(self, value):
        self.session.traj = value

    @property
    def initial_moods(self):
        return self.session.initial_moods

    @initial_moods.setter
    def initial_moods(self, value):
        self.session.initial_moods = dict(value)

    @property
    def mood_events(self):
        return self.session.mood_events

    @mood_events.setter
    def mood_events(self, value):
        self.session.mood_events = list(value or ())

    @property
    def cycles(self) -> int:
        return self.session.cycles

    @cycles.setter
    def cycles(self, value: int) -> None:
        self.session.cycles = int(value)

    @property
    def operator_pool(self):
        return list(getattr(self.session.loaded, "pool", []) or [])

    @property
    def initial_global(self):
        return dict(getattr(self.session.loaded, "initial_global", {}) or {})

    @property
    def import_reports(self):
        return list(getattr(self.session.loaded, "reports", []) or [])

    @property
    def import_summary(self) -> str:
        loaded = self.session.loaded
        return loaded.summary() if loaded is not None else ""

    @property
    def idle_entries(self):
        return self.session.idle_entries

    @idle_entries.setter
    def idle_entries(self, value):
        self.session.idle_entries = dict(value)

    @property
    def idle_globals(self):
        return self.session.idle_globals

    @idle_globals.setter
    def idle_globals(self, value):
        self.session.idle_globals = dict(value)

    # —— 闲置入宿的**全局口径**（锁定位置数 / 黑名单）与面板要用的辅助读法 ——
    def idle_protected_slots(self) -> int:
        """**锁定位置数**（文档 §5）：按竖向正序锁前 N 个位置（默认 5）。"""
        return int(self.session.idle_protected_slots)

    def idle_blacklist(self) -> list:
        """**黑名单**（文档 §6）：永远不能通过闲置入宿进宿舍的人。"""
        return list(self.session.idle_blacklist)

    def idle_name_pool(self) -> list:
        """「黑名单」下拉的候选池＝这份排班里出现过的所有干员名。"""
        if self.schedule is None:
            return []
        return list(self.schedule.operator_names())

    # —— 「不在基建」名单（既不在工作设施、也不在宿舍的人）：住在 Session 上 ——
    @property
    def detached(self):
        return self.session.detached

    @detached.setter
    def detached(self, value):
        self.session.set_detached(list(value or ()))

    # —— 进驻事件（换心情）的四项设置：住在 Session 上，界面按别名读写 ——
    @property
    def entry_swap_with(self):
        return self.session.entry_swap_with

    @entry_swap_with.setter
    def entry_swap_with(self, value):
        self.session.entry_swap_with = value

    @property
    def entry_scope(self):
        return self.session.entry_scope

    @entry_scope.setter
    def entry_scope(self, value):
        self.session.entry_scope = value

    @property
    def entry_restore_back(self):
        return self.session.entry_restore_back

    @entry_restore_back.setter
    def entry_restore_back(self, value):
        self.session.entry_restore_back = bool(value)

    @property
    def entry_when(self):
        return self.session.entry_when

    @entry_when.setter
    def entry_when(self, value):
        self.session.entry_when = value

    @property
    def entry_per_shift(self):
        return self.session.entry_per_shift

    @entry_per_shift.setter
    def entry_per_shift(self, value):
        self.session.entry_per_shift = list(value or ())
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
        """工具栏＝**3 组**（组间有细分隔线）：设置类 │ 状态摘要 │ 播放控制。

        ```
        [导入排班…] [设置…] 周期数[1▾]  │  已开启 · 按班次 · 未开启  │  [▶播放] 速度[1x] ＝实时  [回到起点]
        ```

        - **设置类**：导入 / 设置（唯一设置入口）；**周期数**放这儿是因为它调得频，
          与「设置…」→ 时间轴里的那一个是**同一个变量**（`self.cycles_var`），两处永远一致。
        - **状态摘要**：换心情 · 闲置入宿（点它也能开设置）；两个 `_detail` 标签的名字与文案
          保持原样 —— `_sync_entry_label` / `_sync_idle_label` 是唯一写入点，测试也按这两句断言。
        - **播放控制**：播放 / 速度 / 回到起点。
        """
        bar = tk.Frame(self, bg=theme.BG)
        bar.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, theme.GAP))
        self.toolbar = bar

        # —— 组 1：设置类 ——
        ttk.Button(bar, text="导入排班…", style="Accent.TButton",
                   command=self.import_files).pack(side="left")
        ttk.Button(bar, text="设置…", command=self.open_settings).pack(side="left",
                                                                       padx=(6, 0))
        tk.Label(bar, text="周期数", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(12, 4))
        self.cycles_box = ttk.Combobox(bar, textvariable=self.cycles_var, width=3,
                                       state="readonly",
                                       values=tuple(str(i) for i in range(1, MAX_CYCLES + 1)))
        self.cycles_box.pack(side="left")
        self.cycles_box.bind("<<ComboboxSelected>>", lambda _e: self._on_cycles())

        self._toolbar_sep(bar)

        # —— 组 2：状态摘要 ——
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

        self._toolbar_sep(bar)

        # —— 组 3：播放控制 ——
        play = tk.Frame(bar, bg=theme.BG)
        play.pack(side="left")
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

    @staticmethod
    def _toolbar_sep(bar):
        """工具栏的**组分隔线**（一条细竖线，高度跟着这一行）。"""
        sep = tk.Frame(bar, bg=theme.BORDER, width=1)
        sep.pack(side="left", fill="y", padx=theme.GAP, pady=1)
        return sep

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
        return self.session.imported_moods()

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

        # 看板**只做展示**（2026-10）：不传任何点击回调 —— 位置/房间头都点不动，
        # 手动入宿与心情的入口在「设置 → 干员与心情 / 闲置入宿」。
        self.board = BaseBoard(body)
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
        # （只做**点选**：左键 = 对点看曲线；右键设心情已随"看板只做展示"一起撤掉）
        self.roster = RosterStrip(self, on_pick=self.on_roster_pick)
        self.roster.pack(fill="x", pady=(theme.GAP, 0))

    # ================================================================== 底部
    def _build_bottom(self):
        bottom = tk.Frame(self, bg=theme.BG)
        bottom.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, 4))

        self.shift_bar = tk.Frame(bottom, bg=theme.BG)
        self.shift_bar.pack(fill="x")
        self.shift_buttons: list = []
        # 「周期」选择器（只在周期数 > 1 时出现）：见 `_build_shift_buttons`
        self.cycle_buttons: list = []

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
        """冷启动时自动载入示例排班（**只在开局没排班时**）。"""
        self._autoload_job = None
        if not self.winfo_exists():
            return
        if self.schedule is not None:
            # 启动那 60ms 内已经载入过（手动导入、或测试里先 load_paths）→ 不要覆盖人家
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
        """按文件集合装配排班（可能抛 ValueError，调用方展示原因）。

        格式**自动识别**（`mood_soc/importer.py`）：本工具场景 / MAA 排班 / v3 求解输出 /
        v4 蓝图+干员池——点一次「导入排班…」即可，不需要先选格式。
        """
        # 设置中心里握着"当前排班"（面板建好后就认那一份）→ 换排班前先把它关掉，
        # 免得面板往旧 schedule 上写（各个面板都是进入分区时按最新排班重建的）。
        if self.settings_dlg is not None and self.settings_dlg.winfo_exists():
            self.settings_dlg.destroy()
        self.settings_dlg = None
        # ⚠️ 换排班 = 之前还在算的那次异步重算**整个作废**（代数推高 + 丢掉待落地结果），
        #    否则旧排班的结果可能在新排班之后落地、把状态栏与看板又刷回去（实测踩过）。
        self._recalc_gen += 1
        self._recalc_ready = None
        self._recalc_done_gen = self._recalc_gen
        # 旧排班那次编辑留下的"落地后要写进状态栏的话"也一起作废（否则会盖掉导入摘要）；
        # 轮询任务也停掉 —— 它跑完会去 `_settle_recalc`，又把状态栏写成通用那句。
        self._status_after_recalc = None
        if self._recalc_poll_job is not None:
            try:
                self.after_cancel(self._recalc_poll_job)
            except tk.TclError:
                pass
            self._recalc_poll_job = None
        # ⚠️ 还要**摘掉旧工作线程的引用**：`recompute_async` 是看"线程还活着"决定要不要起新的
        #    （"算完自己接着跑最新一代"由**轮询任务**负责），而轮询上面刚被取消 —— 两者一起看就
        #    会出现死结：换排班时旧线程还没算完 ⇒ 紧跟的那次编辑只会把代数推高、**不起新线程**，
        #    而唯一会接着跑新一代的轮询已经不在了 ⇒ 那一代**永远不落地**（表现：界面卡在
        #    「计算中…」、曲线还是换排班前那条；实测 `wait_recalc` 只能靠 120s 超时放过）。
        #    摘掉引用后新旧线程互不干扰：旧线程的结果本来就会因**代数过期**被丢弃。
        self._recalc_thread = None
        # 装配（解析 → 排班 → 池/变量初始值/导入报告 → 同步 JSON 里的设置）**全在 Session 里**：
        # `load_paths` 读完文件会顺手把 `entry_events` / `idle_to_dorm` / `initial_global`
        # 同步成会话设置，界面只负责把它们画出来。
        self.session.load_paths(paths)
        self.current_t = Decimal("0")
        self._sync_entry_label()
        self._sync_idle_label()
        self._build_shift_buttons()
        # ⚠️ 顺序：先 `recompute`（重建 traj），再 `_sync_operator_box`（挑一个**新排班里存在**的
        #    对点对象并刷曲线）。反过来会拿着**旧 traj** 去查新名字 —— 换成"干员集合不同"的排班
        #    （如空蓝图 / 另一个基地）时 `red_face_spans` 直接 KeyError。
        self.recompute(fit_slider=True)
        self._sync_operator_box()
        if self.import_summary:                 # 用导入摘要盖住"重算耗时"那句
            self.status.configure(text=self.import_summary)

    def import_files(self):
        paths = filedialog.askopenfilenames(
            title="选择排班 / 蓝图文件（自动识别格式；可多选：12h / 6h / 6h 三个文件也对）",
            initialdir=str(DATA_RES),
            filetypes=[("排班 / 蓝图 JSON", "*.json"), ("全部文件", "*.*")])
        if not paths:
            return
        try:
            self.load_paths(list(paths))
        except ValueError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
        except OSError as exc:
            messagebox.showerror("读取失败", str(exc), parent=self)

    def recompute(self, fit_slider: bool = False):
        """结构变化后重算轨迹（**同步**；导入 / 测试 / 程序路径用它）。

        界面上的"编辑"走 `recompute_async()`（后台线程 + 落地），这样窗口不再冻住 ——
        这里保留同步语义：函数返回时轨迹已经是新的。
        """
        if self.schedule is None:
            return
        t0 = time.perf_counter()
        self.status.configure(text="计算中…")
        self.update_idletasks()
        # 重算**只有一条路径**：`Session.recompute()`（它自己读会话里的全部设置，
        # 包括"全不勾的 per_shift 要原样传空列表"那条口径，见 store/session.py）。
        self.session.recompute()
        self.session.last_recompute_ms = (time.perf_counter() - t0) * 1000
        self._publish_recompute(fit_slider)

    # ---------------------------------------------------------------- 异步重算（P5）
    def recompute_async(self, fit_slider: bool = False):
        """**不阻塞界面**的重算：工作线程算，主线程轮询收结果（编辑路径用它）。

        规则（连续编辑时只落地最后一版）：
          - 每来一次请求 `_recalc_gen += 1`；已经在算的那次算完若"代数过期"就**丢弃**，
            紧接着按最新设置再算一次；
          - 结果只由**主线程**装进 `Session`（`adopt`），再触发 `_publish_recompute` 刷新界面；
          - 计算期间状态栏写「计算中…」，窗口照常响应（拖滑块/切页/继续编辑都不卡）。
        """
        if self.schedule is None:
            return
        self._recalc_gen += 1
        self._recalc_fit = self._recalc_fit or bool(fit_slider)
        if self._recalc_thread is not None and self._recalc_thread.is_alive():
            return                              # 让它算完自己接着跑最新一代
        self._launch_recalc()

    def _launch_recalc(self):
        """按**当前**设置冻结输入并起一个工作线程。"""
        inputs = self.session.recompute_inputs()
        if inputs is None:
            return
        gen, fit = self._recalc_gen, self._recalc_fit
        sigs = inputs.get("_sigs")           # 增量重算的"逐段指纹"（供周期数/后段编辑续算）
        self._recalc_fit = False
        self.status.configure(text=f"计算中…（周期数 {self.cycles}）")
        self.update_idletasks()
        # ⚠️ 所有跨线程要用的东西都在**启动前**取成局部量：工作线程里**一次都不碰 `self`**
        #    （`self` 是 Tk 控件）。线程里哪怕只是"读一下 app 的属性"，都可能让 Tk 对象
        #    在错误线程里被回收 —— 那是 `Tcl_AsyncDelete: async handler deleted by the
        #    wrong thread` 那个**直接崩进程**的病根之一（见 `destroy()` 的注释）。
        queue_ = self._recalc_q

        def _work():
            # ⚠️ 工作线程里**只关 GC、绝不恢复**（见模块头 `_gc_off_for_worker` 的说明）：
            #    `gc.enable()` 是解释器级的，只要析构还有机会在工作线程里发生，Tk 就会报
            #    `main thread is not in main loop`，再往下就是 `Tcl_AsyncDelete` **崩进程**。
            #    恢复交给主线程的 `_gc_on_when_idle()`（本线程结束后由轮询任务触发）。
            #    计数一直压到"线程真正干完活"为止（`queue_.put` 之后），否则并发窗口里
            #    主线程可能提前把它恢复掉。
            _gc_off_for_worker()
            try:
                try:
                    traj = Session.compute_trajectory(inputs)
                except Exception as exc:        # noqa: BLE001 —— 线程里出错也要让主线程知道
                    traj = exc
                queue_.put((gen, fit, sigs, traj))
            finally:
                _gc_worker_done()

        thread = threading.Thread(target=_work, name="dsh-recalc", daemon=True)
        self._recalc_thread = thread
        # 登记所有"还活着的工作线程"：`load_paths` 会摘掉 `_recalc_thread`（好让下一次编辑
        # 起新线程），但**旧的还在跑** —— 关窗口时必须把它们一起收干净，见 `destroy()`。
        self._recalc_threads = [t for t in self._recalc_threads if t.is_alive()]
        self._recalc_threads.append(thread)
        thread.start()
        if self._recalc_poll_job is None:
            self._recalc_poll_job = self.after(30, self._poll_recalc)

    def _panels_busy(self) -> bool:
        """设置中心里有没有"编辑还在防抖窗口里"的面板？

        ⚠️ 为什么异步重算要看它：面板的防抖（心情 500ms / 闲置入宿 250ms）到点才 `_collect`，
        而**落地刷新会把面板里的格子按轨迹重写** —— 如果重算比防抖先落地（P5 之后完全可能），
        用户刚敲进去的值会被轨迹值盖掉、那次编辑就丢了（实测：改「泡泡 8」后
        `apply_batch` 收到空 mood）。所以：**面板还在等防抖就先别落地**，
        等它把编辑提交上去（`has_pending_edit()` 变假）再刷新。
        """
        dlg = self.settings_dlg
        if dlg is None:
            return False
        try:
            return bool(dlg.winfo_exists() and dlg.has_pending_edit())
        except tk.TclError:
            return False

    def _poll_recalc(self):
        """主线程轮询：收结果 → 采用（或丢弃）→ 需要的话接着算最新一代。

        顺序：① 收线程结果（只留最新一条）→ ② 线程还活着就继续等 → ③ 有"最新一代"的结果
        但**面板还在防抖**就先不落地（否则会把用户正在敲的值盖掉）→ ④ 期间又改了 / 结果过期
        就按最新设置重算 → ⑤ 都落地了收尾。
        """
        self._recalc_poll_job = None
        _gc_on_when_idle()                       # 主线程路径：工作线程都收工了就把 GC 恢复
        if not self.winfo_exists():
            return
        latest = None
        while True:
            try:
                item = self._recalc_q.get_nowait()
            except queue.Empty:
                break
            latest = item                        # 队列里只留最后一条（旧的直接丢）
        if latest is not None and latest[0] == self._recalc_gen:
            self._recalc_ready = latest          # 过期代次直接丢，不进 ready
        if self._recalc_thread is not None and self._recalc_thread.is_alive():
            self._recalc_poll_job = self.after(30, self._poll_recalc)
            return
        self._recalc_thread = None
        if self._recalc_ready is not None and self._recalc_ready[0] == self._recalc_gen:
            if self._panels_busy():
                self._recalc_poll_job = self.after(30, self._poll_recalc)
                return
            gen, fit, sigs, traj = self._recalc_ready
            self._recalc_ready = None
            self._recalc_done_gen = gen
            if isinstance(traj, Exception):
                self.status.configure(text=f"重算失败：{traj}")
            else:
                self.session.adopt(traj, sigs)
                self._publish_recompute(fit)
        if self._recalc_ready is not None or self._recalc_done_gen != self._recalc_gen:
            if self._recalc_ready is None:       # 设置又变了 ⇒ 按最新设置重算
                self._launch_recalc()
            if self._recalc_poll_job is None:
                self._recalc_poll_job = self.after(30, self._poll_recalc)
            return
        self._settle_recalc()                    # 全部落地：把状态栏/面板收尾

    def _settle_recalc(self):
        """异步链结束后的收尾：状态栏恢复（或盖上调用方指定的文案）+ 通知监听者。"""
        if self._status_after_recalc is not None:
            self.status.configure(text=self._status_after_recalc)
            self._status_after_recalc = None
        elif self.schedule is not None and self.session.traj is not None:
            self.status.configure(text=self.session.status_text()
                                       + f"　｜　{self._entry_status()}　｜　{self._idle_status()}")
        for ref in list(self._recalc_listeners):
            fn = ref()
            if fn is None:
                self._recalc_listeners.remove(ref)
                continue
            try:
                fn()
            except tk.TclError:
                self._recalc_listeners.remove(ref)

    def wait_recalc(self, timeout: float = 120.0):
        """**等异步重算落地**（测试与面板用；返回是否等到）。

        做法：一边 `update()` 跑事件循环（轮询任务因此会执行），一边看"已经算到最新一代没有"。
        """
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            _gc_on_when_idle()                   # 主线程泵事件循环期间顺手把 GC 收回来
            if (self._recalc_thread is None or not self._recalc_thread.is_alive()) \
                    and self._recalc_done_gen == self._recalc_gen:
                return True
            try:
                self.update()
            except tk.TclError:
                return False
            time.sleep(0.005)
        return False

    def add_recalc_listener(self, fn):
        """注册"重算落地后要刷新"的回调（弱引用；面板销毁后自动摘掉）。

        用途：闲置入宿面板的表格内容依赖**新轨迹**（逐位候选的宿舍态），
        而它拿到的那份 `groups` 是改动前的 —— 落地后必须按新表重建一次。
        """
        self._recalc_listeners.append(weakref.WeakMethod(fn))

    def _publish_recompute(self, fit_slider: bool):
        """把 `session.traj` 的**最新结果**铺到界面上（同步/异步两条路共用）。"""
        _gc_on_when_idle()                       # 主线程路径
        total = self._total_hours()
        if fit_slider or self.current_t > total:
            self.current_t = Decimal("0")
        self.scale.configure(to=float(total))
        # 「全员一览」的芯片很贵（重建 57 个 ≈ 127ms），而"改一格心情"根本不影响干员集合：
        # 集合没变就只换练度角标，不重建芯片（否则每次编辑都白花 127ms）。
        names = list(self.traj.names)
        if names != self._roster_names:
            self.roster.set_operators(names)
            self._roster_names = names
        self.roster.set_badges(self._elite_badges())
        self._refresh_layout()
        self._sync_operator_box()
        self._sync_entry_label()
        self._sync_idle_label()
        self.refresh_view()
        self.status.configure(text=self.session.status_text()
                                   + f"　｜　{self._entry_status()}　｜　{self._idle_status()}")

    def _engine_world(self, t=None):
        """**引擎这一刻实际用的那份布局**（模拟副本）；拿不到就退回排班快照。

        ⚠️ 界面要显示"她在哪"就用它，别用 `schedule.shifts[i].world` —— 后者是"你导入的排班"，
        进驻事件的位置互换与**闲置入宿的换人都只改副本**，换过人之后两份会不一样
        （实测：快照说"贸易站#3 在上班"，引擎里她其实已经被换出去、心情平线 0）。
        """
        t = self.current_t if t is None else t
        traj = getattr(self, "traj", None)
        if traj is not None:
            w = traj.world_at(t)
            if w is not None:
                return w
        if self.schedule is None:
            return None
        return self.schedule.shift_at(t).world

    def _refresh_layout(self, quick: bool = False):
        """看板只在"当前时刻所在班次的布局"变化时刷新（房间结构没变则只换内容）。

        ⚠️ 判"布局有没有变"用的是**内容签名**（房间 + 住户 + 练度），所以哪怕调用方因为
        "换了个执行点 / 换了排班"而每段都调进来，只要画出来一样就不会重画控件。
        `quick=True`（拖动中）时**先只换看板**，把「全员一览」的位置标记推迟到停手后补——
        否则拖过班次边界时要额外重画 57 个芯片，会噎一下。
        """
        idx = self.schedule.index_at(self.current_t)
        world = self._engine_world()
        # 签名里带上练度：精英化一变，芯片上的 `E0/E1` 角标要跟着变
        # （否则结构"看起来没变"，看板就不会刷新内容）；也带上"谁在哪"（闲置入宿会换人）
        sig = (world is not None and id(world), tuple(
            (f.display_name, tuple((o.name, o.elite) for o in f.operators))
            for f in (world.facilities if world is not None else ())))
        if sig != self._layout_sig:
            self.board.set_layout(world, sub_title=self._shift_span_text(idx),
                                  shift_label=self.schedule.shifts[idx].label)
            self._layout_sig = sig
        # 记下"画的是哪一份世界"：`set_time` 用它判断要不要在**内部换班执行点**上重画
        self._layout_world = world
        if quick:
            self._roster_dirty = True
        else:
            self.roster.set_context(self._room_tags(world), bench=self.session.bench_names())
            self._roster_dirty = False

    def _flush_roster_context(self) -> None:
        """补上拖动期间推迟的「全员一览」位置标记。"""
        if self._roster_dirty and self.schedule is not None:
            self.roster.set_context(self._room_tags(self._engine_world()),
                                   bench=self.session.bench_names())
            self._roster_dirty = False

    def _room_tags(self, world) -> dict:
        """干员 → 这一刻所在房间的标记（`制1`/`宿3`/`中`…）；不在基建的不在表里。

        `world` 传**引擎那份副本**（`_engine_world()`）或 `Shift`：被闲置入宿换出去的人不在
        这里面，于是「全员一览」上她会显示成"休/不"，与她的平线心情对得上。
        """
        tags = {}
        if world is None:
            return tags
        facs = getattr(getattr(world, "world", world), "facilities", ())
        counts = {}
        for f in facs:
            counts[f.ftype] = counts.get(f.ftype, 0) + 1
        seen = {}
        for f in facs:
            seen[f.ftype] = seen.get(f.ftype, 0) + 1
            tag = facility_tag(f, seen[f.ftype] if counts[f.ftype] > 1 else 0)
            for op in f.operators:
                tags[op.name] = tag
        return tags

    def refresh_view(self, quick: bool = False):
        """只更新随时间变化的部分（滑块拖动走这里，O(位置数)）。

        `quick=True`：拖动中的快路径（数值 + 色条跟手，底色等停手后再补）。
        """
        _gc_on_when_idle()                       # 主线程路径（快速路径，几乎零成本）
        if self.traj is None:
            return
        moods = self.traj.moods_at(self.current_t)
        self.board.update_moods(moods, quick=quick)
        self.roster.update_moods(moods, quick=quick)
        self.chart.set_cursor(self.current_t)
        self.time_label.configure(text=self.clock_text(self.current_t))
        self._highlight_shift_button()
        # 设置窗口开着时，把"当前时刻 + 此刻的实际心情"推给「干员与心情」分区
        # （它按指定时刻显示各人心；勾了「跟随滑块」就跟着这里走）。
        self._push_view_to_settings()
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
        # ⚠️ 判据**不能只看"班次变了"**：**内部换班执行点**（长班 >12h 的 12h 整数倍）按设计
        #    **不改 `shift_index`**（见 `store.schedule.execution_offsets`），可那一刻引擎会
        #    重建布局、重跑进驻事件与闲置入宿 —— 布局可能真的换人（实测：24h 单班在 t=12
        #    把一个人换进宿舍#2）。只判班次的话，看板会一直画着换人**之前**的排布
        #    （用户报过"内部换班时 UI 没有显示对应布局"）。
        #    判法：`world_at(t)` 在**每个换班执行点**各给一份深拷贝，所以"这一刻那份世界还是不是
        #    上一次画的那一份"就是"要不要重画"的判据；同一段内是同一个对象（不会每步都重画）。
        world = self._engine_world(t)
        if changed_shift or world is not self._layout_world:
            self._refresh_layout(quick=quick)
        self.refresh_view(quick=quick)

    def nudge(self, delta: Decimal):
        self.set_time(self.current_t + delta)

    def _total_hours(self) -> Decimal:
        return self.session.total_hours

    def _shift_span_text(self, idx: int) -> str:
        """底部按钮上的一句话：`1. 00:00 – 12:00`。

        ⚠️ 「（第N天）」**只在起止不在同一天时标在结束那侧**（用户口径）：
        起点 21:00 时 `1. 21:00 – 09:00（第2天）` 是有用的（真跨天），
        而原先 `2. 09:00（第2天） – 15:00（第2天）` 两头都标只是噪音、还把按钮撑宽
        （实测宽度 486px → 798px）。跨天信息只出现一次，与图表横轴
        `theme.fmt_clock_short` 的口径一致。
        """
        s = self.schedule.shifts[idx]
        start = self.schedule.starts[idx]
        end = start + s.hours
        cycle, offset = self.schedule.cycle_hours, self.schedule.start_clock
        same_day = int((start + offset) // cycle) == int((end + offset) // cycle)
        left = theme.fmt_clock_short(start, cycle, offset)
        right = (theme.fmt_clock_short(end, cycle, offset) if same_day
                 else theme.fmt_clock(end, cycle, offset))
        return f"{left} – {right}"

    # ================================================================== 时刻显示
    def clock_text(self, t) -> str:
        """周期内时刻 → `HH:MM`（**带初始时间点**；见 `Schedule.start_clock`）。"""
        if self.schedule is None:
            return theme.fmt_clock(t)
        return theme.fmt_clock(t, self.schedule.cycle_hours, self.schedule.start_clock)

    def _push_view_to_settings(self) -> None:
        """把"滑块这一刻"告诉设置中心（只有它们真开着才做，且失败不许影响主界面）。"""
        dlg = self.settings_dlg
        if dlg is None or not dlg.winfo_exists():
            return
        push = getattr(dlg, "notify_view", None)
        if push is None:
            return
        push(self.current_t, self.moods_at_now())

    # ================================================================== 编辑
    def _editing_shift_index(self) -> int:
        return self.schedule.index_at(self.current_t)

    def on_slot_left(self, fac_index: int, slot_index: int):
        """**程序化入口**（界面已不再绑定；供新编辑器 / 测试用）：选人 / 更换 / 清空该位置。

        2026-10 起看板只做展示，位置芯片不再绑左键 —— 但这段实现是"把手动入宿
        写进布局"的现成路径（`Session.set_slots`），所以**留着**：
        `scripts/verify_modules.py` 的「布局」写入口表里有它，下一步的新编辑器要用。
        """
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
        ops = [o.name for o in facility.operators]
        while len(ops) <= slot_index:
            ops.append("")
        ops[slot_index] = picked                      # "" = 清空
        self.session.set_slots(idx, fac_index, ops)
        self._layout_sig = None
        self.recompute_async()

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

    def _ask_and_set_mood(self, who: str):
        current = self.initial_moods.get(who, self._current_start_mood(who))
        v = ask_mood(self, who, current)
        if v is None:
            return
        self.session.set_initial_mood(who, v)
        self.recompute_async()

    def _current_start_mood(self, who: str) -> Decimal:
        return self.session.imported_moods().get(who, MOOD_MAX)

    def moods_at_now(self) -> dict:
        """滑块所在时刻的实际心情（设置中心用它做「按当前时刻回填」）。"""
        return self.traj.moods_at(self.current_t) if self.traj is not None else {}

    def moods_at_abs(self, t) -> dict:
        """**任意**绝对时刻的实际心情（「干员与心情」按指定时刻显示心情列用的口子）。"""
        return self.session.moods_at(t)

    def world_at_abs(self, t):
        """**任意**绝对时刻**引擎实际用的那份布局**（模拟副本）。

        「干员与心情」的位置列用它：显示"引擎这一刻怎么排的"。拿不到（还没有轨迹）就给 `None`，
        面板会退回排班快照。
        """
        traj = getattr(self, "traj", None)
        return traj.world_at(t) if traj is not None else None

    # ------------------------------------------------------------ 闲置入宿
    def _idle_entry_list(self):
        """把界面的逐次设置转成 `[IdleToDormEntry, ...]`（口径在 `Session.idle_entry_list()`）。

        **只列改过默认的**（勾掉"参与"的人）；没人改过就返回 `None`。
        ⚠️ 三层解耦后这里只剩"参不参与" —— 手动入宿走**布局编辑**（看板 / 「干员与心情」）。
        """
        return self.session.idle_entry_list()

    def _idle_groups(self, cycles: Optional[int] = None, entries: Optional[dict] = None,
                     traj=None):
        """给设置框算**按时间排序的逐次表** → `[group, ...]`。

        `group = (标题, (周期, 班次), [(干员, 心情, 位置, 参与, 说明, []), ...], 起, 止)`。

        为什么按"**换班执行点** → 周期"展开：心情跨班、跨内部换班、跨周期连续，所以**每一次**
        "谁没满、谁在宿舍"都不一样。
        长班（> 12h）的内部换班点在标题里带 `（12h 内部换班）`，与班初各占一组，
        但**共用同一份逐人设置**（改任一组会同步影响同班其他执行点）。只列**真的有候选**的那几次。
        """
        # 候选与"引擎这一刻的安排"全在 `Session.idle_groups()`（**与程序接口同一份**）；
        # 界面只做两件"视图的事"：把心情值格式化、把组头时刻按初始时间点渲染。
        out = []
        for title, scope, rows, t0, t1 in self.session.idle_groups(cycles=cycles, entries=entries):
            shown = [(n, theme.fmt_mood(m), where, use, note, options)
                     for n, m, where, use, note, options in rows]
            out.append((f"{title}（{self.clock_text(t0)}–{self.clock_text(t1)}）",
                        scope, shown, t0, t1))
        return out

    def _idle_count(self) -> int:
        """当前设置下会有多少次"有人入宿"、共涉及多少人（按换班执行点计）。"""
        return self.session.idle_count()

    def _sync_idle_label(self):
        """工具栏右侧的当前状态：`未开启` / `已开启 · 6 次`（手动入宿不算在这里，它在布局里）。"""
        if not self.idle_to_dorm.get():
            self.idle_detail.configure(text="未开启", fg=theme.MUTED)
            return
        self.idle_detail.configure(fg=theme.TEXT)
        self.idle_detail.configure(text=f"已开启 · {self._idle_count()} 次")

    def _idle_status(self) -> str:
        """状态栏那一句口径。"""
        if not self.idle_to_dorm.get():
            return "闲置入宿：未开启"
        return (f"闲置入宿：已开启（每个换班执行点把该班没出现在任何设施、心情未满的干员"
                f"安排进宿舍：{self._idle_count()} 次；空位优先，全满则换出锁定区外"
                f"心情最接近的那位；锁定 {self.idle_protected_slots()} 个位置"
                + (f"、黑名单 {len(self.idle_blacklist())} 人" if self.idle_blacklist() else "")
                + "；手动入宿请看板 / 「干员与心情」）")

    def edit_idle_to_dorm(self):
        """（旧入口，现等价于）打开设置中心的「闲置入宿」分区。"""
        return self.open_settings("idle")

    def apply_idle_to_dorm(self, enabled: bool, entries: dict,
                           protected_slots: Optional[int] = None,
                           blacklist: Optional[Sequence[str]] = None):
        """「闲置入宿」设置落地（设置中心里**每次改动**都会调它）。

        参数：总开关 / 逐次设置 `{(周期, 班次, 干员): 参不参与}` /
        **锁定位置数** / **黑名单**（`None` = 不动）。
        返回**新的分组表**：改动会影响后面每一次的候选，所以面板要按新表重建。
        """
        self.session.idle_to_dorm = bool(enabled)
        # ⚠️ 面板给的整份状态里可能有 `True`（参与）—— 只留**改过默认的**（`False`）：
        #    `idle_globals` 的语义是"这些人不参与"，记一堆 `True` 会污染增量指纹与导出。
        self.session.idle_entries = {k: v for k, v in dict(entries).items() if not v}
        if protected_slots is not None:
            self.session.idle_protected_slots = max(0, int(protected_slots))
        if blacklist is not None:
            self.session.idle_blacklist = [str(n) for n in blacklist]
        # ⚠️ 异步重算（P5）：`recompute_async` 立即返回，返回的 `groups` 还是**改动前**那份；
        #    落地后由 `_settle_recalc` 通知监听者（设置中心注册了自己）按新表重建 ——
        #    见 `ui/settings.py` 里 `add_recalc_listener` 那处。
        self.recompute_async()                     # 看板/曲线跟着刷新
        self._sync_idle_label()
        return self._idle_groups()

    # ------------------------------------------------------------ 批量设置
    def batch_edit(self):
        """（旧入口，现等价于）打开设置中心的「干员与心情」分区。"""
        return self.open_settings("batch")

    def apply_batch(self, changes: dict, moods: dict, mood_events=None,
                    detached=None) -> None:
        """「干员与心情」落地：干员改动按班次写回布局，心情整份替换周期起点 + 收下锚点。

        干员改动按"改过哪几班"返回（面板里可切班次，未改的不会丢）；
        心情是**周期起点**（全排班共用），面板只给"与导入值不同的那些"，
        所以这里整份替换 `initial_moods`（`恢复导入值` ⇒ 空差集 ⇒ 手动心情清空）。
        `mood_events` 是**心情指定事件**（面板里的锚点，`MoodSetEvent` 列表）；
        ⚠️ 原样替换（`None` 也当空列表），否则删掉的锚点会留在引擎里继续生效。
        `detached` 是**「不在基建」名单**（既不在工作设施、也不在宿舍的人）；
        `None` = 用户没动过这一段 → 保留现有名单（别把导入带来的名单抹掉）。
        """
        n_ops = sum(1 for i in sorted(changes) for f in changes[i]
                    for n in seat_values(f) if n)
        # ⚠️ **Q15=(a)**：界面摆位**也写手动台账**（"手动入宿"不止设置页那一个入口）。
        #    走 `apply_manual_shifts` 而不是本地 `replaced_shift`：它只给"占位真的变了"的
        #    设施打标（未动的房间不会被顺手锁上），并且**不重算**（下面的异步重算统一算一次）。
        n_fac = self.session.apply_manual_shifts(changes, recompute=False)
        # 心情整份替换起点 + 收下锚点（**原样替换**：`None` 也当空列表，
        # 否则删掉的锚点会留在引擎里继续生效 —— 这条口径现在写在 Session 里）
        self.session.initial_moods = {str(k): v for k, v in dict(moods).items()}
        self.session.mood_events = list(mood_events or ())
        if detached is not None:
            self.session.set_detached(list(detached))      # 名单同步进 Schedule 与各班 world
        self._layout_sig = None
        # 整周期重算走**异步**（P5）：状态栏先写"计算中…"（`_launch_recalc` 负责），
        # 落地后 `_settle_recalc` 把下面这句盖上去。
        which = ("第 " + "、".join(str(i + 1) for i in sorted(changes)) + " 班"
                 if changes else "未改动布局")
        n_bench = len(self.session.bench_names())
        self._status_after_recalc = (
            f"设置已生效：{which}"
            + (f"（{n_fac} 间房 / {n_ops} 个位置）" if changes else "")
            + f"　｜　手动起点心情 {len(moods)} 名，其余用导入值"
            + (f"　｜　不在基建 {n_bench} 名" if n_bench else "")
            + (f"　｜　心情指定事件 {len(self.mood_events)} 条" if self.mood_events else ""))
        self.recompute_async()

    def apply_start_clock(self, clock) -> None:
        """「时间轴」→「初始时间点」落地：**只改显示口径**（周期起点是几点）。

        引擎数值一字不变（模型本来就是相对时间），所以这里只换 `Schedule.start_clock`
        + 强制重画一次标签；看板的"未变则不刷"签名也要作废（头部那段时段变了）。
        """
        if self.schedule is None:
            return
        self.session.set_start_clock(clock)
        self._layout_sig = None
        self._build_shift_buttons()        # 底部班次按钮写的是时段 → 要重建
        self._refresh_layout()
        self.refresh_view()
        now = self.clock_text(self.current_t)
        self.status.configure(text=f"周期起点＝{theme.fmt_clock(self.schedule.start_clock)}"
                                   f"（当前时刻 {now}；只改显示口径，数值不变）")
        # 逐次表组头写的是时刻、「干员与心情」的时刻框也按它显示 → 两个分区都标脏
        # （当前显示的那一页不标：它就是改动来源）
        self._invalidate_settings("idle", "batch")

    # ------------------------------------------------------------ 进驻事件（换心情）
    def _entry_candidates(self):
        """返回 `(触发者名单, 可交换对象名单)`（口径在 `Session.entry_candidates()`）。"""
        if self.schedule is None:
            return [], []
        return self.session.entry_candidates()

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
        scope = picked[2] if len(picked) > 2 else self.session.entry_scope
        restore_back = picked[3] if len(picked) > 3 else self.session.entry_restore_back
        when = normalize_entry_when(picked[4]) if len(picked) > 4 else self.session.entry_when
        per_shift = picked[5] if len(picked) > 5 else self.session.entry_per_shift
        # 设置**写在 Session 上**（界面只是它的视图；程序接口改的是同一批字段）
        self.session.entry_events = bool(enabled)
        self.session.entry_swap_with = swap_with
        self.session.entry_scope = scope
        self.session.entry_restore_back = restore_back
        self.session.entry_when = when or "full"   # 界面口径：缺省＝"没满就不换"
        self.session.entry_per_shift = list(per_shift or [])
        self._sync_entry_label()
        # 异步重算（P5）：状态栏文案留到**落地之后**再写（否则会被"计算中…"/通用状态盖掉）
        if not self._entry_candidates()[0]:
            self._status_after_recalc = ("本排班里没有能触发进驻事件的干员（如菲亚梅塔），"
                                         "这个开关暂时不会有任何效果")
        else:
            self._status_after_recalc = self._entry_summary()   # 配置摘要
        self.recompute_async()

    def _layout_issues(self) -> str:
        """当前班次布局的自检问题（上游约束：单类型上限 / 建造位总量 9 / 人数 ≤ 等级容量）。"""
        v = self.session.validate()
        return "；".join(i.message for i in v.issues)

    def _facilities_of(self, idx: int):
        return self.session.facilities_of(idx)

    # ------------------------------------------------------------ 练度（精英化）
    def _elite_badges(self) -> dict:
        """`{干员: "E1"}`——取该干员在各班次里**最低**的精英化（只有非 E2 才进表）。

        为什么取最低：同一个人在不同班次可以有不同练度（少见但合法），
        用最低那档才不会漏掉"某一班他的技能其实没生效"。
        """
        badges = self.session.elite_badges()
        if badges:
            return badges
        # Session 只记非满练的角标；这里补上"当前布局里出现过的满练干员"由芯片自己处理，
        # 所以直接返回即可（`elite_badge()` 仍被 chip 用）。
        return {}

    def _operator_obj(self, name: str):
        """找这个干员的 `Operator`（优先当前班次，其次第一个有他的班次）。"""
        return self.session.operator_obj(name, self._editing_shift_index())

    def _elite_text(self, name: str) -> str:
        """练度摘要（对点查询用）：`练度 E1 · 已解锁 6 条心情技能；因未满练少 1 条（「手工艺品·β」）`。"""
        return self.session.elite_text(name)

    def _apply_facilities(self, idx: int, facs):
        """改完某个班次的布局 → 重建 Schedule → 重算（转发到 Session）。"""
        self.session.replace_facilities(idx, facs)
        self._layout_sig = None
        self.recompute_async()

    def edit_shifts(self):
        """（旧入口，现等价于）打开设置中心的「时间轴」分区。"""
        return self.open_settings("timeline")

    def apply_shift_hours(self, hours) -> None:
        """「时间轴」落地：改各班时长（面板已保证"各班长之和 == 周期"）。

        ⚠️ **时长没变就直接返回**：本函数会 `recompute(fit_slider=True)`，而后者会
        **把当前时刻重置为 0:00** 并重算一整轮。`TimelinePanel` 构建时也会校验一次并回调，
        不做这个判据的话，"打开时间轴页"就会把主界面时刻冲成 00:00（实测过），
        「跟随滑块」看起来就像在乱跳。
        """
        if self.schedule is None or not hours:
            return
        now = [to_decimal(h) for h in hours]
        if now == [s.hours for s in self.schedule.shifts]:
            return
        self.session.set_timeline(hours=hours)      # 周期自动 = 各班长之和
        self._build_shift_buttons()
        self.recompute_async(fit_slider=True)
        # 班次时长/数量变了 → 别的分区里的班次下拉、逐次表都得重建（设置窗口自己不用）
        self._invalidate_settings("batch", "entry", "idle")

    def _invalidate_settings(self, *pages: str) -> None:
        """把设置中心里这些分区标脏（窗口没开就什么都不用做）。"""
        dlg = self.settings_dlg
        if dlg is not None and dlg.winfo_exists():
            dlg.invalidate(*pages)

    def _on_cycles(self):
        # ⚠️ 走 `set_cycles`（夹到 1~`MAX_CYCLES`），夹过了就**回写下拉**——
        #    别让"工具栏显示 9、会话里其实是 7"两处不一致。
        self.session.set_cycles(self.cycles_var.get())
        self.cycles_var.set(str(self.session.cycles))
        # 周期数变了 → 底部「周期」选择器要跟着出现/消失（`_build_shift_buttons` 按它决定）
        self._build_shift_buttons()
        self.recompute_async(fit_slider=True)
        # 逐次表按周期展开、干员与心情的「周期」下拉也有 1~周期数 项 → 两个分区都得重建
        self._invalidate_settings("idle", "batch")

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
            f"最低 {theme.fmt_mood(lo)} @ {self.clock_text(lo_t)}"
            f"　最高 {theme.fmt_mood(hi)} @ {self.clock_text(hi_t)}",
            f"红脸 {len(spans)} 段，合计 {theme.fmt_hours(total_red)}" if spans else "红脸 无",
        ]
        per = traj.min_mood_at_each_shift(name)
        lines.append("各班最低：" + "　".join(f"{l} {theme.fmt_mood(v)}" for l, v in per))
        if name in set(self.session.bench_names()):
            # 「不在基建」的人：整条曲线是平线，不写清楚容易被当成"技能全失效"的 bug
            lines.append("⚠ 不在基建（既不在工作设施、也不在宿舍）：心情整段不变、不参与技能计数")
        elite = self._elite_text(name)
        if elite:
            lines.append(elite)
        return "\n".join(lines)

    def _facility_at(self, t: Decimal) -> str:
        if self.schedule is None:
            return ""
        world = self._engine_world(t)          # 引擎那份（被闲置入宿换出去的人 → 未排班）
        fac = world.facility_of(self.curve_operator) if world is not None else None
        return fac.display_name if fac else "未排班"

    # ================================================================== 班次条
    def _build_shift_buttons(self):
        """底部班次条＝**纯切换器**，只写「序号 + 时段」（`1. 00:00 – 12:00`）。

        ⚠️ 这里**不再写班次名**：班次名（`Shift 1 · 12h`）已经在**看板头部**写着，
        上下各写一份就是"同一条班次显示两遍"；而且 MAA 的班次名里本来就带 `12h`，
        再补一个 `（12h）` 会变成 `Shift 1 · 12h（12h）`（曾经的重复显示 bug）。
        按钮上给时段更有用：一眼看出"这一班从几点到几点"。

        **周期选择器**（用户口径：`周期数 > 1` 时要能选到**不同周期里的时段**）：
        前面多一段 `周期 [1][2][3]`，班次按钮跳的是"**你当前所在周期**的那一班"，
        点周期 `c` 则跳到"第 `c` 周期的**同一班**"。周期数 = 1 时这一段自动不出现
        （没有"哪一周期"这个问题）。

        ⚠️ **重建前把整行清空**（周期段 + 标签 + 按钮一起）：早先只销毁按钮、
        「班次」标签每调一次新建一个 ⇒ 导入一次/改一次时间点就多攒一个，
        底部显示成「班次 班次 班次 1. …」（实测复现，2026-09 修）。
        """
        for w in self.shift_bar.winfo_children():
            w.destroy()
        self.shift_buttons.clear()
        self.cycle_buttons.clear()
        if self.schedule is None:
            return
        cycles = max(1, int(self.session.cycles))
        if cycles > 1:
            tk.Label(self.shift_bar, text="周期", bg=theme.BG, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(0, 4))
            for c in range(1, cycles + 1):
                b = ttk.Button(self.shift_bar, text=str(c), width=3,
                               command=lambda k=c: self._goto_cycle(k))
                b.pack(side="left", padx=(0, 4))
                self.cycle_buttons.append(b)
        tk.Label(self.shift_bar, text="班次", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(0, 6))
        for i, _s in enumerate(self.schedule.shifts):
            b = ttk.Button(self.shift_bar,
                           text=f"{i + 1}. {self._shift_span_text(i)}",
                           command=lambda k=i: self._goto_shift(k))
            b.pack(side="left", padx=(0, 4))
            self.shift_buttons.append(b)
        self._space_only_plays(self.shift_bar)     # 新建的班次按钮也要"空格＝播放/暂停"

    def _goto_cycle(self, cycle: int) -> None:
        """跳到**第 `cycle` 周期的同一班**（"周期数 > 1 时要能选到各周期里的时段"）。"""
        if self.schedule is None:
            return
        idx = self.schedule.index_at(self.current_t)
        self.set_time(self.schedule.cycle_hours * (int(cycle) - 1) + self.schedule.starts[idx])

    def _goto_shift(self, index: int) -> None:
        """跳到**当前所在周期**里的第 `index` 班（周期由这一刻的滑块位置决定，不另存状态）。"""
        if self.schedule is None:
            return
        cycle, _within = self.session.cycle_of(self.current_t)
        self.set_time(self.schedule.cycle_hours * (cycle - 1) + self.schedule.starts[index])

    def _highlight_shift_button(self):
        idx = self.schedule.index_at(self.current_t) if self.schedule else -1
        cycle = self.session.cycle_of(self.current_t)[0] if self.schedule else -1
        for i, b in enumerate(self.shift_buttons):
            b.configure(style="Accent.TButton" if i == idx else "TButton")
        for i, b in enumerate(self.cycle_buttons):
            b.configure(style="Accent.TButton" if i + 1 == cycle else "TButton")

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
        """退出前取消所有挂起的 `after` 回调，并关掉设置中心。

        否则窗口销毁后回调仍会触发，Tk 会打印
        `invalid command name "..._autoload_sample"`（测试里尤其吵）。
        """
        if self.settings_dlg is not None:
            try:
                if self.settings_dlg.winfo_exists():
                    self.settings_dlg.destroy()
            except tk.TclError:
                pass
            self.settings_dlg = None
        for attr in ("_refresh_job", "_settle_job", "_play_job", "_autoload_job",
                     "_recalc_poll_job"):
            job = getattr(self, attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
            setattr(self, attr, None)
        # 在算的那次异步重算：把代数推高 ⇒ 结果回来时会被判为过期、不碰已销毁的控件
        self._recalc_gen += 1
        self._recalc_thread = None
        # ⚠️ **还要把工作线程 join 干净**（有界等待）：`_work()` 里虽然已不碰 `self`，但只要
        #    还有线程活着，它的分配就可能触发 GC、在**非主线程**里回收已销毁窗口的 Tk 对象
        #    ⇒ `Variable.__del__` 报 "main thread is not in main loop"，严重时 Tcl 的 async
        #    handler 被错误线程删掉，直接 `Tcl_AsyncDelete` **崩掉进程**（实测：全量测试合并跑
        #    三次崩两次，把 Tk 用例单独跑就稳；真实场景＝"重算过程中关窗口"）。
        #    窗口正在销毁，这里等一会儿是值得的；超时也不阻塞太久（线程是 daemon）。
        threads, self._recalc_threads = self._recalc_threads, []
        for thread in threads:
            if not thread.is_alive():
                continue
            try:
                thread.join(timeout=RECALC_JOIN_TIMEOUT)
            except Exception:                  # noqa: BLE001 —— 收尾阶段不许再抛
                pass
        # 线程都收工了才恢复 GC —— 恢复动作本身必须在**主线程**（见模块头）。
        _gc_on_when_idle()
        # 「全员一览」的 `after_idle` 重建任务也取消掉：窗口没了它还挂着，Tk 会打印
        # `invalid command name "..._deferred_rebuild"`（回调里的 `winfo_exists()` 挡不住 ——
        # 命令名在 Tcl 里已经被删了，进回调之前就报错）。
        try:
            roster = getattr(self, "roster", None)
            if roster is not None:
                roster.cancel_pending()
        except Exception:                      # noqa: BLE001
            pass
        super().destroy()


def main(argv=None) -> int:
    """图形界面入口（`python -m ui` / 打包后的 exe）。

    `--smoke`：**建好窗口、跑几轮事件循环就退出**（退出码 0 ＝ 一切正常）。
    它是给"打包成 exe 之后自检"用的 —— `--windowed` 的 exe 没有控制台、
    看不到 stdout，只能靠退出码判断"界面到底建没建起来"：
        dist/RhodesMoodSOC/RhodesMoodSOC.exe --smoke
    顺带也能当"这台机器能不能开界面"的探针（无图形环境会抛异常 ⇒ 非 0 退出）。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    app = MoodSocApp()
    if "--smoke" in args:
        app.update_idletasks()
        app.update()                      # 先把控件真正布出来（这一步最容易出问题）
        app.after(200, app.destroy)       # 再让事件循环自转一小会儿
        app.mainloop()
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
