"""ui/settings.py —— 「设置」中心：**一个窗口管完所有设置**（左侧分区导航 + 右侧内容）。

## 为什么合并

合并前工具栏上并列 **8 个控件**（导入排班… / 班次设置… / 重置心情 / 批量设置… / 周期数 /
换心情设置+状态 / 闲置入宿设置+状态 / ▶播放·速度·回到起点）：

- **重复**：`重置心情`（回到导入值）与批量表里的「恢复导入值」是同一件事；
  「周期数」与「班次设置」说的都是时间轴；
- **只增加步骤**：每个设置都是"点开对话框 → 改 → 点应用 → 关窗"四步，
  而其中一半（闲置入宿）本来就是改完即时生效的；
- **入口太散**：想改"换谁"要先在工具栏上找到那颗按钮，还得先知道它藏在哪。

现在工具栏只剩 4 组：`导入排班…` │ `设置…` + 一行状态摘要 │ `▶播放 · 速度` │ `回到起点`。

## 分区（左导航）

| 分区 | 内容 | 由谁实现 |
|---|---|---|
| **时间轴** | 周期时长 / 各班次时长 / **周期数**（1~3） | `dialogs.TimelinePanel` |
| **干员与心情** | 房间等级 + 干员表 + 练度 + 心情（含"恢复导入值"＝原来的「重置心情」） | `batch.BatchPanel` |
| **换心情** | 进驻事件（M15a）：开关 + 每班一行表 | `dialogs.EntryEventPanel` |
| **闲置入宿** | 未满的闲置干员进宿舍：开关 + 逐次表 | `dialogs.IdleToDormPanel` |

## 切换为什么"丝滑"（三个要点）

1. **内容区尺寸固定**（`PAGE_H`）：四个分区共用同一块尺寸固定的内容区，
   切换时**窗口不缩放、不跳**——短的分区就在下面留白，长的分区在自己的表格里滚动。
   一条测试盯着"每个分区的自然高度都不超过它"（`test_每个分区都装得进内容区`）。
2. **分区只建一次**（`self._pages` 缓存）：切换只做 `pack_forget` / `pack`，
   **不销毁重建**（「干员与心情」那页有 50 行控件，重建要 ~290ms，就是"切页一顿"的来源）。
3. **改坏了才重建**（`invalidate`）：时间轴一改，班次数量与时长就变了，别的分区里的
   班次下拉/逐次表必须拿到新排班——那时把这些分区**标脏**，下次进入时重建。
   `ui/app.py` 在 `apply_shift_hours` / `on_cycles_changed` 里调用 `invalidate`。

⚠️ **改动立即生效**（心情输入等走 250ms 防抖），窗口底部**只有「关闭」**——
没有"应用 / 取消"两步；关掉即接受。破坏性动作（清空本班次 / 恢复导入值）保留二次确认。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional

from . import theme
from .batch import BatchPanel
from .dialogs import EntryEventPanel, IdleToDormPanel, TimelinePanel

# (分区键, 标题, 一句话说明)
PAGES = (
    ("timeline", "时间轴", "周期多长、分几班、每班几小时、跑几个周期"),
    ("batch", "干员与心情", "房间等级 · 每个位置放谁 · 练度(E0~E2) · 周期起点心情"),
    ("entry", "换心情", "进驻事件 M15a：进驻那一刻与谁互换心情"),
    ("idle", "闲置入宿", "把没上班、没在宿舍、心情未满的干员安排进宿舍"),
)

# 内容区固定高度：四个分区共用（切换时窗口不跳）。
# 取"最高的那个分区"（干员与心情：等级区 + 心情区 + 干员区 + 表格 380 + 提示行）≈ 740。
PAGE_H = 740
# 「干员与心情」那张表的可视高度：设置中心里矮一点，四个分区就都能装进 PAGE_H
BATCH_TABLE_H = 380
# 分区标题/说明用同一套栅格：标签列宽、控件间距都从这里取，免得各页自己凑
LABEL_W = 12
NAV_W = 14


def setting_row(parent, label: str, hint: str = ""):
    """统一的"一行设置"：`标签(右对齐) | 控件位 | 说明`。

    返回 `(行 Frame, 控件位 Frame)`：调用方把控件 pack 进第二个 Frame，
    说明文字自动排在后面——四个分区都用它，排版就不会一页一个样。
    """
    row = tk.Frame(parent, bg=theme.BG)
    tk.Label(row, text=label, bg=theme.BG, fg=theme.TEXT, width=LABEL_W, anchor="e",
             font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(side="left")
    slot = tk.Frame(row, bg=theme.BG)
    slot.pack(side="left", padx=(8, 0))
    if hint:
        tk.Label(row, text=hint, bg=theme.BG, fg=theme.MUTED, anchor="w",
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(8, 0))
    return row, slot


class SettingsDialog(tk.Toplevel):
    """设置中心：左侧导航 + 右侧分区内容。所有改动**立即生效**，底部只有「关闭」。

    `app` 提供数据与落地回调（见 `ui.app` 的 `settings_*` / `apply_*` 方法）：
    这样"设置窗口"里没有一行引擎逻辑，它只负责把面板与 app 接起来。
    """

    def __init__(self, parent, app, page: Optional[str] = None):
        super().__init__(parent, bg=theme.BG)
        self.title("设置")
        self.resizable(True, True)
        self.app = app
        self.page: str = ""
        self.panel = None
        self._nav: Dict[str, tk.Frame] = {}
        self._nav_btn: Dict[str, tk.Label] = {}
        self._nav_bar: Dict[str, tk.Frame] = {}
        self._pages: Dict[str, tk.Frame] = {}      # 分页缓存（只建一次）
        self._dirty: set = set()                   # 需要重建的分区
        self._placed: set = set()                  # 已经 place 过的分区（之后只需 tkraise）

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD, 0))

        # —— 左侧导航 ——
        left = tk.Frame(body, bg=theme.PANEL, highlightthickness=1,
                        highlightbackground=theme.BORDER)
        left.pack(side="left", fill="y")
        tk.Label(left, text="设置", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                 font=(theme.FONT_FAMILY, theme.FS_TITLE)).pack(fill="x", padx=theme.GAP,
                                                                 pady=(theme.GAP, 4))
        for key, title, _desc in PAGES:
            item = tk.Frame(left, bg=theme.PANEL)
            item.pack(fill="x", pady=1)
            bar = tk.Frame(item, bg=theme.PANEL, width=3)      # 当前项左侧的强调色竖条
            bar.pack(side="left", fill="y")
            btn = tk.Label(item, text=title, bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                           width=NAV_W, cursor="hand2",
                           font=(theme.FONT_FAMILY, theme.FS_BODY))
            btn.pack(side="left", fill="x", padx=(7, 6), pady=3)
            for w in (item, btn):
                w.bind("<Button-1>", lambda _e, k=key: self.open_page(k))
            self._nav[key] = item
            self._nav_btn[key] = btn
            self._nav_bar[key] = bar

        # —— 右侧内容（标题 + 说明 + 固定尺寸的内容区）——
        right = tk.Frame(body, bg=theme.BG)
        right.pack(side="left", fill="both", expand=True, padx=(theme.GAP, 0))
        self.head = tk.Label(right, text="", bg=theme.BG, fg=theme.TEXT, anchor="w",
                             font=(theme.FONT_FAMILY, theme.FS_TITLE))
        self.head.pack(fill="x")
        self.note = tk.Label(right, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                             font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.note.pack(fill="x", pady=(0, theme.GAP))
        # `pack_propagate(False)`：内容区高度由 PAGE_H 说了算（子控件不会把它撑大）
        self.host = tk.Frame(right, bg=theme.BG, height=PAGE_H, width=760)
        self.host.pack(fill="both", expand=True)
        self.host.pack_propagate(False)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, theme.PAD))
        tk.Label(btns, text="改动立即生效；关掉本窗口即保留当前设置。", bg=theme.BG,
                 fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        ttk.Button(btns, text="关闭", style="Accent.TButton", command=self.destroy).pack(
            side="right")
        self.bind("<Escape>", lambda _e: self.destroy())
        # Tab 只在本分区里转（隐藏页仍然存在，不拦一下焦点会跑进看不见的控件）
        self.bind("<Tab>", lambda _e: self._cycle_focus(False))
        self.bind("<Shift-Tab>", lambda _e: self._cycle_focus(True))
        self.bind("<ISO_Left_Tab>", lambda _e: self._cycle_focus(True))

        self._center(parent)
        self.open_page(page or PAGES[0][0], first=True)
        self.transient(parent)

    # ------------------------------------------------------------------ 分区
    def open_page(self, key: str, first: bool = False) -> None:
        """切到某个分区：**只把缓存好的那一页提到最上面**（不重建、不改窗口大小）。

        为什么用 `place` + `tkraise` 而不是 `pack_forget` + `pack`：
        「干员与心情」那页有 400+ 个控件，**重新 pack 一次要 ~100ms**（几何管理要重排整页），
        而 `tkraise` 只做"显示/隐藏"，实测切换从 ~100ms 降到 ~17ms。
        代价是隐藏页的控件仍然存在（Tab 焦点可能跑进去）——所以配了焦点环
        （`_cycle_focus` + `_focus_active`），Tab 只在**当前分区**里转。
        """
        if key not in [k for k, _t, _d in PAGES]:
            raise ValueError(f"没有这个分区：{key!r}（可选 {[k for k, _t, _d in PAGES]}）")
        if key == self.page and not first:
            return
        self.page = key
        self.panel = self._page(key)
        if key not in self._placed:                # 只 place 一次，之后切页就只是 tkraise
            self.panel.place(x=0, y=0, relwidth=1, relheight=1)
            self._placed.add(key)
        self.panel.tkraise()
        self._sync_nav()
        title = next(t for k, t, _d in PAGES if k == key)
        desc = next(d for k, _t, d in PAGES if k == key)
        self.head.configure(text=title)
        self.note.configure(text=desc + "　（改动立即生效）")
        self._focus_active()

    # —— 焦点环：Tab 只在本分区里转，别跑到看不见的那几页上去 ——
    def _focusables(self):
        """当前分区里参与 Tab 的控件（按层级顺序）。"""
        out, stack = [], [self.panel]
        while stack:
            w = stack.pop(0)
            for child in w.winfo_children():
                cls = child.winfo_class()
                if cls in ("TEntry", "Entry", "TCombobox", "Combobox", "TButton", "Button",
                           "Checkbutton", "TCheckbutton", "TScale", "Scale"):
                    try:
                        if str(child.cget("state")) != "disabled":
                            out.append(child)
                    except tk.TclError:
                        pass
                stack.append(child)
        return out

    def _focus_active(self) -> None:
        """如果焦点落在看不见的分区里，把它收回本窗口（Tab 从当前分区重新开始）。"""
        cur = self.focus_get()
        if cur is None or cur is self:
            return
        w = cur
        while w is not None:
            if w is self.panel:
                return
            w = getattr(w, "master", None)
        self.focus_set()

    def _cycle_focus(self, back: bool = False):
        """Tab / Shift+Tab：在**当前分区**内循环。"""
        items = self._focusables()
        if not items:
            return "break"
        cur = self.focus_get()
        try:
            i = items.index(cur)
        except ValueError:
            i = -1 if not back else 0
        nxt = items[(i + (-1 if back else 1)) % len(items)]
        nxt.focus_set()
        return "break"

    def invalidate(self, *keys: str) -> None:
        """把这些分区**标脏**（下次进入时重建）。

        什么时候要标脏：**改了会影响别的分区内容的东西**——时间轴一改，班次数量与时长变了，
        「换心情」的每班表、「闲置入宿」的逐次表、以及「干员与心情」的班次下拉都得重建。
        当前正在显示的那一页不用标（它就是改动来源）。
        """
        for k in keys or ():
            if k == self.page:
                continue
            self._dirty.add(k)

    def _page(self, key: str) -> tk.Frame:
        """取分区控件（缓存命中就直接用；标脏过就先销毁重建）。"""
        if key in self._dirty:
            old = self._pages.pop(key, None)
            if old is not None:
                old.destroy()
            self._dirty.discard(key)
            self._placed.discard(key)              # 重建过 → 需要重新 place
        if key not in self._pages:
            page = getattr(self, f"_build_{key}")()
            self._pages[key] = page
            self._assert_fits(page)
        return self._pages[key]

    def _assert_fits(self, page: tk.Frame) -> None:
        """自然高度不允许超过固定内容区（超了就是排版得再收一收）。"""
        self.update_idletasks()
        need = page.winfo_reqheight()
        if need > PAGE_H:
            # 不崩、不截断成谜：写进窗口标题旁边让人看见（测试里会直接断言）
            self.note.configure(text=self.note.cget("text") +
                                f"　⚠ 本分区需要 {need}px > 内容区 {PAGE_H}px")

    def _sync_nav(self) -> None:
        for k, btn in self._nav_btn.items():
            cur = (k == self.page)
            btn.configure(bg=(theme.PANEL_ALT if cur else theme.PANEL),
                          fg=(theme.ACCENT if cur else theme.TEXT),
                          font=(theme.FONT_FAMILY, theme.FS_BODY, "bold" if cur else "normal"))
            self._nav[k].configure(bg=(theme.PANEL_ALT if cur else theme.PANEL))
            self._nav_bar[k].configure(bg=(theme.ACCENT if cur else theme.PANEL))

    # —— 四个分区（每个都只是"把面板接上一个回调"）——
    def _build_timeline(self) -> tk.Frame:
        app = self.app
        box = tk.Frame(self.host, bg=theme.BG)
        self.timeline = TimelinePanel(box, app.schedule.shift_labels(),
                                      [s.hours for s in app.schedule.shifts],
                                      app.schedule.cycle_hours, on_change=app.apply_shift_hours)
        self.timeline.pack(fill="x")
        row, slot = setting_row(box, "周期数", "（连着跑几个周期，用来看这套排班能不能永动）")
        row.pack(fill="x", pady=(theme.GAP, 0))
        cb = ttk.Combobox(slot, textvariable=app.cycles_var, width=3, state="readonly",
                          values=("1", "2", "3"))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _e: app.on_cycles_changed())
        return box

    def _build_batch(self) -> tk.Frame:
        app = self.app
        return BatchPanel(self.host, app.schedule, shift_index=app.editing_shift_index(),
                          initial_moods=app.initial_moods,
                          imported_moods=app.imported_moods(),
                          moods_now=app.moods_now(), current_t=app.current_t,
                          on_change=app.apply_batch,
                          pool=app.operator_pool,     # 导入 v4 蓝图时的干员池
                          table_height=BATCH_TABLE_H)

    def _build_entry(self) -> tk.Frame:
        app = self.app
        holders, mates = app.entry_candidates()
        return EntryEventPanel(self.host, app.entry_events.get(), app.entry_swap_with, mates,
                               holders, scope=app.entry_scope,
                               restore_back=app.entry_restore_back, when=app.entry_when,
                               shift_labels=app.schedule.shift_labels(),
                               per_shift=app.entry_per_shift,
                               on_change=app.apply_entry_event)

    def _build_idle(self) -> tk.Frame:
        app = self.app
        return IdleToDormPanel(self.host, app.idle_to_dorm.get(), app.idle_groups(),
                              on_change=app.apply_idle_to_dorm)

    # ------------------------------------------------------------------ 杂务
    def _center(self, parent) -> None:
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), 980)
        h = min(max(self.winfo_reqheight(), 620), 900)
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        self.geometry(f"+{px + max((pw - w) // 2, 0)}+{py + max((ph - h) // 4, 0)}")


def ask_settings(parent, app, page: Optional[str] = None) -> Optional["SettingsDialog"]:
    """打开设置中心（非模态：它跟着主窗口走，不挡看板刷新）。"""
    return SettingsDialog(parent, app, page=page)
