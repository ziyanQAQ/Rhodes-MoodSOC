"""ui/dialogs.py —— 四个小对话框：选人 / 设心情 / 班次时长设置 / 进驻事件设置。

都做成"模态 + 返回结果"的简单函数（`ask_*` → 值或 None），调用方（`app.py`）不必关心细节。
键盘优先：选人框回车即确认、Esc 取消；设心情框支持 `0/6/12/18/24` 快捷按钮。

进驻事件设置（`EntryEventDialog`）：一个总开关 + **一张"每班一行"的表**
（`用 / 换谁 / 强制切换`）——真正逐班的就是这一整组，所以不再分"全局值 + 例外"两层。
批量改干员与心情在 `ui/batch.py`。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal, InvalidOperation
from tkinter import ttk
from typing import List, Optional, Sequence

from mood_soc import entry_target_kind
from mood_soc.models import normalize_entry_when

from . import theme

# 心情输入的合法范围（周期起点心情．引擎侧同样钳位 [0, 24]）
MOOD_MIN_TEXT = Decimal("0")
MOOD_MAX_TEXT = Decimal("24")

# 表格行的上下留白（「闲置入宿」那一张分组表用）
ROW_PAD = 1


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


class TimelinePanel(tk.Frame):
    """**时间轴**：周期时长 + 每班时长（两者必须相等，实时校验）。

    宿主：`ShiftSettingsDialog`（独立对话框）与 `ui.settings` 的设置中心分区。
    `on_change(hours)`：合法时回调（设置中心用它做"改动立即生效"；独立对话框不传）。
    """

    def __init__(self, master, labels: Sequence[str], hours: Sequence, cycle: Decimal,
                 on_change=None, on_validity=None):
        super().__init__(master, bg=theme.BG)
        self._labels = list(labels)
        self._on_change = on_change
        self.on_validity = on_validity      # 校验结果变化时回调（对话框用它开关「应用」）
        self.valid = False

        tk.Label(self, text="以 24 小时为一个周期：设置几班、每班几小时",
                 bg=theme.BG, fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_TITLE)
                 ).pack(anchor="w", pady=(0, 2))
        tk.Label(self, text="各班长之和必须等于周期时长", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w")

        cyc = tk.Frame(self, bg=theme.BG)
        cyc.pack(fill="x", pady=(theme.GAP, 2))
        tk.Label(cyc, text="周期（小时）", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(side="left")
        self.cycle_var = tk.StringVar(value=theme.fmt_mood(cycle, 3))
        ttk.Entry(cyc, textvariable=self.cycle_var, width=8).pack(side="left", padx=6)
        ttk.Button(cyc, text="均分", command=self._even).pack(side="left")

        self.rows: List[tk.StringVar] = []
        grid = tk.Frame(self, bg=theme.BG)
        grid.pack(fill="x", pady=theme.GAP)
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
        self.msg.pack(anchor="w")
        self._validate()

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
            self.valid = False
            self._notify()
            return
        total = sum(hours, Decimal("0"))
        if total != cycle:
            self.msg.configure(text=f"合计 {theme.fmt_mood(total, 3)}h ≠ 周期 "
                                    f"{theme.fmt_mood(cycle, 3)}h", fg=theme.DANGER)
            self.valid = False
        elif any(h <= 0 for h in hours):
            self.msg.configure(text="每班时长必须为正数", fg=theme.DANGER)
            self.valid = False
        else:
            self.msg.configure(text=f"合计 {theme.fmt_mood(total, 3)}h = 周期 ✔", fg=theme.OK)
            self.valid = True
        self._notify()

    def _notify(self) -> None:
        if self.on_validity is not None:
            self.on_validity(self.valid)
        if self._on_change is not None and self.valid:
            self._on_change(self.value())

    def value(self) -> Optional[List[Decimal]]:
        """合法时返回各班时长，否则 None。"""
        cycle, hours = self._parse()
        if cycle is None or sum(hours, Decimal("0")) != cycle or any(h <= 0 for h in hours):
            return None
        return hours


class EntryEventMixin:
    """**进驻事件**（M15a 患难之交）设置 —— **一张表说尽"每个班次怎么换"**。

    | 控件 | 落到引擎 |
    |---|---|
    | ① 开启心情交换（总开关） | `enabled` |
    | 表格每行「用」 | `per_shift[key].enabled`（不勾＝这一班不换心情） |
    | 表格每行「换谁」 | `swap_with` + `scope`（**每个选项自带范围**） |
    | 表格每行「强制切换」 | `when`：勾＝`"wait"`（等她满心情再换）/ 不勾＝`"full"`（判定时没满就不换） |

    为什么是"每班一行"：真正逐班的东西本来就是**这一整组**（用不用 + 换谁 + 要不要等），
    所以不再做"全局值 + 例外"那两层（旧版的 ①②③ + ④ + 折叠高级区就是那么分的，
    设置时要在脑子里做三次映射）。现在**第 1 个勾着「用」的班次那一行充当全局默认**，
    其余班次只有和它不同才写一条 `per_shift` 覆盖 ⇒ 三班设置相同时 `per_shift` 为空、
    行为与"没有这张表"逐位相同。

    固定口径（不设开关）：**「对方心情是多少」照换**（双方都是 24 也执行）；
    **位置不动**（只换心情，界面固定 `restore_back=True`）。

    ⚠️ 本类**只建控件、只收结果**，自己不是窗口：宿主有两种——
    `EntryEventDialog`（独立对话框）与 `ui.settings` 里的设置中心分区（Frame）。
    两者共用这一份实现，免得出现"两套入口两套行为"。
    """

    TITLE = "进驻事件设置（换心情）"
    # 「换谁」的两个口径（其余下拉项＝具体干员名）
    PREV = "前一位进驻（同宿舍）"              # 游戏原口径：swap_with=None + scope="dorm"
    AUTO = "全基建最累的那位（自动）"            # swap_with="any" + scope="anywhere"

    def _init_entry_body(self, parent, enabled: bool, swap_with, candidates: Sequence[str],
                         current_holders: Sequence[str] = (), scope: str = "dorm",
                         restore_back: bool = True, when: str = "immediate",
                         shift_labels: Sequence[str] = (), per_shift: Sequence = (),
                         on_change=None):
        """把状态收好并建出整块控件（宿主的 `__init__` 里调用；`self` 必须是 tk 容器）。"""
        self._on_change = on_change
        self._candidates = list(candidates)
        self._shift_labels = list(shift_labels)
        self._per_shift_in = list(per_shift)
        # 全局配置（没有逐班覆盖时，每行的预填值就是它）
        self._swap_with_in = swap_with
        self._scope_in = scope or "dorm"
        self._when_in = normalize_entry_when(when) or "full"

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

        # ① 总开关
        self.enabled = tk.BooleanVar(value=bool(enabled))
        ttk.Checkbutton(self, text="① 开启心情交换（进驻宿舍那一刻，与她互换心情）",
                        variable=self.enabled, command=self._on_enabled).pack(anchor="w", **pad)

        holders = "、".join(current_holders) if current_holders else "（本排班里没有）"
        tk.Label(self, text=f"触发者：{holders}。勾了「用」的班次才换心情；"
                            f"「换谁」的两个口径自带范围：前一位进驻＝同一宿舍，"
                            f"全基建最累的 / 具体干员＝基建任意位置。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=520,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad,
                                                               pady=(2, theme.GAP))

        # ② 一张表：每个班次自己的「用 / 换谁 / 强制切换」（没有"全局值 + 例外"两层）
        self._build_shift_table()

        tk.Label(self, text="「强制切换」勾上＝她没满心情就等她回满再换；不勾＝判定时没满就不换。\n"
                            "对方心情是多少都照换（固定口径）；位置不变，只换心情。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=520,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad,
                                                               pady=(theme.GAP, 0))
        self._sync()

    # ------------------------------------------------------------ 逐班表格
    def _target_label(self, swap_with, scope: str) -> str:
        """引擎口径 → 下拉里的文字（口径判定唯一来源仍是 `entry_target_kind`）。"""
        kind = entry_target_kind(swap_with, scope)
        return {"auto": self.AUTO, "named": str(swap_with), "default": self.PREV}[kind]

    def _label_target(self, label: str):
        """下拉文字 → `(swap_with, scope)`；`PREV` 用同宿舍口径（否则 anywhere 下会被读成自动挑）。"""
        if label == self.AUTO:
            return "any", "anywhere"
        if label == self.PREV or not label:
            return None, "dorm"
        return label, "anywhere"

    def _shift_row_defaults(self, index: int):
        """第 `index` 班这一行怎么预填 → `(用, 换谁文字, 强制切换)`。

        有逐班覆盖就用覆盖，没有就用全局配置——所以"没配过 per_shift"时每一行都预填成
        当前的全局设置，等于"三班用同一套"（与旧版"④ 默认全选"逐位等价）。
        """
        ov = next((o for o in self._per_shift_in
                   if o.matches(index, self._shift_labels[index])), None)
        sw = self._swap_with_in
        scope = self._scope_in
        mode = self._when_in
        use = True
        if ov is not None:
            if ov.enabled is not None:
                use = bool(ov.enabled)
            if ov.swap_with is not None:
                sw = ov.swap_with or None            # "" = 明确"不指定" → 回到前一位进驻
            if ov.scope is not None:
                scope = ov.scope
            if ov.when is not None:
                mode = normalize_entry_when(ov.when) or mode
            elif ov.force is not None:               # 旧字段兜底
                mode = "wait" if ov.force else "full"
        return use, self._target_label(sw, scope), (mode == "wait")

    def _build_shift_table(self) -> None:
        """「班次 | 用 | 换谁 | 强制切换」——一行说尽这一班的行为。"""
        self.shift_use: List[tk.BooleanVar] = []
        self.shift_who: List[tk.StringVar] = []
        self.shift_force: List[tk.BooleanVar] = []
        self._shift_widgets: list = []           # ① 关掉时要一起置灰的控件
        if not self._shift_labels:
            tk.Label(self, text="② 每个班次：还没有导入排班，导入后可以逐班设置。",
                     bg=theme.BG, fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                     ).pack(anchor="w", padx=theme.PAD)
            return
        box = tk.LabelFrame(self, text="② 每个班次单独设置（勾了「用」的班次才换心情）",
                            bg=theme.BG, fg=theme.TEXT,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1, relief="groove",
                            labelanchor="nw")
        box.pack(fill="x", padx=theme.PAD)
        hdr = tk.Frame(box, bg=theme.BG)
        hdr.pack(fill="x", padx=theme.GAP, pady=(4, 0))
        for text, width in (("班次", 24), ("用", 5), ("换谁", 26), ("强制切换", 10)):
            tk.Label(hdr, text=text, bg=theme.BG, fg=theme.MUTED, width=width, anchor="w",
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        values = [self.PREV, self.AUTO] + [n for n in self._candidates
                                           if n not in (self.PREV, self.AUTO)]
        for i, label in enumerate(self._shift_labels):
            use_d, who_d, force_d = self._shift_row_defaults(i)
            row = tk.Frame(box, bg=theme.BG)
            row.pack(fill="x", padx=theme.GAP, pady=(2, 0))
            tk.Label(row, text=f"{i + 1}. {label}", bg=theme.BG, fg=theme.TEXT, width=24,
                     anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
            use = tk.BooleanVar(value=use_d)
            chk = tk.Checkbutton(row, text="", variable=use, bg=theme.BG,
                                 activebackground=theme.BG, highlightthickness=0,
                                 command=self._notify)
            chk.pack(side="left", padx=(4, 0))
            who = tk.StringVar(value=who_d)
            box_who = ttk.Combobox(row, textvariable=who, state="readonly", values=values,
                                   width=22)
            box_who.pack(side="left", padx=(4, 6))
            box_who.bind("<<ComboboxSelected>>", lambda _e: self._notify())
            force = tk.BooleanVar(value=force_d)
            chk_force = tk.Checkbutton(row, text="", variable=force, bg=theme.BG,
                                       activebackground=theme.BG, highlightthickness=0,
                                       command=self._notify)
            chk_force.pack(side="left", padx=(4, 0))
            self.shift_use.append(use)
            self.shift_who.append(who)
            self.shift_force.append(force)
            self._shift_widgets.extend([chk, box_who, chk_force])
        bar = tk.Frame(box, bg=theme.BG)
        bar.pack(fill="x", padx=theme.GAP, pady=(4, 2))
        for text, value in (("全选", True), ("全不选", False)):
            btn = ttk.Button(bar, text=text, command=lambda v=value: self._set_all_shifts(v))
            btn.pack(side="left", padx=(0, 6))
            self._shift_widgets.append(btn)
        tk.Label(box, text="第 1 班就是「默认口径」：其余班次只有和它不同时才单独记一笔，"
                           "所以三班都一样时不会产生多余配置。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=500,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.GAP,
                                                                pady=(0, 6))

    def _set_all_shifts(self, value: bool) -> None:
        """表格下方的全选 / 全不选。"""
        for var in self.shift_use:
            var.set(bool(value))

    def _sync(self) -> None:
        """关掉「① 开启」时把整张表置灰（① 自己不能灰）。"""
        on = bool(self.enabled.get())
        for w in self._shift_widgets:
            try:
                w.state(["!disabled"] if on else ["disabled"])
            except (tk.TclError, AttributeError):
                w.configure(state="normal" if on else "disabled")

    def _on_enabled(self) -> None:
        """① 总开关：置灰表格 + 通知宿主（设置中心＝立即生效）。"""
        self._sync()
        self._notify()

    def _notify(self) -> None:
        """表格/开关改动 → 通知宿主（独立对话框没有回调；设置中心用它做"改动立即生效"）。"""
        if self._on_change is not None:
            self._on_change(self.value())

    def value(self):
        """收成 `(enabled, swap_with, scope, restore_back, when, per_shift)`。

        **第 1 个勾着「用」的班次那一行充当全局默认**（写进 `swap_with`/`scope`/`when`），
        其余班次只有和它不同（含"这一班不用"）才写一条 `per_shift` 覆盖——
        于是"三班设置相同"时 `per_shift` 为空，工具栏文案仍是那套紧凑的单设置写法。
        `restore_back` 固定 `True`（只换心情、两人留原位）。
        """
        from mood_soc.models import EntryShiftOverride

        rows = []
        for i in range(len(self._shift_labels)):
            sw, scope = self._label_target(self.shift_who[i].get().strip())
            rows.append((bool(self.shift_use[i].get()), sw, scope,
                         bool(self.shift_force[i].get())))
        bi = next((i for i, r in enumerate(rows) if r[0]), 0)
        _, base_sw, base_scope, base_force = rows[bi] if rows else (True, None, "dorm", False)
        per_shift = []
        for i, (use, sw, scope, force) in enumerate(rows):
            if use and sw == base_sw and scope == base_scope and force == base_force:
                continue                     # 与第 1 班那一行完全相同 → 不写覆盖项
            per_shift.append(EntryShiftOverride(
                key=i + 1, enabled=use,
                # `""`＝明确"不指定"（回到「前一位进驻」），否则会被全局的指定对象盖住
                swap_with=(sw if sw is not None else ""),
                scope=scope, when=("wait" if force else "full")))
        return (bool(self.enabled.get()), base_sw, base_scope, True,
                "wait" if base_force else "full", per_shift)


class EntryEventPanel(tk.Frame, EntryEventMixin):
    """「换心情」设置**内容本体**（设置中心「换心情」分区；改动经 `on_change` 立即生效）。"""

    def __init__(self, master, enabled: bool, swap_with, candidates: Sequence[str],
                 current_holders: Sequence[str] = (), scope: str = "dorm",
                 restore_back: bool = True, when: str = "immediate",
                 shift_labels: Sequence[str] = (), per_shift: Sequence = (),
                 on_change=None):
        super().__init__(master, bg=theme.BG)
        self._init_entry_body(master, enabled, swap_with, candidates, current_holders,
                              scope=scope, restore_back=restore_back, when=when,
                              shift_labels=shift_labels, per_shift=per_shift,
                              on_change=on_change)


def messagebox_showinfo_safe(parent, text: str) -> None:
    from tkinter import messagebox
    messagebox.showinfo("提示", text, parent=parent)


class LevelDialog(tk.Toplevel):
    """改一间房的**等级**（容量随之变化）。

    上游依据：`rooms[].phases[lv].maxStationedNum` —— 制造站/贸易站 1/2/3 人、
    发电站 1/1/1、宿舍 5/5/5/5/5、控制中枢 1/2/3/4/5、会客室与训练室 2、加工站与办公室 1。
    等级同时影响别的口径（宿舍等级 → 基础回复、中枢等级 → 能放几个人 → 全基建减免），
    所以这里把"这一级能放几个人"直接写在选项旁边。
    """

    def __init__(self, parent, name: str, current: int, max_level: int, slots_of):
        super().__init__(parent, bg=theme.BG)
        self.title(f"房间等级 · {name}")
        self.resizable(False, False)
        self.result = None
        self._max = max(1, int(max_level))

        tk.Label(self, text=name, bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_TITLE)).pack(anchor="w", padx=theme.PAD,
                                                                pady=(theme.PAD, 2))
        tk.Label(self, text="等级决定能放几个人（上游 rooms[].phases[].maxStationedNum）；"
                            "宿舍等级还决定基础回复速率，中枢等级决定中枢能站几个人"
                            "（进而决定全基建的心情减免）。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=420,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.PAD)

        self.var = tk.IntVar(value=max(1, min(int(current), self._max)))
        box = tk.Frame(self, bg=theme.BG)
        box.pack(fill="x", padx=theme.PAD, pady=theme.GAP)
        for lv in range(1, self._max + 1):
            ttk.Radiobutton(box, value=lv, variable=self.var,
                            text=f"Lv{lv}（可放 {slots_of(lv)} 人）").pack(anchor="w")

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=(0, theme.PAD))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="确定", style="Accent.TButton", command=self._ok).pack(
            side="right", padx=(0, 6))
        self.bind("<Escape>", lambda _e: self.destroy())
        _modal(self, parent)

    def _ok(self) -> None:
        self.result = int(self.var.get())
        self.destroy()


def ask_level(parent, name: str, current: int, max_level: int, slots_of) -> Optional[int]:
    """返回新的等级；取消返回 None。"""
    dlg = LevelDialog(parent, name, current, max_level, slots_of)
    parent.wait_window(dlg)
    return dlg.result


class IdleToDormMixin:
    """**闲置入宿**设置 —— 一个总开关 + 一张"候选人一行"的表。

    规则（框内也写给用户看）：把"**没在上班、也不在宿舍、心情还没满**"的干员安排进宿舍——
    宿舍有空位就直接放进去（氛围高的优先）；没空位就**与宿舍里心情已满的那位互换**
    （她进宿舍恢复，那位换出来闲置——他已是满心情，闲置不会掉心情）。

    | 控件 | 落到引擎 |
    |---|---|
    | ① 启用闲置入宿 | `IdleToDormConfig.enabled` |
    | 每行的「参与」 | `per_operator[(周期,班次,干员)].enabled` |
    | 每行的「换谁」 | `per_operator[(周期,班次,干员)].swap_with`（自动 = `None`） |

    **表格按时间排**（第 1 周期第 1 班 → … → 第 1 周期第 3 班 → 第 2 周期第 1 班 → …），
    每个"周期 × 班次"一组、组头写明时刻区间、组内只放那一刻**真的有候选**的人；
    「换谁」下拉**只列那一刻在宿舍且心情满的人**（每次可选的人都不一样）。

    ⚠️ 改动会**实时生效**：每次勾选 / 改目标都会回调 `on_change(状态)` ——
    调用方（`ui.app`）把它套进模拟重算并返回**新的分组表**，本面板据此重建表格
    （因为改动会影响后面每一次的候选）。取消时由调用方回滚。

    ⚠️ 本类**只建控件、只收状态**，自己不是窗口：宿主是 `IdleToDormDialog`（独立对话框）
    或 `ui.settings` 的设置中心分区（Frame）。
    """

    TITLE = "闲置入宿设置（未满的闲置干员进宿舍）"
    AUTO = "自动（挑宿舍里满心情的一位）"
    REBUILD_MS = 250              # 改动后的防抖：连续点几下只重算一次

    def _init_idle_body(self, parent, enabled: bool, groups: Sequence,
                        on_change=None, note: str = ""):
        """把状态收好并建出整块控件（宿主的 `__init__` 里调用；`self` 必须是 tk 容器）。"""
        self.result = None
        # { (周期, 班次, 干员): (参与, 换谁 或 None) } —— 面板里的"当前状态"（源真源）
        self.state: dict = {}
        self._groups = list(groups)
        self._on_change = on_change
        self._rows: list = []          # [(周期, 班次, 干员, 参与 BooleanVar, 换谁 StringVar)]
        self._widgets: list = []       # ① 关掉时要置灰的控件
        self._job = None
        self._busy = False
        pad = dict(padx=theme.PAD)

        tk.Label(self, text="闲置入宿 = 把没在上班、也不在宿舍、心情还没满的干员安排进宿舍恢复。",
                 bg=theme.BG, fg=theme.TEXT, justify="left", wraplength=600,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(anchor="w", **pad,
                                                               pady=(theme.PAD, 2))
        tk.Label(self,
                 text="规则：先看宿舍有没有空位，有空位就直接放进去（氛围高的宿舍优先）；\n"
                      "没空位就与宿舍里【心情已满】的那位互换——她进宿舍恢复，那位换出来闲置\n"
                      "（他已经是满心情，闲置不会掉心情）。宿舍里连一个满心情的都没有时，"
                      "这一班就不动。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=600,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad,
                                                                pady=(0, theme.GAP))

        self.enabled = tk.BooleanVar(value=bool(enabled))
        ttk.Checkbutton(self, text="① 启用闲置入宿（每班开始时结算一次）",
                        variable=self.enabled, command=self._on_toggle).pack(anchor="w", **pad)

        tk.Label(self, text="② 逐次设置（从早到晚；「去哪／与谁换」可选【那一刻有空位的宿舍】"
                            "（宿舍01、宿舍02…＝放进那间宿舍的空位）或【那一刻在宿舍且满心情的人】"
                            "（与他互换，他换出来闲置）；改动立即生效）",
                 bg=theme.BG, fg=theme.TEXT, padx=theme.PAD, justify="left",
                 wraplength=640, anchor="w").pack(anchor="w", pady=(theme.GAP, 2))
        self._build_table()

        if note:
            tk.Label(self, text=note, bg=theme.BG, fg=theme.MUTED, justify="left",
                     wraplength=600, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                     ).pack(anchor="w", **pad)
        self._sync()

    # ------------------------------------------------------------ 表格
    def _build_table(self) -> None:
        """可滚动的分组表（结构固定，内容随 `self._groups` 重建）。"""
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD)
        self.canvas = tk.Canvas(body, bg=theme.PANEL, highlightthickness=1,
                                highlightbackground=theme.BORDER, height=420)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=theme.PANEL)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self._fill_table()
        tk.Label(self, text="「宿舍01」＝第 1 间宿舍（放进它最靠前的空位，不动任何人）；"
                            "选一个人名＝与他互换（他已是满心情，换出来闲置不会掉心情）。"
                            "指定的那间那一刻已经满了 / 那个人不在宿舍或不满心情 → 跳过这一位。",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=640, anchor="w",
                 padx=theme.PAD).pack(anchor="w", pady=(2, 0))

    def _fill_table(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self._rows = []
        self._widgets = []
        if not self._groups:
            tk.Label(self.inner, text="（当前设置下没有「未满且在闲置」的干员）", bg=theme.PANEL,
                     fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                     ).pack(anchor="w", padx=6, pady=6)
            return
        for title, scope, rows in self._groups:
            head = tk.Frame(self.inner, bg=theme.PANEL_ALT)
            head.pack(fill="x", pady=(2, 0))
            tk.Label(head, text=title, bg=theme.PANEL_ALT, fg=theme.TEXT, anchor="w",
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=6)
            for text, value in (("全选", True), ("全不选", False)):
                btn = ttk.Button(head, text=text, width=6,
                                 command=lambda v=value, s=scope: self._set_group(s, v))
                btn.pack(side="right", padx=(0, 4))
                self._widgets.append(btn)
            for name, mood_text, where, _use_d, target_d, targets in rows:
                row = tk.Frame(self.inner, bg=theme.PANEL)
                row.pack(fill="x", padx=4, pady=ROW_PAD)
                tk.Label(row, text=name, bg=theme.PANEL, fg=theme.TEXT, width=13, anchor="w",
                         font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
                tk.Label(row, text=mood_text, bg=theme.PANEL, fg=theme.MUTED, width=7,
                         anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
                tk.Label(row, text=where, bg=theme.PANEL, fg=theme.MUTED, width=12, anchor="w",
                         font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
                key = (scope[0], scope[1], name)
                use_d, target_d = self.state.get(key, (True, None))
                use = tk.BooleanVar(value=bool(use_d))
                chk = tk.Checkbutton(row, text="", variable=use, bg=theme.PANEL,
                                     activebackground=theme.PANEL, highlightthickness=0,
                                     command=self._schedule_rebuild)
                chk.pack(side="left", padx=(6, 0))
                who = tk.StringVar(value=(target_d or self.AUTO))
                cb = ttk.Combobox(row, textvariable=who, state="readonly", width=20,
                                  values=[self.AUTO] + list(targets))
                cb.pack(side="left", padx=(4, 0))
                cb.bind("<<ComboboxSelected>>", lambda _e: self._schedule_rebuild())
                self._rows.append((key, use, who, chk, cb))
                self._widgets.extend([chk, cb])
        self._sync()
        self.canvas.yview_moveto(0)

    # ------------------------------------------------------------ 交互
    def _collect(self) -> None:
        """把控件里的当前值收回 `self.state`。"""
        for key, use, who, _c, _b in self._rows:
            target = who.get().strip()
            self.state[key] = (bool(use.get()),
                               None if target in ("", self.AUTO) else target)

    def _on_toggle(self) -> None:
        """① 总开关：置灰整张表并实时重算。"""
        self._sync()
        self._schedule_rebuild()

    def _set_group(self, scope, value: bool) -> None:
        """某一组（某次进驻）的全选 / 全不选。"""
        for key, use, _w, _c, _b in self._rows:
            if key[:2] == tuple(scope):
                use.set(bool(value))
        self._schedule_rebuild()

    def _schedule_rebuild(self) -> None:
        """防抖：连续改动只重算一次（重算约 0.3s）。"""
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = self.after(self.REBUILD_MS, self._rebuild_from_timer)

    def _rebuild_from_timer(self) -> None:
        """定时器到点：先把 job id 清掉（这样 `destroy()` 不会去取消一个已经跑完的任务）。"""
        self._job = None
        self._rebuild()

    def _rebuild(self) -> None:
        """把当前状态交给调用方重算，并用返回的新分组表重建表格。"""
        self._collect()
        if self._on_change is not None:
            groups = self._on_change(bool(self.enabled.get()), dict(self.state))
            if groups is not None:
                self._groups = list(groups)
        if self.winfo_exists():
            self._fill_table()

    def _sync(self) -> None:
        """关掉总开关时把整张表置灰。"""
        on = bool(self.enabled.get())
        for w in self._widgets:
            try:
                w.state(["!disabled"] if on else ["disabled"])
            except (tk.TclError, AttributeError):
                w.configure(state="normal" if on else "disabled")

    def _cancel_job(self) -> None:
        """取消还没跑的重建任务（否则会对着已销毁的控件报 invalid command name）。"""
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def value(self):
        """收成 `(enabled, {(周期, 班次, 干员): (参与, 换谁)})`。"""
        self._collect()
        return (bool(self.enabled.get()), dict(self.state))


class IdleToDormPanel(tk.Frame, IdleToDormMixin):
    """「闲置入宿」设置**内容本体**（设置中心「闲置入宿」分区）。

    改动经 `on_change` **实时生效**（防抖 250ms）；面板不需要"应用"
    （关掉设置中心即接受），也没有"取消回滚"。
    """

    def __init__(self, master, enabled: bool, groups: Sequence,
                 on_change=None, note: str = ""):
        super().__init__(master, bg=theme.BG)
        self._init_idle_body(master, enabled, groups, on_change=on_change, note=note)

    def destroy(self) -> None:
        """销毁时取消还没跑的重建任务（否则会对着已销毁的控件报 invalid command name）。"""
        self._cancel_job()
        tk.Frame.destroy(self)
