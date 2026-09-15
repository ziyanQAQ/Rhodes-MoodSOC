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

⚠️ **改动立即生效**（心情输入等走 250ms 防抖），窗口底部**只有「关闭」**——
没有"应用 / 取消"两步；关掉即接受。破坏性动作（清空本班次 / 恢复导入值）保留二次确认。

⚠️ 每个分区**每次进入时重建**（`open_page`）：时间轴一改，班次数量与时长就变了，
别的分区里那些控件（班次下拉、逐次表）必须拿到新的排班，重建是最省心也最不容易漏的做法。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

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
        self._nav: dict = {}

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.PAD, 0))

        # —— 左侧导航 ——
        left = tk.Frame(body, bg=theme.PANEL, highlightthickness=1,
                        highlightbackground=theme.BORDER)
        left.pack(side="left", fill="y")
        tk.Label(left, text="设置", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                 font=(theme.FONT_FAMILY, theme.FS_TITLE)).pack(fill="x", padx=theme.GAP,
                                                                 pady=(theme.GAP, 2))
        for key, title, _desc in PAGES:
            btn = tk.Button(left, text=title, anchor="w", relief="flat", bd=0,
                            bg=theme.PANEL, fg=theme.TEXT, activebackground=theme.PANEL_ALT,
                            highlightthickness=0, cursor="hand2", width=12,
                            font=(theme.FONT_FAMILY, theme.FS_BODY),
                            command=lambda k=key: self.open_page(k))
            btn.pack(fill="x", padx=4, pady=1)
            self._nav[key] = btn

        # —— 右侧内容 ——
        right = tk.Frame(body, bg=theme.BG)
        right.pack(side="left", fill="both", expand=True, padx=(theme.GAP, 0))
        self.head = tk.Label(right, text="", bg=theme.BG, fg=theme.TEXT, anchor="w",
                             font=(theme.FONT_FAMILY, theme.FS_BODY))
        self.head.pack(fill="x")
        self.note = tk.Label(right, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                             font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.note.pack(fill="x", pady=(0, 2))
        self.host = tk.Frame(right, bg=theme.BG)
        self.host.pack(fill="both", expand=True)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, theme.PAD))
        tk.Label(btns, text="改动立即生效；关掉本窗口即保留当前设置。", bg=theme.BG,
                 fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        ttk.Button(btns, text="关闭", style="Accent.TButton", command=self.destroy).pack(
            side="right")
        self.bind("<Escape>", lambda _e: self.destroy())

        self._center(parent)
        self.open_page(page or PAGES[0][0], first=True)
        self.transient(parent)

    # ------------------------------------------------------------------ 分区
    def open_page(self, key: str, first: bool = False) -> None:
        """切到某个分区（每次重建内容：时间轴一改，别的分区的排班就是旧的）。"""
        if key == self.page and not first:
            return
        for w in self.host.winfo_children():
            w.destroy()
        self.panel = None
        self.page = key
        for k, btn in self._nav.items():
            btn.configure(bg=(theme.PANEL_ALT if k == key else theme.PANEL),
                          fg=(theme.ACCENT if k == key else theme.TEXT))
        desc = next(d for k, _t, d in PAGES if k == key)
        self.head.configure(text=next(t for k, t, _d in PAGES if k == key))
        self.note.configure(text=desc + "　（改动立即生效）")
        self.panel = getattr(self, f"_page_{key}")()
        self.panel.pack(fill="both", expand=True)
        self.geometry("")                      # 让窗口按新分区重新算大小
        self.update_idletasks()

    # —— 四个分区（每个都只是"把面板接上一个回调"）——
    def _page_timeline(self) -> tk.Frame:
        app = self.app
        box = tk.Frame(self.host, bg=theme.BG)
        self.timeline = TimelinePanel(box, app.schedule.shift_labels(),
                                      [s.hours for s in app.schedule.shifts],
                                      app.schedule.cycle_hours, on_change=app.apply_shift_hours)
        self.timeline.pack(fill="x")
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x", pady=(theme.GAP, 0))
        tk.Label(row, text="周期数", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        cb = ttk.Combobox(row, textvariable=app.cycles_var, width=3, state="readonly",
                          values=("1", "2", "3"))
        cb.pack(side="left", padx=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: app.on_cycles_changed())
        tk.Label(row, text="（连着跑几个周期，用来看这套排班能不能永动）", bg=theme.BG,
                 fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left",
                                                                                padx=(8, 0))
        return box

    def _page_batch(self) -> tk.Frame:
        app = self.app
        panel = BatchPanel(self.host, app.schedule, shift_index=app.editing_shift_index(),
                           initial_moods=app.initial_moods,
                           imported_moods=app.imported_moods(),
                           moods_now=app.moods_now(), current_t=app.current_t,
                           on_change=app.apply_batch)
        return panel

    def _page_entry(self) -> tk.Frame:
        app = self.app
        holders, mates = app.entry_candidates()
        return EntryEventPanel(self.host, app.entry_events.get(), app.entry_swap_with, mates,
                              holders, scope=app.entry_scope,
                              restore_back=app.entry_restore_back, when=app.entry_when,
                              shift_labels=app.schedule.shift_labels(),
                              per_shift=app.entry_per_shift,
                              on_change=app.apply_entry_event)

    def _page_idle(self) -> tk.Frame:
        app = self.app
        return IdleToDormPanel(self.host, app.idle_to_dorm.get(), app.idle_groups(),
                              on_change=app.apply_idle_to_dorm)

    # ------------------------------------------------------------------ 杂务
    def _center(self, parent) -> None:
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), 860)
        h = min(max(self.winfo_reqheight(), 560), 860)
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        self.geometry(f"+{px + max((pw - w) // 2, 0)}+{py + max((ph - h) // 4, 0)}")


def ask_settings(parent, app, page: Optional[str] = None) -> Optional["SettingsDialog"]:
    """打开设置中心（非模态：它跟着主窗口走，不挡看板刷新）。"""
    return SettingsDialog(parent, app, page=page)
