"""ui/dialogs.py —— 四个小对话框：选人 / 设心情 / 班次时长设置 / 进驻事件设置。

都做成"模态 + 返回结果"的简单函数（`ask_*` → 值或 None），调用方（`app.py`）不必关心细节。
键盘优先：选人框回车即确认、Esc 取消；设心情框支持 `0/6/12/18/24` 快捷按钮。

进驻事件设置（`EntryEventDialog`）按用户要求**只有三个设置**：① 开启心情交换、② 换谁、
③ 强制切换（勾＝等她满心情再换、不勾＝判定时没满就不换）；逐班覆盖收进折叠的「高级」区。
批量改干员与心情在 `ui/batch.py`。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import ttk
from typing import List, Optional, Sequence

from mood_soc.models import normalize_entry_when

from . import theme

# 「什么时候换」在下拉里的短标签（按班次用）
WHEN_LABELS = {
    "immediate": "立即（强制）",
    "wait": "等她回满",
    "full": "只在她满时",
}
LABEL_TO_WHEN = {v: k for k, v in WHEN_LABELS.items()}

# 心情输入的合法范围（周期起点心情．引擎侧同样钳位 [0, 24]）
MOOD_MIN_TEXT = Decimal("0")
MOOD_MAX_TEXT = Decimal("24")


def parse_mood(text) -> Optional[Decimal]:
    """把输入框里的文字解析成心情值 → `Decimal`；不合法（非数字 / 越界 / 空）返回 `None`。

    纯函数、无界面依赖：`MoodDialog`（单人设心情）与 `ui/batch.BatchDialog`（批量表格）
    共用这一处校验，免得两边对"什么算合法输入"的口径跑偏。
    """
    try:
        v = Decimal(str(text).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if v < MOOD_MIN_TEXT or v > MOOD_MAX_TEXT:
        return None
    return v


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
        v = parse_mood(self.var.get())
        if v is None:
            self.err.configure(text="请输入数字（0 ~ 24）")
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
    """**进驻事件**（M15a 患难之交）设置 —— 只有三个设置。

    | 设置 | 控件 | 落到引擎 |
    |---|---|---|
    | ① 开启心情交换 | 复选框 | `enabled`（与工具栏那个开关是同一个变量） |
    | ② 换谁 | 一个下拉 + 「更多干员…」 | `swap_with` + `scope`（**每个选项自带范围**，不会再出现"任意位置 + 前一位进驻"这种矛盾组合） |
    | ③ 强制切换 | 复选框 | `when`：勾＝`"wait"`（等她满心情再换）/ 不勾＝`"full"`（判定时她没满就不换） |

    按用户要求收成固定口径、不再出现在界面上的两件事：

    - **「对方心情是多少」不设开关**：固定"照换"——哪怕双方都是 24 也执行
      （数值不变；位置互换模式下位置照换）。
    - **「位置也一起互换」**：固定为「只换心情，两人都留在自己的岗位上」。

    逐班覆盖收进底部的**折叠区**（默认收起；导入的 MAA 逐班配置仍然生效、可展开改）。
    """

    TITLE = "进驻事件设置（换心情）"
    # 「换谁」的两个口径（其余下拉项＝具体干员名）
    PREV = "前一位进驻（同宿舍）"              # 游戏原口径：swap_with=None + scope="dorm"
    AUTO = "全基建最累的那位（自动）"            # swap_with="any" + scope="anywhere"
    INHERIT = "（跟随上面的默认）"              # 按班次：不覆盖
    DEFAULT_TARGET = "前一位进驻（默认口径）"     # 按班次：明确用默认口径

    def __init__(self, parent, enabled: bool, swap_with, candidates: Sequence[str],
                 current_holders: Sequence[str] = (), scope: str = "dorm",
                 restore_back: bool = True, when: str = "immediate",
                 shift_labels: Sequence[str] = (), per_shift: Sequence = (),
                 all_names: Sequence[str] = ()):
        super().__init__(parent, bg=theme.BG)
        self.title(self.TITLE)
        self.resizable(False, False)
        self.result = None      # (enabled, swap_with, scope, restore_back, when, per_shift)
        self._candidates = list(candidates)
        self._all_names = list(all_names) or list(candidates)
        self._shift_labels = list(shift_labels)
        self._per_shift_in = list(per_shift)

        pad = dict(padx=theme.PAD)
        tk.Label(self, text="进驻事件 = 干员【进驻那一刻】的一次性心情跳变，不是每小时速率。",
                 bg=theme.BG, fg=theme.TEXT, justify="left", wraplength=470,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(anchor="w", **pad, pady=(theme.PAD, 2))
        tk.Label(self,
                 text="典型例子：菲亚梅塔「患难之交」——进驻宿舍时与某人【互换心情】\n"
                      "（她拿 24 换走对方的 6 点，对方反而变成 24）。\n"
                      "它只发生在进驻瞬间，所以默认【不】结算，需要你在这里明确打开。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=470,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad, pady=(0, theme.GAP))

        # ① 开启
        self.enabled = tk.BooleanVar(value=bool(enabled))
        ttk.Checkbutton(self, text="① 开启心情交换（进驻宿舍那一刻，与她互换心情）",
                        variable=self.enabled, command=self._sync).pack(anchor="w", **pad)

        # ② 换谁（一个下拉；"在哪换"由选项自带，不再单独设）
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", **pad, pady=(theme.GAP, 0))
        tk.Label(row, text="② 换谁", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(side="left")
        target = (swap_with or "").strip()
        auto = (target.lower() in ("any", "auto", "anyone")
                or target in ("任意", "最累", "谁都可以"))
        self.target = tk.StringVar(value=(self.AUTO if auto else (target or self.PREV)))
        self._values = [self.PREV, self.AUTO] + [n for n in self._candidates
                                                 if n not in (self.PREV, self.AUTO)]
        self.target_box = ttk.Combobox(row, textvariable=self.target, state="readonly",
                                       values=self._values, width=24)
        self.target_box.pack(side="left", padx=(6, 4))
        self.more_btn = ttk.Button(row, text="更多干员…", command=self._pick_other)
        self.more_btn.pack(side="left")
        holders = "、".join(current_holders) if current_holders else "（本排班里没有）"
        tk.Label(self, text=f"触发者：{holders}。前两位自带范围（同宿舍 / 全基建最累的）；"
                            f"选到具体干员时可换基建任意位置的人。\n"
                            f"点到的人不在允许范围内时，该班次不换并给出提示。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=470,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad, pady=(2, 0))

        # ③ 强制切换（一个复选框取代原来的三选一）
        self.force = tk.BooleanVar(value=(normalize_entry_when(when) == "wait"))
        self.force_chk = ttk.Checkbutton(
            self, text="③ 强制切换（必须换成功：她没满心情就等她回满再换）",
            variable=self.force)
        self.force_chk.pack(anchor="w", **pad, pady=(theme.GAP, 0))
        note = ("不勾＝到交换班次判定时她心情没满，这一次就【不换】。\n"
                "「不管对方心情是多少都照换」是固定口径，不需要设置。")
        if normalize_entry_when(when) == "immediate":
            note += "\n（当前配置是「立刻换」那一档；界面不再提供，应用后会变成「没满就不换」。）"
        tk.Label(self, text=note, bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=470,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad, pady=(2, 0))

        # 高级：按班次覆盖（折叠，默认收起；已有逐班配置时自动展开）
        self.advanced = tk.BooleanVar(value=bool(self._per_shift_in))
        ttk.Checkbutton(self, text="高级：按班次单独设置（不勾＝三班共用上面这一套）",
                        variable=self.advanced, command=self._toggle_advanced).pack(
            anchor="w", **pad, pady=(theme.GAP, 0))
        self.adv_frame = tk.Frame(self, bg=theme.BG)
        self._build_advanced()

        self.btn_frame = tk.Frame(self, bg=theme.BG)
        self.btn_frame.pack(fill="x", **pad, pady=(theme.GAP, theme.PAD))
        ttk.Button(self.btn_frame, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(self.btn_frame, text="应用", style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 6))
        if self.advanced.get():
            self._toggle_advanced()
        self.bind("<Escape>", lambda _e: self.destroy())
        self._sync()
        _modal(self, parent)

    # ------------------------------------------------------------ 折叠区：按班次
    def _build_advanced(self) -> None:
        """逐班覆盖表格（构建后由 `_toggle_advanced` 决定显示与否）。"""
        f = self.adv_frame
        self.shift_rows = []            # [(使用 BooleanVar, 换谁 StringVar, 什么时候 StringVar)]
        if not self._shift_labels:
            tk.Label(f, text="（还没有导入排班，导入后可以逐班设置）", bg=theme.BG,
                     fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                     ).pack(anchor="w", padx=theme.GAP, pady=4)
            return
        hdr = tk.Frame(f, bg=theme.BG)
        hdr.pack(fill="x", padx=theme.GAP)
        for text, width in (("班次", 20), ("使用", 5), ("换给谁", 18), ("什么时候换", 14)):
            tk.Label(hdr, text=text, bg=theme.BG, fg=theme.MUTED, width=width, anchor="w",
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        values = [self.INHERIT, self.DEFAULT_TARGET, self.AUTO] + self._candidates
        # 界面口径只有「等她满 / 只在她满时」两档；旧配置里的「立刻换」仍会原样显示
        when_values = [self.INHERIT] + [WHEN_LABELS[m] for m in ("wait", "full")]
        for i, label in enumerate(self._shift_labels):
            row = tk.Frame(f, bg=theme.BG)
            row.pack(fill="x", padx=theme.GAP, pady=(2, 0))
            tk.Label(row, text=f"{i + 1}. {label}", bg=theme.BG, fg=theme.TEXT, width=20,
                     anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
            use = tk.BooleanVar(value=True)
            tk.Checkbutton(row, text="", variable=use, bg=theme.BG,
                           activebackground=theme.BG, highlightthickness=0).pack(
                side="left", padx=(4, 0))
            who = tk.StringVar(value=self.INHERIT)
            ttk.Combobox(row, textvariable=who, state="readonly", values=values,
                         width=16).pack(side="left", padx=(4, 6))
            when_var = tk.StringVar(value=self.INHERIT)
            ttk.Combobox(row, textvariable=when_var, state="readonly", values=when_values,
                         width=12).pack(side="left")
            self.shift_rows.append((use, who, when_var))
        tk.Label(f, text="使用＝这个班要不要换；换给谁＝这一班的交换对象；什么时候换＝这一班的触发方式。\n"
                         "「跟随上面的默认」= 用 ①②③ 的设置；全都没改的班不会生成覆盖项。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=460,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.GAP,
                                                                pady=(4, 6))
        self._load_per_shift()

    def _toggle_advanced(self) -> None:
        """展开/收起逐班表格（`before=` 保证它仍在按钮上方）。"""
        if self.advanced.get():
            self.adv_frame.pack(fill="x", padx=theme.PAD, pady=(2, 0), before=self.btn_frame)
        else:
            self.adv_frame.pack_forget()

    def _pick_other(self) -> None:
        """「更多干员…」：从全量名册里搜（下拉只放当前排班的干员，省得翻 900 个）。"""
        cur = self.target.get()
        cur = "" if cur in (self.PREV, self.AUTO) else cur
        picked = ask_operator(self, self._all_names, cur)
        if not picked:
            return
        if picked not in self._values:
            self._values.append(picked)
            self.target_box.configure(values=self._values)
        self.target.set(picked)

    # ------------------------------------------------------------------ 按班次
    def _load_per_shift(self) -> None:
        """把已有的按班次配置填进表格（`EntryShiftOverride` → 三个控件）。"""
        for i, (use, who, when_var) in enumerate(self.shift_rows):
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
            if ov.when is not None:
                when_var.set(WHEN_LABELS.get(ov.when, self.INHERIT))
            elif ov.force is not None:              # 旧字段兜底
                when_var.set(WHEN_LABELS["wait" if ov.force else "full"])

    def _collect_per_shift(self) -> list:
        """把表格收成 `EntryShiftOverride` 列表（只写"改过的"项，其余留给默认）。"""
        from mood_soc.models import EntryShiftOverride

        out = []
        for i, (use, who, when_var) in enumerate(self.shift_rows):
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
            label = when_var.get()
            when = LABEL_TO_WHEN.get(label) if label != self.INHERIT else None
            use_on = bool(use.get())
            # 全都跟随默认（使用=是、对象=跟随、时机=跟随）→ 不生成覆盖项
            if use_on and swap_with is None and when is None:
                continue
            out.append(EntryShiftOverride(key=i + 1, enabled=use_on, swap_with=swap_with,
                                          scope=scope, when=when))
        return out

    def _sync(self) -> None:
        """关掉「① 开启」时把 ②③ 置灰（① 自己不能灰）。"""
        on = bool(self.enabled.get())
        self.target_box.configure(state="readonly" if on else "disabled")
        for w in (self.more_btn, self.force_chk):
            try:
                w.state(["!disabled"] if on else ["disabled"])
            except (tk.TclError, AttributeError):
                pass

    def _ok(self) -> None:
        """收成 `(enabled, swap_with, scope, restore_back, when, per_shift)`。

        `scope` 由「② 换谁」的选项自带（前一位进驻＝同宿舍；最累的 / 具体干员＝基建任意位置），
        `restore_back` 固定 `True`（只换心情、两人留原位）。
        """
        enabled = bool(self.enabled.get())
        value = self.target.get().strip()
        swap_with, scope = None, "dorm"
        if value == self.AUTO:
            swap_with, scope = "any", "anywhere"
        elif value and value != self.PREV:
            swap_with, scope = value, "anywhere"
        when = "wait" if self.force.get() else "full"
        self.result = (enabled, swap_with, scope, True, when, self._collect_per_shift())
        self.destroy()


def messagebox_showinfo_safe(parent, text: str) -> None:
    from tkinter import messagebox
    messagebox.showinfo("提示", text, parent=parent)


def ask_entry_event(parent, enabled: bool, swap_with, candidates: Sequence[str],
                    current_holders: Sequence[str] = (), scope: str = "dorm",
                    restore_back: bool = True, when: str = "immediate",
                    shift_labels: Sequence[str] = (), per_shift: Sequence = (),
                    all_names: Sequence[str] = ()):
    """返回 `(enabled, swap_with, scope, restore_back, when, per_shift)`；取消返回 None。

    `candidates`＝下拉里直接列的干员（排班里的那些人）；`all_names`＝「更多干员…」搜索用的
    全量名册（缺省＝`candidates`）。
    """
    dlg = EntryEventDialog(parent, enabled, swap_with, candidates, current_holders,
                           scope=scope, restore_back=restore_back, when=when,
                           shift_labels=shift_labels, per_shift=per_shift,
                           all_names=all_names)
    parent.wait_window(dlg)
    return dlg.result
