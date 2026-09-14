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
    """**进驻事件**（M15a 患难之交）设置：换不换、在哪换、换谁、换完怎么放、要不要强等。

    对话框把"这是什么"写在最上面——这个开关不开也能用，但很多人第一次看到
    「结算进驻事件」并不知道它指的是"进驻那一刻的一次性心情跳变"。
    """

    TITLE = "结算进驻事件（M15a 患难之交）"
    # 「交换对象」里的自动选项（对应引擎的 swap_with="any"）
    AUTO = "全基建最累的那位（自动）"
    INHERIT = "（跟随上面的默认）"          # 按班次：不覆盖
    DEFAULT_TARGET = "前一位进驻（默认口径）"  # 按班次：明确用默认口径

    def __init__(self, parent, enabled: bool, swap_with, candidates: Sequence[str],
                 current_holders: Sequence[str] = (), scope: str = "dorm",
                 restore_back: bool = True, force: bool = False,
                 shift_labels: Sequence[str] = (), per_shift: Sequence = ()):
        super().__init__(parent, bg=theme.BG)
        self.title("进驻事件设置（换心情）")
        self.resizable(False, False)
        self.result = None      # (enabled, swap_with, scope, restore_back, force, per_shift)
        self._candidates = list(candidates)
        self._shift_labels = list(shift_labels)
        self._per_shift_in = list(per_shift)

        pad = dict(padx=theme.PAD)
        tk.Label(self, text="进驻事件 = 干员【进驻那一刻】的一次性心情跳变，不是每小时速率。",
                 bg=theme.BG, fg=theme.TEXT, justify="left", wraplength=470,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(anchor="w", **pad, pady=(theme.PAD, 2))
        tk.Label(self,
                 text="典型例子：菲亚梅塔「患难之交」——进驻宿舍时若自身是满心情，\n"
                      "就与某人【互换心情】（她拿 24 换走对方的 6 点，对方反而变成 24）。\n"
                      "因为它只发生在进驻瞬间，所以默认【不】结算，需要你在这里明确打开。\n"
                      "下面四项决定「在哪换、换谁、换完怎么放、要不要等她」——默认值就是游戏原口径。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=470,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad, pady=(0, theme.GAP))

        self.enabled = tk.BooleanVar(value=bool(enabled))
        ttk.Checkbutton(self, text="结算进驻事件（先换心情，再按排班往下算）",
                        variable=self.enabled, command=self._sync).pack(anchor="w", **pad)

        # ① 范围
        box1 = tk.LabelFrame(self, text="① 在哪换（换心情的范围）", bg=theme.BG, fg=theme.TEXT,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1, relief="groove",
                             labelanchor="nw")
        box1.pack(fill="x", **pad, pady=(theme.GAP, 4))
        self.scope = tk.StringVar(value="anywhere" if scope == "anywhere" else "dorm")
        ttk.Radiobutton(box1, text="仅同一宿舍（游戏原口径：换「前一位进驻」的那位）",
                        value="dorm", variable=self.scope, command=self._sync
                        ).pack(anchor="w", padx=theme.GAP, pady=(4, 0))
        ttk.Radiobutton(box1, text="基建任意位置（任何设施上的干员都能换）",
                        value="anywhere", variable=self.scope, command=self._sync
                        ).pack(anchor="w", padx=theme.GAP, pady=(0, 6))

        # ② 对象
        box2 = tk.LabelFrame(self, text="② 换谁（交换对象）", bg=theme.BG, fg=theme.TEXT,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1, relief="groove",
                             labelanchor="nw")
        box2.pack(fill="x", **pad, pady=(0, 4))
        target = (swap_with or "").strip()
        auto = target.lower() in ("any", "auto", "anyone") or target in ("任意", "最累", "谁都可以")
        if not target:
            mode = "default" if scope != "anywhere" else "auto"
        else:
            mode = "auto" if auto else "pick"
        self.mode = tk.StringVar(value=mode)
        self.prev_radio = ttk.Radiobutton(box2, text="前一位进驻（宿舍进驻顺序里的上一位）",
                                          value="default", variable=self.mode, command=self._sync)
        self.prev_radio.pack(anchor="w", padx=theme.GAP, pady=(4, 0))
        self.auto_radio = ttk.Radiobutton(
            box2, text="全基建最累的那位（自动挑心情最低的）", value="auto",
            variable=self.mode, command=self._sync)
        self.auto_radio.pack(anchor="w", padx=theme.GAP, pady=(0, 0))
        row = tk.Frame(box2, bg=theme.BG)
        row.pack(anchor="w", fill="x", padx=theme.GAP, pady=(0, 4))
        self.pick_radio = ttk.Radiobutton(row, text="指定干员：", value="pick",
                                          variable=self.mode, command=self._sync)
        self.pick_radio.pack(side="left")
        self.person = tk.StringVar(value=(target if mode == "pick" else
                                          (self._candidates[0] if self._candidates else "")))
        self.person_box = ttk.Combobox(row, textvariable=self.person, state="readonly",
                                       values=self._candidates, width=14)
        self.person_box.pack(side="left", padx=(4, 0))
        holders = "、".join(current_holders) if current_holders else "（本排班里没有）"
        tk.Label(box2, text=f"触发者：{holders}。指定对象若不在允许范围内，该班次不换并给出提示。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=460,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.GAP,
                                                                pady=(0, 6))

        # ③ 换完怎么放
        box3 = tk.LabelFrame(self, text="③ 换完之后（被换满的那个人怎么放）", bg=theme.BG,
                             fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                             relief="groove", labelanchor="nw")
        box3.pack(fill="x", **pad, pady=(0, 4))
        self.restore = tk.StringVar(value="back" if restore_back else "swap")
        ttk.Radiobutton(box3, text="把他换回原位（默认）——两人都留在自己的岗位上，只交换心情",
                        value="back", variable=self.restore).pack(anchor="w", padx=theme.GAP,
                                                                  pady=(4, 0))
        ttk.Radiobutton(box3, text="位置也一起互换——她接管对方岗位，对方进她的位置",
                        value="swap", variable=self.restore).pack(anchor="w", padx=theme.GAP,
                                                                  pady=(0, 6))

        # ④ 强制等待
        box4 = tk.LabelFrame(self, text="④ 强制换心情（到点了但她没满）", bg=theme.BG,
                             fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                             relief="groove", labelanchor="nw")
        box4.pack(fill="x", **pad, pady=(0, 4))
        self.force = tk.BooleanVar(value=bool(force))
        ttk.Checkbutton(box4, text="等她回满心情的那一刻再换", variable=self.force
                        ).pack(anchor="w", padx=theme.GAP, pady=(4, 0))
        tk.Label(box4, text="不勾选：到她该换的时候（每班开始）心情不满 → 这一次就不换了；\n"
                            "勾选：一直等到她回满那一刻立刻换（她本次能换出去的心情更多，"
                            "但可能要等到班次中段）。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=460,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.GAP,
                                                                pady=(0, 6))

        # ⑤ 按班次（覆盖上面的默认）
        box5 = tk.LabelFrame(self, text="⑤ 按班次（不填就跟随上面的默认；3 班排班可逐班不同）",
                             bg=theme.BG, fg=theme.TEXT,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1, relief="groove",
                             labelanchor="nw")
        box5.pack(fill="x", **pad, pady=(0, 4))
        self.shift_rows = []            # [(使用 BooleanVar, 换谁 StringVar, 强制 BooleanVar)]
        if self._shift_labels:
            hdr = tk.Frame(box5, bg=theme.BG)
            hdr.pack(fill="x", padx=theme.GAP, pady=(4, 0))
            for text, width in (("班次", 22), ("使用", 5), ("换给谁", 20), ("强制", 5)):
                tk.Label(hdr, text=text, bg=theme.BG, fg=theme.MUTED, width=width, anchor="w",
                         font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
            values = [self.INHERIT, self.DEFAULT_TARGET, self.AUTO] + self._candidates
            for i, label in enumerate(self._shift_labels):
                row = tk.Frame(box5, bg=theme.BG)
                row.pack(fill="x", padx=theme.GAP, pady=(2, 0))
                tk.Label(row, text=f"{i + 1}. {label}", bg=theme.BG, fg=theme.TEXT, width=22,
                         anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
                use = tk.BooleanVar(value=True)
                tk.Checkbutton(row, text="", variable=use, bg=theme.BG,
                               activebackground=theme.BG, highlightthickness=0).pack(
                    side="left", padx=(6, 0))
                who = tk.StringVar(value=self.INHERIT)
                ttk.Combobox(row, textvariable=who, state="readonly", values=values,
                             width=18).pack(side="left", padx=(6, 6))
                strong = tk.BooleanVar(value=False)
                tk.Checkbutton(row, text="", variable=strong, bg=theme.BG,
                               activebackground=theme.BG, highlightthickness=0).pack(side="left")
                self.shift_rows.append((use, who, strong))
            tk.Label(box5,
                     text="使用＝这个班要不要换；换给谁＝这一班的交换对象；"
                          "强制＝这一班到点没满就等她回满再换。\n"
                          "「跟随上面的默认」= 用 ①~④ 的设置；"
                          "「前一位进驻」= 这一班明确按同宿舍口径（不受①「任意位置」影响）。",
                     bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=460,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.GAP,
                                                                    pady=(4, 6))
            self._load_per_shift()
        else:
            tk.Label(box5, text="（还没有导入排班，导入后可以逐班设置）", bg=theme.BG,
                     fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                     ).pack(anchor="w", padx=theme.GAP, pady=6)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", **pad, pady=(theme.GAP, theme.PAD))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="应用", style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())
        self._sync()
        _modal(self, parent)

    # ------------------------------------------------------------------ 按班次
    def _load_per_shift(self) -> None:
        """把已有的按班次配置填进表格（`EntryShiftOverride` → 三个控件）。"""
        for i, (use, who, strong) in enumerate(self.shift_rows):
            ov = next((o for o in self._per_shift_in if o.matches(i, self._shift_labels[i])), None)
            if ov is None:
                continue
            if ov.enabled is not None:
                use.set(bool(ov.enabled))
            if ov.swap_with is not None:
                if ov.swap_with == "":
                    who.set(self.DEFAULT_TARGET)
                elif ov.swap_with in ("any", "auto", "anyone", "任意", "最累", "谁都可以"):
                    who.set(self.AUTO)
                else:
                    who.set(ov.swap_with)
            if ov.force is not None:
                strong.set(bool(ov.force))

    def _collect_per_shift(self) -> list:
        """把表格收成 `EntryShiftOverride` 列表（只写"改过的"项，其余留给默认）。"""
        from mood_soc.models import EntryShiftOverride

        out = []
        for i, (use, who, strong) in enumerate(self.shift_rows):
            target = who.get()
            swap_with = None
            scope = None
            if target == self.DEFAULT_TARGET:
                # 「前一位进驻」这一班就明确用同宿舍口径（否则 scope=anywhere 下会被读成"自动挑"）
                swap_with = ""
                scope = "dorm"
            elif target == self.AUTO:
                swap_with = "any"
            elif target != self.INHERIT:
                swap_with = target
            use_on = bool(use.get())
            force_on = bool(strong.get())
            # 全都跟随默认（使用=是、对象=跟随、强制=否）→ 不生成覆盖项
            if use_on and swap_with is None and not force_on:
                continue
            out.append(EntryShiftOverride(key=i + 1, enabled=use_on, swap_with=swap_with,
                                          scope=scope, force=force_on))
        return out

    def _sync(self) -> None:
        """按"范围"联动可选对象：仅同宿舍时才有「前一位进驻」；关掉总开关则全部置灰。"""
        on = self.enabled.get()
        anywhere = self.scope.get() == "anywhere"
        if anywhere and self.mode.get() == "default":
            self.mode.set("auto")               # 任意位置下没有"同宿舍前一位"可言
        if not anywhere and self.mode.get() == "auto" and not self._candidates:
            self.mode.set("default")
        state = "normal" if on else "disabled"
        for w in (self.prev_radio, self.auto_radio, self.pick_radio):
            w.state(["!disabled"] if state == "normal" else ["disabled"])
        self.prev_radio.state(["disabled"] if anywhere else ["!disabled"])
        self.person_box.configure(state="readonly" if (on and self.mode.get() == "pick")
                                  else "disabled")

    def _ok(self) -> None:
        enabled = bool(self.enabled.get())
        scope = self.scope.get()
        restore_back = self.restore.get() == "back"
        force = bool(self.force.get())
        swap_with = None
        if enabled:
            mode = self.mode.get()
            if mode == "pick":
                swap_with = self.person.get().strip() or None
                if swap_with is None:
                    messagebox_showinfo_safe(self, "请选择一位干员，或改选「自动挑最累的」/「前一位进驻」")
                    return
            elif mode == "auto":
                swap_with = "any"
            # mode == "default" → None（引擎默认「前一位进驻」）
        self.result = (enabled, swap_with, scope, restore_back, force, self._collect_per_shift())
        self.destroy()


def messagebox_showinfo_safe(parent, text: str) -> None:
    from tkinter import messagebox
    messagebox.showinfo("提示", text, parent=parent)


def ask_entry_event(parent, enabled: bool, swap_with, candidates: Sequence[str],
                    current_holders: Sequence[str] = (), scope: str = "dorm",
                    restore_back: bool = True, force: bool = False,
                    shift_labels: Sequence[str] = (), per_shift: Sequence = ()):
    """返回 `(enabled, swap_with, scope, restore_back, force, per_shift)`；取消返回 None。"""
    dlg = EntryEventDialog(parent, enabled, swap_with, candidates, current_holders,
                           scope=scope, restore_back=restore_back, force=force,
                           shift_labels=shift_labels, per_shift=per_shift)
    parent.wait_window(dlg)
    return dlg.result
