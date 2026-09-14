"""ui/dialogs.py —— 三个小对话框：选人 / 设心情 / 班次时长设置。

都做成"模态 + 返回结果"的简单函数（`ask_*` → 值或 None），调用方（`app.py`）不必关心细节。
键盘优先：选人框回车即确认、Esc 取消；设心情框支持 `0/6/12/18/24` 快捷按钮。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import ttk
from typing import List, Optional, Sequence

from . import theme


def _center(win: tk.Toplevel, parent: tk.Misc) -> None:
    win.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    w, h = win.winfo_width(), win.winfo_height()
    win.geometry(f"+{px + max((pw - w) // 2, 0)}+{py + max((ph - h) // 3, 0)}")


def _modal(win: tk.Toplevel, parent: tk.Misc) -> None:
    win.transient(parent)
    win.grab_set()
    _center(win, parent)
    win.focus_set()


class OperatorPicker(tk.Toplevel):
    """选人：搜索框 + 列表（支持键盘上下/回车，双击确认）。"""

    def __init__(self, parent, names: Sequence[str], current: str = "", title: str = "选择干员"):
        super().__init__(parent, bg=theme.BG)
        self.title(title)
        self.resizable(True, True)
        self.result: Optional[str] = None
        self._names = list(names)

        tk.Label(self, text="干员名（可输入关键字过滤）", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.PAD,
                                                                pady=(theme.PAD, 2))
        self.entry = ttk.Entry(self, font=(theme.FONT_FAMILY, theme.FS_BODY))
        self.entry.pack(fill="x", padx=theme.PAD)
        self.entry.insert(0, current)

        wrap = tk.Frame(self, bg=theme.BG)
        wrap.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.GAP)
        self.listbox = tk.Listbox(wrap, font=(theme.FONT_FAMILY, theme.FS_BODY), activestyle="none",
                                  highlightthickness=1, highlightbackground=theme.BORDER,
                                  selectbackground=theme.ACCENT, selectforeground="#ffffff",
                                  borderwidth=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        ttk.Button(btns, text="清空该位置", command=self._clear).pack(side="left")
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="确定", style="Accent.TButton", command=self._ok).pack(side="right",
                                                                                    padx=(0, 6))

        self.entry.bind("<KeyRelease>", lambda _e: self._refill())
        self.entry.bind("<Return>", lambda _e: self._ok())
        self.entry.bind("<Down>", lambda _e: (self.listbox.focus_set(),
                                              self.listbox.selection_set(0)))
        self.listbox.bind("<Double-Button-1>", lambda _e: self._ok())
        self.listbox.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self._refill()
        _modal(self, parent)

    def _refill(self) -> None:
        key = self.entry.get().strip()
        self.listbox.delete(0, "end")
        for n in self._names:
            if not key or key in n:
                self.listbox.insert("end", n)
        if self.listbox.size() and not self.listbox.curselection():
            self.listbox.selection_set(0)

    def _ok(self) -> None:
        sel = self.listbox.curselection()
        if sel:
            self.result = self.listbox.get(sel[0])
        else:
            text = self.entry.get().strip()
            self.result = text or None
        self.destroy()

    def _clear(self) -> None:
        self.result = ""          # "" = 明确要求清空该位置（None = 取消，调用方据此区分）
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def ask_operator(parent, names: Sequence[str], current: str = "") -> Optional[str]:
    """返回选中的干员名；`""` 表示"清空该位置"；`None` 表示取消。"""
    dlg = OperatorPicker(parent, names, current)
    parent.wait_window(dlg)
    return dlg.result


class MoodDialog(tk.Toplevel):
    """设置心情（周期起点的心情值）。"""

    def __init__(self, parent, who: str, current, cycle_note: str = ""):
        super().__init__(parent, bg=theme.BG)
        self.title(f"设置心情 · {who}")
        self.resizable(False, False)
        self.result: Optional[Decimal] = None

        tk.Label(self, text=f"{who}", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_TITLE)).pack(anchor="w", padx=theme.PAD,
                                                                pady=(theme.PAD, 2))
        tk.Label(self, text="这是周期起点（0:00）的心情；之后由引擎按排班推算",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=300,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.PAD)

        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", padx=theme.PAD, pady=theme.GAP)
        self.var = tk.StringVar(value=theme.fmt_mood(current))
        entry = ttk.Entry(row, textvariable=self.var, width=10,
                          font=(theme.FONT_FAMILY, theme.FS_BIG))
        entry.pack(side="left")
        tk.Label(row, text="／ 24", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(side="left", padx=(4, 0))

        quick = tk.Frame(self, bg=theme.BG)
        quick.pack(fill="x", padx=theme.PAD)
        for v in ("0", "6", "12", "18", "24"):
            ttk.Button(quick, text=v, width=4,
                       command=lambda x=v: self.var.set(x)).pack(side="left", padx=(0, 4))

        self.err = tk.Label(self, text="", bg=theme.BG, fg=theme.DANGER,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.err.pack(anchor="w", padx=theme.PAD)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="确定", style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 6))
        entry.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        entry.focus_set()
        entry.selection_range(0, "end")
        _modal(self, parent)

    def _ok(self) -> None:
        try:
            v = Decimal(self.var.get().strip())
        except (InvalidOperation, ValueError):
            self.err.configure(text="请输入数字（0 ~ 24）")
            return
        if v < 0 or v > 24:
            self.err.configure(text="心情必须在 0 ~ 24 之间")
            return
        self.result = v
        self.destroy()


def ask_mood(parent, who: str, current, note: str = "") -> Optional[Decimal]:
    dlg = MoodDialog(parent, who, current, note)
    parent.wait_window(dlg)
    return dlg.result


class ShiftSettingsDialog(tk.Toplevel):
    """班次设置：周期时长 + 每班时长（两者必须相等，实时校验）。"""

    def __init__(self, parent, labels: Sequence[str], hours: Sequence, cycle: Decimal):
        super().__init__(parent, bg=theme.BG)
        self.title("班次设置")
        self.resizable(False, False)
        self.result: Optional[List[Decimal]] = None
        self._labels = list(labels)

        tk.Label(self, text="以 24 小时为一个周期：设置几班、每班几小时",
                 bg=theme.BG, fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_TITLE)
                 ).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, 2))
        tk.Label(self, text="各班长之和必须等于周期时长", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.PAD)

        cyc = tk.Frame(self, bg=theme.BG)
        cyc.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, 2))
        tk.Label(cyc, text="周期（小时）", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(side="left")
        self.cycle_var = tk.StringVar(value=theme.fmt_mood(cycle, 3))
        ttk.Entry(cyc, textvariable=self.cycle_var, width=8).pack(side="left", padx=6)
        ttk.Button(cyc, text="均分", command=self._even).pack(side="left")

        self.rows: List[tk.StringVar] = []
        grid = tk.Frame(self, bg=theme.BG)
        grid.pack(fill="x", padx=theme.PAD, pady=theme.GAP)
        for i, (label, h) in enumerate(zip(self._labels, hours)):
            tk.Label(grid, text=label, bg=theme.BG, fg=theme.TEXT, anchor="w",
                     font=(theme.FONT_FAMILY, theme.FS_BODY)).grid(row=i, column=0, sticky="w",
                                                                    pady=2)
            var = tk.StringVar(value=theme.fmt_mood(h, 3))
            var.trace_add("write", lambda *_a: self._validate())
            ttk.Entry(grid, textvariable=var, width=8).grid(row=i, column=1, padx=8)
            tk.Label(grid, text="小时", bg=theme.BG, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).grid(row=i, column=2, sticky="w")
            self.rows.append(var)
        self.cycle_var.trace_add("write", lambda *_a: self._validate())

        self.msg = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.msg.pack(anchor="w", padx=theme.PAD)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=theme.PAD)
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        self.ok = ttk.Button(btns, text="应用", style="Accent.TButton", command=self._ok)
        self.ok.pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())
        self._validate()
        _modal(self, parent)

    def _even(self) -> None:
        try:
            cycle = Decimal(self.cycle_var.get().strip())
        except (InvalidOperation, ValueError):
            return
        share = cycle / len(self.rows)
        for v in self.rows:
            v.set(theme.fmt_mood(share, 3))

    def _parse(self):
        try:
            cycle = Decimal(self.cycle_var.get().strip())
            hours = [Decimal(v.get().strip()) for v in self.rows]
        except (InvalidOperation, ValueError):
            return None, None
        return cycle, hours

    def _validate(self) -> None:
        cycle, hours = self._parse()
        if cycle is None:
            self.msg.configure(text="请输入数字", fg=theme.DANGER)
            self.ok.state(["disabled"])
            return
        total = sum(hours, Decimal("0"))
        if total != cycle:
            self.msg.configure(text=f"合计 {theme.fmt_mood(total, 3)}h ≠ 周期 "
                                    f"{theme.fmt_mood(cycle, 3)}h", fg=theme.DANGER)
            self.ok.state(["disabled"])
        elif any(h <= 0 for h in hours):
            self.msg.configure(text="每班时长必须为正数", fg=theme.DANGER)
            self.ok.state(["disabled"])
        else:
            self.msg.configure(text=f"合计 {theme.fmt_mood(total, 3)}h = 周期 ✔", fg=theme.OK)
            self.ok.state(["!disabled"])

    def _ok(self) -> None:
        cycle, hours = self._parse()
        if cycle is None or sum(hours, Decimal("0")) != cycle:
            return
        self.result = hours
        self.destroy()


def ask_shift_hours(parent, labels: Sequence[str], hours: Sequence, cycle: Decimal):
    dlg = ShiftSettingsDialog(parent, labels, hours, cycle)
    parent.wait_window(dlg)
    return dlg.result


class EntryEventDialog(tk.Toplevel):
    """**进驻事件**（M15a 患难之交）设置：换不换、换谁。

    对话框把"这是什么"写在最上面——这个开关不开也能用，但很多人第一次看到
    「结算进驻事件」并不知道它指的是"进驻那一刻的一次性心情跳变"。
    """

    TITLE = "结算进驻事件（M15a 患难之交）"

    def __init__(self, parent, enabled: bool, swap_with, candidates: Sequence[str],
                 current_holders: Sequence[str] = ()):
        super().__init__(parent, bg=theme.BG)
        self.title("进驻事件设置")
        self.resizable(False, False)
        self.result = None                     # (enabled: bool, swap_with: Optional[str])
        self._candidates = list(candidates)

        pad = dict(padx=theme.PAD)
        tk.Label(self, text="进驻事件 = 干员【进驻那一刻】的一次性心情跳变，不是每小时速率。",
                 bg=theme.BG, fg=theme.TEXT, justify="left", wraplength=430,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(anchor="w", **pad, pady=(theme.PAD, 2))
        tk.Label(self,
                 text="典型例子：菲亚梅塔「患难之交」——进驻宿舍时若自身是满心情，\n"
                      "就与同宿舍的某人【互换心情】（24 点换走对方的 6 点，对方反而变成 24）。\n"
                      "因为它只发生在进驻瞬间，所以默认【不】结算，需要你在这里明确打开。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=430,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad, pady=(0, theme.GAP))

        self.enabled = tk.BooleanVar(value=bool(enabled))
        ttk.Checkbutton(self, text="结算进驻事件（先换心情，再按排班往下算）",
                        variable=self.enabled).pack(anchor="w", **pad)

        box = tk.LabelFrame(self, text="交换对象", bg=theme.BG, fg=theme.TEXT,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                            relief="groove", labelanchor="nw")
        box.pack(fill="x", **pad, pady=(theme.GAP, 4))
        self.mode = tk.StringVar(value="default" if not swap_with else "pick")
        ttk.Radiobutton(box, text="前一位进驻（宿舍进驻顺序里的上一位，默认）",
                        value="default", variable=self.mode,
                        command=self._sync).pack(anchor="w", padx=theme.GAP, pady=(4, 0))
        row = tk.Frame(box, bg=theme.BG)
        row.pack(anchor="w", fill="x", padx=theme.GAP, pady=(2, 6))
        ttk.Radiobutton(row, text="指定干员：", value="pick", variable=self.mode,
                        command=self._sync).pack(side="left")
        self.person = tk.StringVar(value=swap_with or (self._candidates[0]
                                                      if self._candidates else ""))
        self.person_box = ttk.Combobox(row, textvariable=self.person, state="readonly",
                                       values=self._candidates, width=14)
        self.person_box.pack(side="left", padx=(4, 0))
        holders = "、".join(current_holders) if current_holders else "（本排班里没有）"
        tk.Label(box, text=f"能换的人：同宿舍的其他干员；触发者：{holders}。"
                           f"不在同一宿舍的人会被忽略（并给出提示）。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=420,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.GAP,
                                                                pady=(0, 6))

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", **pad, pady=(theme.GAP, theme.PAD))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="应用", style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())
        self._sync()
        _modal(self, parent)

    def _sync(self) -> None:
        self.person_box.configure(state="readonly" if self.mode.get() == "pick" else "disabled")

    def _ok(self) -> None:
        target = None
        if self.mode.get() == "pick":
            target = self.person.get().strip() or None
            if target is None:
                messagebox_showinfo_safe(self, "请选择一位干员，或改选「前一位进驻」")
                return
        self.result = (bool(self.enabled.get()), target)
        self.destroy()


def messagebox_showinfo_safe(parent, text: str) -> None:
    from tkinter import messagebox
    messagebox.showinfo("提示", text, parent=parent)


def ask_entry_event(parent, enabled: bool, swap_with, candidates: Sequence[str],
                    current_holders: Sequence[str] = ()):
    """返回 `(enabled, swap_with)`；取消返回 None。"""
    dlg = EntryEventDialog(parent, enabled, swap_with, candidates, current_holders)
    parent.wait_window(dlg)
    return dlg.result
