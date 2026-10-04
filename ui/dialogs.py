"""ui/dialogs.py —— 四个小对话框：选人 / 设心情 / 班次时长设置 / 进驻事件设置。

都做成"模态 + 返回结果"的简单函数（`ask_*` → 值或 None），调用方（`app.py`）不必关心细节。
键盘优先：选人框回车即确认、Esc 取消；设心情框支持 `0/6/12/18/24` 快捷按钮。

进驻事件设置（`EntryEventDialog`）：一个总开关 + **一张"每班一行"的表**
（`用 / 换谁 / 强制切换`）——真正逐班的就是这一整组，所以不再分"全局值 + 例外"两层。
批量改干员与心情在 `ui/batch.py`。
"""
from __future__ import annotations

import re
import tkinter as tk
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from tkinter import messagebox, ttk
from typing import List, Optional, Sequence

from mood_soc import entry_target_kind
from mood_soc.models import normalize_entry_when

from . import theme
from .scroll import VScroll

# 「设置」中心里各分区共用的排版栅格：标签列宽（右对齐）
LABEL_W = 12

# 心情输入的合法范围（周期起点心情．引擎侧同样钳位 [0, 24]）
MOOD_MIN_TEXT = Decimal("0")
MOOD_MAX_TEXT = Decimal("24")

# 表格行的上下留白（「闲置入宿」那一张分组表用）
ROW_PAD = 1

# 「闲置入宿」页的两栏布局（③ 手动入宿 ∥ ④ 逐次表，见 `_init_idle_body`）：
# 左栏固定这么宽，右栏吃剩下的；
# `IDLE_TABLE_H_INIT` 是表格的**初始可视高度**（建表格之前只能给个估值，
# 见 `_resolve_table_height`）；建表之后由它自己的 `_fit_table_height()` 自校正。
#
# ⚠️ **左栏 380 是怎么定的**（改前 330 ⇒ 位次格子横排 5 个被裁掉大半，见
#    `SLOT_COLS` 那段注释）：左栏里最宽的一行是那 5 个位次格子，
#    而列数由 `SLOT_COLS` 静态算出来 ⇒ 380 必须**够 2 列**、又**不许把右栏压到不能用**。
#    实测（`_scratch/probe_manual.py`）：`slots_frame` 的可用内宽 = `IDLE_MANUAL_W - 28`
#    （`theme.PAD` 10×2 ＋ LabelFrame 的边框与标签），2 列要 `2×146 = 292` ⇒ 380-28 = 352 ✔；
#    右栏随后仍剩 ~430px（逐次表是"干员 / 心情 / 参与 / 说明"四段流式 `pack`，
#    430 够一行放下前两段 + 说明的截断版）。
IDLE_MANUAL_W = 380
IDLE_TABLE_H_INIT = 320
MIN_TABLE_H = 190

# 一格逐位控件（`第 N 位` + 人名按钮）的请求宽，**实测值**、不是估的：
# 2026-10 删掉每格的 `☑ 锁` 之前它量到 `144x35`（`Label width=4` 34 ＋ Button 74
# ＋ Checkbutton 34 ＋ 两次 `padx=(1,1)`）；删掉 Checkbutton、并把空位按钮的文案换成
# `SLOT_EMPTY_TEXT`（按钮 `width=11`，要放得下「（空位 · 点这里选人）」）之后，
# `_scratch/probe_manual.py` 量到 5 格全是 `150x35`，加上 `_build_slots` 的
# `padx=(0, 2)` ⇒ **152/列**（于是 380 的栏排 **2 列 3 行**，与删锁之前一样）。
# 改这一格的控件（多一个字、换字号、换按钮宽）就要重量一遍并同步这个常量
# —— `SLOT_COLS` 由它算出来，量错了格子就又会被裁。
SLOT_CELL_W = 152

# 空位的按钮文案（**保留可点引导**：这一页唯一的"摆位"入口，不能只剩一个空框）。
# 有人时按钮显示人名。见 `_build_slots` / `_set_slot_button`。
SLOT_EMPTY_TEXT = "（空位 · 点这里选人）"

# 「③ 手动入宿」那行提示的**固定口径**（`IdleToDormMixin._notice_text` 拼在动态那句后面）：
# `MANUAL_CAVEAT` 是**界面上看得见**的那半句（≤26 字）；
# `MANUAL_CAVEAT_HINT` 是它挂的**悬停提示全文**（口径一字不丢，原文见
# `documents/10-图形界面.md` §6.2）。两处**必须一起改**。
# 为什么不全写在界面上：整行原先 84 字、占 3 行 74px，把半栏的纵向空间吃掉了。
#
# ⚠️ 2026-10 **口径反转**（用户原话：「放上去之后自动上锁。**不需要手动上锁**」
#    「解锁时只需要**将该位置空**就可以了」「锁功能只作为内部自动入宿进行位置判定时使用，
#    而**不对外输出暴露**」）：上一轮定的是「清空即上锁」，现在改成
#    **「摆位即上锁、清空即解锁」** —— 所以旧那半句「取消 ☑ = 交还自动入宿」已经不成立
#    （逐位 `☑ 锁` 与「全部解锁」整块控件都删掉了），新那句是「清空该位 = 交还自动入宿」。
MANUAL_CAVEAT = "放上去即锁定；清空该位 = 交还自动入宿"
MANUAL_CAVEAT_HINT = ("把某人放进某个位次**即自动上锁**（自动入宿从此不占这一位、不换她）；"
                      "**把该位置空**＝那一位交还自动入宿（人留在原位时可被换出）。"
                      "锁只在**内部**（自动入宿判定位置）用，界面上不再暴露 —— "
                      "没有逐位 `☑ 锁`、也没有「全部解锁」按钮。"
                      "（程序接口仍保留 `set_seat_lock` / `clear_seat_locks`："
                      "API 可把一个**空位**单独锁住，那是「预留空位」的能力。）")

# 逐次表「说明」列的**显示截断**：引擎给的原文可达 109~120 字，不截的话每行高矮不齐、
# 一屏只看得到三四行。⚠️ 只截**显示**（`_clip_note`），`Trajectory.idle_note_at`
# 与任何引擎侧字符串都不动；完整原文挂 `attach_hint` 的悬停提示。
NOTE_CLIP = 30


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


# 「说明」列里那串心情小数的显示位数：引擎是 Decimal 精确运算，跨事件分割会留下
# `20.10000000000000000000000001` 这类 28 位尾巴 —— 那是精度极限、不是算错，
# 只在**显示边界**舍入（口径与 `ui.theme.fmt_mood` 一致：0.01 精度、去掉多余的 0）。
_NOTE_NUM = re.compile(r"\d+\.\d{3,}")
_NOTE_PLACES = Decimal("0.01")


def _tidy_note(note) -> str:
    """把「说明」列里那串 28 位心情小数截到 2 位（`20.1000…01` → `20.1`）。

    ⚠️ 只动**小数点后 ≥3 位**的数字（`_NOTE_NUM`）：位次（`第 5 位`）与整点心情
    （`心情 24`）不匹配、原样留着；`2` 位以内的正常值也不动。
    """
    text = (note or "").strip()
    if not text:
        return "—"

    def _short(m: "re.Match") -> str:
        v = Decimal(m.group(0)).quantize(_NOTE_PLACES, rounding=ROUND_HALF_UP)
        return f"{v:f}".rstrip("0").rstrip(".")

    return _NOTE_NUM.sub(_short, text)


def _clip_note(text: str, limit: int = NOTE_CLIP) -> str:
    """把「说明」列的**显示**压到一行（超出 `limit` 字就截断 + `…`）。

    ⚠️ **只截显示**：引擎给的原文（`Trajectory.idle_note_at`）一字不动，完整那句由
    调用方挂到悬停提示上（`_hint`）。截断判据是**字数**而不是像素：`tk.Label` 在这里
    是流式 `pack`、可以用 `wraplength` 折行，但折行会让每行高矮不齐 —— 这一列要的是
    "每行一行高"，所以按字数切。
    """
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _hint(widget, text) -> None:
    """给控件挂悬停提示 —— **复用 `ui/batch.attach_hint`**（别在 `ui/` 里另写一套 tooltip）。

    ⚠️ 导入放在函数里：`ui/batch.py` 顶层 `from ui.dialogs import ...` 反向依赖本模块，
    顶层再 import 回去就成环（`ui/settings.py` 把两者凑到一起时就炸）。悬停提示是
    **运行时**才需要的东西，函数内 import 既不成环、也不会多付导入代价。
    """
    from ui.batch import attach_hint
    attach_hint(widget, text)


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

        # 栅格：标签右对齐 + 输入框等宽（与设置中心别的分区同一套排版）
        grid = tk.Frame(self, bg=theme.BG)
        grid.pack(fill="x", pady=(theme.GAP, 0))
        grid.columnconfigure(1, minsize=90)
        tk.Label(grid, text="周期", bg=theme.BG, fg=theme.TEXT, width=LABEL_W, anchor="e",
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).grid(row=0, column=0, sticky="e",
                                                               pady=2)
        self.cycle_var = tk.StringVar(value=theme.fmt_mood(cycle, 3))
        ttk.Entry(grid, textvariable=self.cycle_var, width=8).grid(row=0, column=1, padx=8,
                                                                   sticky="w")
        tk.Label(grid, text="小时", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).grid(row=0, column=2, sticky="w")
        ttk.Button(grid, text="均分", command=self._even).grid(row=0, column=3, padx=(8, 0))

        self.rows: List[tk.StringVar] = []
        for i, (label, h) in enumerate(zip(self._labels, hours)):
            row = i + 1
            tk.Label(grid, text=label, bg=theme.BG, fg=theme.TEXT, width=LABEL_W, anchor="e",
                     font=(theme.FONT_FAMILY, theme.FS_BODY)).grid(row=row, column=0, sticky="e",
                                                                    pady=2)
            var = tk.StringVar(value=theme.fmt_mood(h, 3))
            var.trace_add("write", lambda *_a: self._validate())
            ttk.Entry(grid, textvariable=var, width=8).grid(row=row, column=1, padx=8, sticky="w")
            tk.Label(grid, text="小时", bg=theme.BG, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).grid(row=row, column=2, sticky="w")
            self.rows.append(var)
        self.cycle_var.trace_add("write", lambda *_a: self._validate())

        self.msg = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.msg.pack(fill="x", pady=(theme.GAP, 0))
        # ⚠️ 构建时只**报告有效性**、不回调"改动"：`_validate()` 末尾会 `_notify()`，而
        #    `on_change` 是"用户改了时长"的落地口子（`app.apply_shift_hours` → 重算 +
        #    **把当前时刻重置为 0:00**）。构建时也回调的话，"打开时间轴页"就会把主界面
        #    时刻冲成 00:00（实测过）——「跟随滑块」看起来像在乱跳，还白重算一轮。
        self._building = True
        self._validate()
        self._building = False

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
        if getattr(self, "_building", False):
            return                    # 构建时只报有效性（见 `__init__` 末尾的说明）
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
        # —— 页内说明只留"会影响操作结果"的；整段口径原文见 documents/10-图形界面.md §6.2 ——

        # ① 总开关
        self.enabled = tk.BooleanVar(value=bool(enabled))
        ttk.Checkbutton(self, text="① 开启心情交换（进驻宿舍那一刻，与她互换心情）",
                        variable=self.enabled, command=self._on_enabled).pack(anchor="w", **pad)

        holders = "、".join(current_holders) if current_holders else "（本排班里没有）"
        # ⚠️ 两行都**必须短**（`side="left"` 的标签不给 `wraplength` 会按整句要宽度）。
        #    "触发者是谁"会影响预期（没这个人就什么都不会发生），所以留；
        #    "勾了「用」的班次才换"是**会拦住操作**的口径，也留。其余搬文档。
        tk.Label(self, text=f"触发者：{holders}　｜　仅对勾选「用」的班次生效",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=520,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad, pady=(2, 2))
        tk.Label(self, text="「换谁」自带范围：前一位进驻＝同宿舍，其余＝基建任意位置",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=520,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad,
                                                               pady=(0, theme.GAP))

        # ② 一张表：每个班次自己的「用 / 换谁 / 强制切换」（没有"全局值 + 例外"两层）
        self._build_shift_table()

        tk.Label(self, text="「强制切换」勾上＝等她回满再换；不勾＝判定时没满就不换",
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
            bg = theme.zebra(i)                     # 隔行底色
            row = tk.Frame(box, bg=bg)
            row.pack(fill="x", padx=theme.GAP, pady=(2, 0))
            tk.Label(row, text=f"{i + 1}. {label}", bg=bg, fg=theme.TEXT, width=24,
                     anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
            use = tk.BooleanVar(value=use_d)
            chk = tk.Checkbutton(row, text="", variable=use, bg=bg,
                                 activebackground=bg, highlightthickness=0,
                                 command=self._notify)
            chk.pack(side="left", padx=(4, 0))
            who = tk.StringVar(value=who_d)
            box_who = ttk.Combobox(row, textvariable=who, state="readonly", values=values,
                                   width=22)
            box_who.pack(side="left", padx=(4, 6))
            box_who.bind("<<ComboboxSelected>>", lambda _e: self._notify())
            force = tk.BooleanVar(value=force_d)
            chk_force = tk.Checkbutton(row, text="", variable=force, bg=bg,
                                       activebackground=bg, highlightthickness=0,
                                       command=self._notify)
            chk_force.pack(side="left", padx=(4, 0))
            self.shift_use.append(use)
            self.shift_who.append(who)
            self.shift_force.append(force)
            self._shift_widgets.extend([chk, box_who, chk_force])
        bar = tk.Frame(box, bg=theme.BG)
        bar.pack(fill="x", padx=theme.GAP, pady=(4, 6))
        for text, value in (("全选", True), ("全不选", False)):
            btn = ttk.Button(bar, text=text, command=lambda v=value: self._set_all_shifts(v))
            btn.pack(side="left", padx=(0, 6))
            self._shift_widgets.append(btn)
        # （原先这里还有一段 49 字的"第 1 班就是默认口径…"：纯解释，已搬
        #   `documents/10-图形界面.md` §6.2）

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
    """**闲置入宿**设置 —— 总开关 + 全局口径（锁定位置数 / 黑名单）+ 一张"候选一行"的逐次表。

    引擎规则（`mood_soc/rules.apply_idle_to_dorm`；三层解耦：**手动编辑 > 自动入宿 > 导入布局**）：

    | 层 | 谁 | 本面板管不管 |
    |---|---|---|
    | ① 手动编辑 | 看板 /「干员与心情」/ 本页的**③ 手动入宿**子面板把某人放进某个宿舍位次（写班次快照 + 手动台账） | **管**：`③ 手动入宿`（勾班次 → 选宿舍 → 逐位选人；**摆位即上锁、清空即解锁**） |
    | ② 自动入宿 | 竖向正序填空床 → 全满则取**心情最低**的候选，换出"锁定区外、心情 ≥ 她、心情最大"的住户 | 本面板显示它这一刻打算安排谁 |
    | ③ 全局配置 | 总开关 / 锁定位置数 / 黑名单 / 逐人"不参与" | ① ② 与 ④ 那张逐次表 |

    | 控件 | 落到引擎 |
    |---|---|
    | ① 启用闲置入宿 | `IdleToDormConfig.enabled` |
    | ② 锁定位置数 | `IdleToDormConfig.protected_slots`（按竖向正序锁前 N 个位置） |
    | ② 黑名单 | `IdleToDormConfig.blacklist`（永远不能**通过闲置入宿进宿舍**的人） |
    | 每行的「参与」 | `per_operator[(周期,班次,干员)].enabled` |
    | ③ 手动入宿的每格 | `facilities[].slots` + `manual`（只走 `set_facility_slots`） |

    **逐次表**按时间排（第 1 周期第 1 班 → …），**一个换班执行点一组**：真实班初一组，
    长班（> 12h）的每个**内部换班点**各一组（标题带 `（12h 内部换班）`）；组内只放那一刻
    **真的有候选**的人；每行只有「参与」可改，另给一列**只读提示**（这一位会被安排去哪、
    或为什么没安排）。⚠️ **同班各执行点共用同一份逐人设置**：组里的 `(周期, 班次, 干员)`
    键相同 ⇒ 改任一组会同步影响同班其他执行点。

    ⚠️ **手动入宿现在就在这一页**（**③ 手动入宿**子面板，2026-10）：勾班次 → 选宿舍 →
    逐位选人；「干员与心情」的位置列与看板仍是另一条入口（写的都是同一份台账）。
    两者都**只写布局 + 手动台账**。

    ⚠️ **锁在界面上不再暴露**（2026-10 用户口径：「放上去之后自动上锁。**不需要手动上锁**」
    「解锁时只需要**将该位置空**就可以了」「锁功能只作为内部自动入宿进行位置判定时使用，
    而**不对外输出暴露**」）：逐位 `☑ 锁`、「全部解锁…」按钮都已删除 ——
    **摆位即上锁、清空该位即解锁**。`Session.set_seat_lock` / `clear_seat_locks` 仍保留
    给程序接口（`api/`）：API 可以把一个**空位**单独锁住（"预留空位"）。

    ⚠️ 改动会**实时生效**：每次改动 / 改锁定数 / 改黑名单都会回调 `on_change(状态)` ——
    调用方（`ui.app`）把它套进模拟重算并返回**新的分组表**，本面板据此重建表格。

    ⚠️ 本类**只建控件、只收状态**，自己不是窗口：宿主是设置中心的「闲置入宿」分区（Frame）。
    """

    TITLE = "闲置入宿设置（未满的闲置干员进宿舍）"
    REBUILD_MS = 250              # 改动后的防抖：连续点几下只重算一次

    def _init_idle_body(self, parent, enabled: bool, groups: Sequence,
                        on_change=None, note: str = "", table_height="auto",
                        page_height: int = 0, groups_provider=None,
                        protected_slots: int = 5, blacklist: Sequence = (),
                        all_names: Sequence = (), state_provider=None,
                        on_manual=None, operator_names: Sequence = ()):
        """把状态收好并建出整块控件（宿主的 `__init__` 里调用；`self` 必须是 tk 容器）。

        `groups_provider`：无参可调用，返回**当前**分组表（设置中心传 `app.idle_groups`）。
        异步重算落地后由 `refresh_from_provider()` 用它取新表 —— 见那个方法。
        `protected_slots` / `blacklist`：全局口径的初值（来自 `Session`）。
        `all_names`：可以加入黑名单的干员名（下拉的候选池）。
        `state_provider` / `on_manual` / `operator_names`：
        **手动入宿编辑器**（③ 区）要的三样 ——
        `state_provider(班次下标)` ← `Session.manual_dorm_editor_state`（只读数据源）；
        `on_manual(请求)` ← `app.apply_manual_dorm`（唯一的写入口，返回一句结果文案）；
        `operator_names`：选人对话框的候选全表。

        ⚠️ 2026-10 删掉了这里的 `locked_probe` / `session` / `on_after` 三个参数（连同
        `SettingsDialog._shift_has_manual`）——它们只服务「全部解锁…」按钮，而锁已按用户口径
        **不再对外暴露**（`set_seat_lock` / `clear_seat_locks` 仍留在 `Session` 给 API 用）。
        """
        self.result = None
        # 表格高度：显式数字（独立对话框）或 "auto"（设置中心：吃内容区剩余高度）
        self._table_h = None if table_height == "auto" else int(table_height)
        self._page_h = int(page_height) or 0
        # { (周期, 班次, 干员): 参不参与 } —— 面板里的"当前状态"（源真源；只记改过的）
        self.state: dict = {}
        self._groups = list(groups)
        self._on_change = on_change
        self._groups_provider = groups_provider
        # [(周期, 班次, 干员, 参与 BooleanVar, [用户改过?])] ——
        # ⚠️ 最后那个是**单元素列表**（可变标记）：同班的多个执行点共用同一个键，
        #    收状态时必须优先采用"用户刚动过"的那一行，否则会被同键的其它行盖回去。
        self._rows: list = []
        self._widgets: list = []       # ① 关掉时要置灰的控件
        self._job = None
        self._busy = False
        self._all_names = [str(n) for n in all_names]
        self.blacklist: List[str] = [str(n) for n in blacklist]
        self._protected_cache = max(0, int(protected_slots))
        # —— 手动入宿编辑器（③ 区）的状态 ——
        self._state_provider = state_provider
        self._on_manual = on_manual
        self._op_names = [str(n) for n in operator_names]
        self._room_names: dict = {}        # {班次下标: {设施下标: 宿舍显示名}}
        self._slot_btns: dict = {}         # {(班次下标, 设施下标, 位次): 人名按钮}
        # 「宿舍」下拉的**标签 → 设施下标**映射（2026-10 修下拉取值：变量存标签，
        #   `_current_dorm` 靠它取下标；见 `_refresh_manual_state`）
        self._dorm_label_to_index: dict = {}
        self._two_col = None               # 两栏容器 (左, 右, 容器)；单列宿主保持 None
        pad = dict(padx=theme.PAD)

        # ⚠️ 这一行只留"谁会被安排"（会影响预期）；完整的候选/换人口径搬
        #    `documents/10-图形界面.md` §6.2（原先这里是 174 字一段，白占 74px）。
        tk.Label(self, text="没上班、没在宿舍、心情未满的人进宿舍",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=700,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", **pad,
                                                                pady=(0, theme.GAP))

        self.enabled = tk.BooleanVar(value=bool(enabled))
        ttk.Checkbutton(self, text="① 启用闲置入宿（每个换班执行点结算）",
                        variable=self.enabled, command=self._on_toggle).pack(anchor="w", **pad)

        # ---- ② 全局口径：锁定位置数 + 黑名单 ----
        lock_row = tk.Frame(self, bg=theme.BG)
        lock_row.pack(fill="x", **pad)
        tk.Label(lock_row, text="② 锁定位置数", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.protected = tk.StringVar(value=str(self._protected_cache))
        spin = tk.Spinbox(lock_row, from_=0, to=999, width=5, textvariable=self.protected,
                          command=self._schedule_rebuild)
        spin.pack(side="left", padx=(4, 6))
        spin.bind("<KeyRelease>", lambda _e: self._schedule_rebuild())
        self._widgets.append(spin)
        # ⚠️ 这一行里的说明**必须短**：`side="left"` 的标签不给 `wraplength` 时按整句文字要宽度，
        #    会把整块面板撑过内容区（回归 `test_四个分区都装得进固定内容区`：曾量到 906 > 816）。
        #    细节写在上面那段规则说明里（它有 `wraplength`）。
        tk.Label(lock_row, text="（竖向正序前 N 个位置；锁定区里的人自动不换）",
                 bg=theme.BG, fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                 ).pack(side="left")

        black_row = tk.Frame(self, bg=theme.BG)
        black_row.pack(fill="x", **pad)
        tk.Label(black_row, text="黑名单", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.black_pick = tk.StringVar()
        self.black_combo = ttk.Combobox(black_row, textvariable=self.black_pick,
                                        state="readonly", width=12,
                                        values=list(self._all_names))
        self.black_combo.pack(side="left", padx=(6, 4))
        add_btn = ttk.Button(black_row, text="＋ 加入", width=8, command=self._add_blacklist)
        add_btn.pack(side="left")
        del_btn = ttk.Button(black_row, text="移除选中", width=10, command=self._remove_blacklist)
        del_btn.pack(side="left", padx=(4, 6))
        self._widgets.extend([self.black_combo, add_btn, del_btn])
        tk.Label(black_row, text="（不能通过闲置入宿进宿舍）",
                 bg=theme.BG, fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                 ).pack(side="left")
        self.black_list = tk.Listbox(self, height=4, activestyle="none",
                                     bg=theme.PANEL, fg=theme.TEXT, highlightthickness=1,
                                     highlightbackground=theme.BORDER, exportselection=False,
                                     font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.black_list.pack(fill="x", **pad)
        self._widgets.append(self.black_list)
        self._refresh_blacklist()

        # ================= ③ 手动入宿 ∥ ④ 逐次表：**左右并排** =================
        # 为什么并排：这一页纵向最紧（改前自然高 728 > 内容区 740 的 98%），
        # 而 ③ 只有 4 行逐位控件、④ 是一张要滚的表 —— 两栏各自用满高度，
        # 省下的正好是"两者相加"的那一份（改前 152 + 192 = 344px）。
        # ⚠️ 两栏都 `sticky="nsew"` + 行权 1 ⇒ 谁矮就自己留白，谁高就自己滚，
        #    **不许**把对方顶出去（左栏 `pack_propagate(False)` 固定宽度；右栏吃剩余）。
        cols = tk.Frame(self, bg=theme.BG)
        cols.pack(fill="both", expand=True, **pad)
        cols.columnconfigure(0, weight=0, minsize=IDLE_MANUAL_W)
        cols.columnconfigure(1, weight=1)
        cols.rowconfigure(0, weight=1)
        left = tk.Frame(cols, bg=theme.BG, width=IDLE_MANUAL_W)
        left.grid(row=0, column=0, sticky="nsew")
        left.pack_propagate(False)
        right = tk.Frame(cols, bg=theme.BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(theme.GAP, 0))
        self._two_col = (left, right, cols)

        self._build_manual_dorm(left)

        tk.Label(right, text="④ 逐次设置：每行只有「参与」可改；「说明」是只读的引擎结果",
                 bg=theme.BG, fg=theme.TEXT, padx=0, justify="left",
                 wraplength=IDLE_MANUAL_W + 60, anchor="w").pack(anchor="w", pady=(0, 2))
        self.table_height = self._resolve_table_height()
        self._build_table(right)

        if note:
            tk.Label(self, text=note, bg=theme.BG, fg=theme.MUTED, justify="left",
                     wraplength=600, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                     ).pack(anchor="w", **pad)
        self._sync()

    # ====================================================== ③ 手动入宿编辑器
    def _build_manual_dorm(self, parent) -> None:
        """**手动入宿编辑器**（本页最靠上的一层：手动编辑 > 自动入宿 > 导入布局）。

        `parent`＝两栏布局的**左栏**（固定宽度 `IDLE_MANUAL_W`，`pack_propagate(False)`）。

        形态：`③ 手动入宿` 一块 LabelFrame，四行 ——

        | 行 | 控件 | 语义 |
        |---|---|---|
        | ① | 班次 `☑` × N + 「全选 / 全不选」 | **勾中的班＝改动落地目标**，同时**第一个勾中的班就是显示的那一班**（2026-10 合并成一个班次控件，删掉了「正在看」下拉） |
        | ② | 宿舍下拉 | 变量存**标签**（`宿舍#1（5 位）`），`_current_dorm` 用 `_dorm_label_to_index` 取设施下标 —— 写入口吃下标。**绝不显示裸数字** |
        | ③ | 提示行 | 「改动落到哪几个班次」（动态）+ 固定口径（全文挂悬停提示） |
        | ④ | `第 N 位` ＋ `人名按钮` | 点人名开 `ask_operator`（含「清空该位置」）；**列数由 `SLOT_COLS` 按栏宽算出来** |

        ⚠️ **这里没有锁控件**（2026-10 用户口径）：逐位 `☑ 锁` 与「全部解锁…」都已删除 ——
        **摆位即上锁、清空该位即解锁**，锁只在引擎内部（自动入宿判定位置）用。
        `manual_dorm_editor_state` 里仍然给 `locked`（约束：字段不动），界面只是不显示它。
        """
        box = tk.LabelFrame(parent, text="③ 手动入宿（手动编辑 > 自动入宿）",
                            bg=theme.BG, fg=theme.TEXT, bd=1, relief="groove",
                            labelanchor="nw", font=(theme.FONT_FAMILY, theme.FS_SMALL))
        box.pack(fill="both", expand=True, padx=(theme.PAD, 0), pady=(0, theme.GAP))

        self.shift_pick: List[tk.BooleanVar] = []
        # ⚠️ 班次表来自**只读数据源**（`manual_dorm_editor_state` 的 `shifts`），
        #    不借 `EntryEventPanel._shift_labels`（那是另一个类的东西，本面板没有）。
        self._manual_shifts: List[tuple] = []
        self.manual_notice = tk.Label(box, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                                      justify="left", wraplength=IDLE_MANUAL_W - 24,
                                      font=(theme.FONT_FAMILY, theme.FS_SMALL))
        if not self._manual_state(0).get("shifts"):
            tk.Label(box, text="（还没有导入排班：导入后可以在这里逐位安排宿舍）",
                     bg=theme.BG, fg=theme.MUTED, wraplength=IDLE_MANUAL_W - 24,
                     justify="left",
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=theme.GAP,
                                                                    pady=(4, 6))
            self.manual_msg = self.manual_notice
            self.manual_notice.pack(fill="x", padx=theme.GAP, pady=(2, 6))
            return
        labels = [str(label) for _i, label in self._manual_shifts]
        # —— ① 班次多选（**自己一行**：左栏窄，横着塞会顶宽整块面板）——
        row1 = tk.Frame(box, bg=theme.BG)
        row1.pack(fill="x", padx=theme.GAP, pady=(4, 0))
        tk.Label(row1, text="班次", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(0, 4))
        for i, label in enumerate(labels):
            var = tk.BooleanVar(value=True)          # 默认全勾："改动落到所有班次"
            ttk.Checkbutton(row1, text=f"{i + 1}", variable=var,
                            command=self._on_shift_pick).pack(side="left")
            self.shift_pick.append(var)
        ttk.Button(row1, text="全选", width=5,
                   command=lambda: self._set_all_shift_pick(True)).pack(side="left", padx=(4, 0))
        ttk.Button(row1, text="全不选", width=6,
                   command=lambda: self._set_all_shift_pick(False)).pack(side="left", padx=(2, 0))
        # —— ② 宿舍下拉（第二行）——
        # ⚠️ 2026-10 **删掉了「正在看」下拉**（用户选"合并成一个班次控件"）：显示哪一班
        #    由 `_view_shift()` 取**第一个勾中的班**决定。原先那个下拉的病根是
        #    `values` 放显示标签、`textvariable` 存下标 ⇒ readonly 的 Combobox 显示的是
        #    **变量值**（裸数字），用户选第 2 班后 `_view_shift()` 仍返回 0 ⇒ **静默改错班次**。
        row2 = tk.Frame(box, bg=theme.BG)
        row2.pack(fill="x", padx=theme.GAP, pady=(2, 0))
        tk.Label(row2, text="宿舍", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.dorm_var = tk.StringVar()
        self.dorm_combo = ttk.Combobox(row2, textvariable=self.dorm_var, state="readonly",
                                       width=18, values=[])
        self.dorm_combo.pack(side="left", padx=(4, 0))
        self.dorm_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_dorm_pick())

        # —— ③ 提示行（「改动落到哪些班次」+ 口径说明）——
        # ⚠️ 2026-10 口径＝**摆位即上锁、清空即解锁**（旧那半句"取消 ☑ = 交还"已不成立）。
        #    可见的那半句**压到 ≤26 字**（`MANUAL_CAVEAT`，见模块常量）；完整口径（含"人留在
        #    原位、可被换出"与"锁不再对外暴露"）挂在**悬停提示**上（`ui/batch.py: attach_hint`，
        #    别另写一套 tooltip）。
        self.manual_notice.pack(fill="x", padx=theme.GAP, pady=(2, 4))
        _hint(self.manual_notice, self._notice_hint)

        self.slots_frame = tk.Frame(box, bg=theme.BG)
        self.slots_frame.pack(fill="x", padx=theme.GAP, pady=(4, 6))

        self.manual_msg = self.manual_notice
        self._refresh_manual_state()
        self._build_slots()

    def _manual_state(self, shift_index: int) -> dict:
        """取某一班的只读编辑数据（`manual_dorm_editor_state`）；没接数据源时给空壳。

        ⚠️ 顺手把顶层 `shifts`（班次表）填进 `self._manual_shifts` —— 它是这份只读数据的
        一部分，**不要**去借别的面板的 `_shift_labels`（曾 AttributeError）。
        """
        if self._state_provider is None:
            return {"shift": int(shift_index), "dorms": [], "shifts": []}
        state = self._state_provider(int(shift_index))
        if state.get("shifts"):
            self._manual_shifts = list(state["shifts"])
        return state

    def _checked_shifts(self) -> List[int]:
        """勾上的班次下标（写入口的 `shift_index` 列表）。"""
        return [i for i, v in enumerate(self.shift_pick) if bool(v.get())]

    def _view_shift(self) -> int:
        """**显示的那一班** ＝ **第一个勾中的班**（2026-10 合并成一个班次控件）。

        没勾任何班时返回 0（提示行走「一个班次都没勾」那一支，`_request` 也不落任何改动）。

        ⚠️ 旧实现读的是「正在看」下拉的**变量值**，而那个变量存的是**下标**、`values` 放的
        是显示标签 ⇒ readonly 的 Combobox 显示变量值（裸数字 `0`），用户选了「Shift 2 · 6h」
        之后 `_view_shift()` 仍返回 0 ⇒ **用户以为在改第 2 班、实际改的是第 1 班**
        （静默写错目标）。那个下拉已经删掉，显示哪一班完全由勾选决定。
        """
        checked = self._checked_shifts()
        return checked[0] if checked else 0

    def _current_dorm(self):
        """当前显示的那间宿舍的编辑数据（`dorm_var` 存的是**标签**，靠映射取下标）。

        ⚠️ 2026-10 修：原来这里是 `int(self.dorm_var.get())`，而变量里存的是**设施下标**、
        `values` 放的是显示标签 ⇒ 用户一旦从下拉里选「宿舍#2（5 位）」，变量变成那个标签、
        `int()` 转不动 ⇒ `want=-1` ⇒ **返回 None**，位次区变成「（这一班没有宿舍）」、
        **5 个位次按钮全没了**（实测缺陷）。现在走 `_dorm_label_to_index` 反查下标。
        """
        state = self._manual_state(self._view_shift())
        label = str(self.dorm_var.get() or "")
        want = self._dorm_label_to_index.get(label)
        if want is None:
            # 兜底：变量是空的 / 是历史遗留的裸下标串（旧会话）——两种都按"第 1 间"处理
            try:
                want = int(label)
            except (TypeError, ValueError):
                want = -1
        dorms = state.get("dorms", [])
        for d in dorms:
            if int(d["index"]) == int(want):
                return d
        return None

    def _on_shift_pick(self) -> None:
        """勾/取消一个班次：显示的那一班（＝第一个勾中的班）可能跟着变 → 重刷一遍。"""
        self._refresh_manual_state()
        self._build_slots()
        self._emit_manual(None)

    def _set_all_shift_pick(self, value: bool) -> None:
        """「全选 / 全不选」（样板：`EntryEventPanel._set_all_shifts`）。"""
        for var in self.shift_pick:
            var.set(bool(value))
        self._on_shift_pick()

    def _on_dorm_pick(self) -> None:
        """换宿舍：重画这一间的位次（顺带把这间的显示名记进 `_room_names`）。"""
        self._remember_room_names()
        self._build_slots()

    # ------------------------------------------------------------ 显示 / 刷新
    def _remember_room_names(self) -> None:
        """把当前这一班的宿舍显示名记下来（写入口只吃设施下标，显示要名字）。"""
        state = self._manual_state(self._view_shift())
        self._room_names[int(state.get("shift", self._view_shift()))] = {
            int(d["index"]): str(d["name"]) for d in state.get("dorms", [])}

    def _room_label(self, shift_index: int, facility_index: int) -> str:
        """宿舍显示名；没记到就退回 `宿舍（第 N 间）`。"""
        known = self._room_names.get(int(shift_index), {})
        return known.get(int(facility_index)) or f"宿舍（第 {facility_index + 1} 间）"

    def _refresh_manual_state(self, force: bool = False) -> bool:
        """按 `manual_dorm_editor_state` 刷新**宿舍下拉**（标签 ↔ 设施下标）与房间名。

        返回有没有变化。`force=False` 时：**面板还有没落地的编辑就直接返回** ——
        异步重算落地后的刷新不能把用户刚写进去的值盖回旧的。

        ⚠️ 2026-10：**`values` 与变量都改成"标签"**（`宿舍#1（5 位）`），下标靠
        `_dorm_label_to_index` 反查 —— 旧写法是 `values=[str(index)]`、变量也存下标，
        于是界面显示裸数字（`13`），用户一旦从下拉里选名字，`int()` 就转不动、
        `_current_dorm()` 返回 None、**位次网格整块消失**（实测缺陷）。
        ⚠️ 同时删掉了「刷新 `☑ 锁` 值」那一段（锁控件整块没了）——**但"还有没落地的编辑
        就不刷新"的保护留着**（`self._job is not None` 那条）。
        """
        if not self.shift_pick:
            return False
        if not force and self._job is not None:
            return False
        self._remember_room_names()
        dorms = self._manual_state(self._view_shift()).get("dorms", [])
        labels = [f"{d['name']}（{d['capacity']} 位）" for d in dorms]
        self._dorm_labels = labels
        self._dorm_label_to_index = {label: int(d["index"])
                                     for label, d in zip(labels, dorms)}
        self.dorm_combo.configure(values=labels)
        if self.dorm_var.get() not in self._dorm_label_to_index:
            self.dorm_var.set(labels[0] if labels else "")
        for d in dorms:
            self._room_names.setdefault(self._view_shift(), {})[
                int(d["index"])] = str(d["name"])
        return False

    def _notice_text(self, message=None) -> str:
        """提示行 = 「改动落到哪些班次」＋ 可选的即时反馈 ＋ **固定口径说明**（永远都在）。

        ⚠️ 界面上看到的这一行**压到 ≤26 字**（整行原先 84 字、占 3 行）：省下来的原文
        挂悬停提示 —— 「班次勾选＝改动目标、显示第一个勾中的班」那半句在 `_notice_hint` 里，
        固定口径那半句在模块常量 `MANUAL_CAVEAT_HINT`（`_notice_hint` 把它拼在最后）。
        """
        if not self.shift_pick:
            return ""
        all_n = len(self.shift_pick)
        chosen = self._checked_shifts()
        if not chosen:
            head = "⚠ 一个班次都没勾：改动无处落地。"
        elif len(chosen) == all_n:
            head = f"改动落到全部 {all_n} 个班次。"
        else:
            which = "、".join(f"第 {i + 1} 班" for i in chosen)
            head = f"改动落到勾选的 {len(chosen)} 个班次（{which}）。"
        if message:
            head = f"{head}　{message}"
        return f"{head}　{MANUAL_CAVEAT}"

    def _notice_hint(self) -> str:
        """那行提示的**悬停全文**：可见那句 + 被压掉的班次口径 + 固定口径。

        ⚠️ 动态读 `manual_notice` 当前的文本（可见句里带着"落到几个班次 / 哪个班"）——
        不读的话提示会停在建面板时那一句（它随勾选与防抖重算变）。
        """
        try:
            shown = str(self.manual_notice.cget("text"))
        except tk.TclError:
            shown = ""
        return (f"{shown}\n"
                f"班次 `☑` 勾中的就是**改动落地目标**；上面显示的是**第一个勾中的班**"
                f"（「正在看」下拉已删除，显示哪一班完全由勾选决定）。\n"
                f"{MANUAL_CAVEAT_HINT}")

    def _set_manual_msg(self, text: str) -> None:
        """面板内那一行即时反馈（状态栏那句由 `app.apply_manual_dorm` 负责）。"""
        try:
            self.manual_msg.configure(text=self._notice_text(text))
        except tk.TclError:
            pass

    def _emit_manual(self, message) -> None:
        """刷新「改动落到勾选的 N 个班次」那一行（+ 可选的即时反馈）。

        ⚠️ 2026-10：这里原来还要同步「全部解锁…」按钮的亮/灰（`_has_manual` +
        `_locked_probe`）；锁控件删掉之后那一段整块去掉（`_locked_probe` 也随之消失，
        `SettingsDialog._shift_has_manual` 已删）。
        """
        if not self.shift_pick:
            return
        self.manual_notice.configure(text=self._notice_text(message))
        if message is not None:
            self._set_manual_msg(message)

    # ------------------------------------------------------------ 位次
    #: 逐位控件的每行格数 —— **按栏宽静态算**，不再写死 5。
    #:
    #: ⚠️ 这里曾经写死 `SLOT_COLS = 5`（整页 760px 宽时定的：5×144 = 720 正好一行），
    #:    改成两栏后它被原样搬进 330px 的半栏 ⇒ `_build_slots` 把 5 格排成**一行**、
    #:    请求宽 730 > 可用 302，**后 3 格看不见也点不到**（实测缺陷，见 §0）。
    #:    现在按 `IDLE_MANUAL_W` 算：可用内宽 = 栏宽 − 28（`theme.PAD`×2 ＋ LabelFrame
    #:    的边框与标签），每列 `SLOT_CELL_W`；容量 5 ⇒ 380 的栏排 **3 列 2 行**。
    #:    这样"以后改栏宽"只是改上面那个常量，列数会自己跟着变。
    #: ⚠️ 有意**不**做运行时 `<Configure>` 自适应重建：面板宽度由常量固定、
    #:    `pack_propagate(False)` 已切断传播，动态重建容易和 `_build_slots` /
    #:    `_refresh_manual_state` 形成刷新回路（见 `ui/batch.py` 那条自激回路的教训）。
    SLOT_COLS = max(1, (IDLE_MANUAL_W - 2 * theme.PAD
                        - 2 * (theme.PAD + theme.GAP)) // SLOT_CELL_W)

    def _build_slots(self) -> None:
        """重建当前宿舍的逐位控件：每格＝`第 N 位` ＋ `人名按钮`（**没有锁控件**）。

        ⚠️ 2026-10 删掉了每格末尾的 `☑ 锁`（用户口径：**不需要手动上锁**，摆位即上锁、
        清空该位即解锁）—— 连带 `_slot_locks` / `_on_seat_lock` / `_clear_locks`
        与 `unlock_btn` 一起删除；`manual_dorm_editor_state` 里的 `locked` 字段仍给（约束），
        界面只是不显示它。
        """
        if not self.shift_pick:
            return
        for w in self.slots_frame.winfo_children():
            w.destroy()
        self._slot_btns = {}
        shift = self._view_shift()
        dorm = self._current_dorm()
        if dorm is None:
            tk.Label(self.slots_frame, text="（这一班没有宿舍）", bg=theme.BG, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).grid(row=0, column=0, sticky="w")
            self._emit_manual(None)
            return
        fac = int(dorm["index"])
        seats = list(dorm.get("seats", []))
        for i, seat in enumerate(seats):
            slot = int(seat["slot"])
            cell = tk.Frame(self.slots_frame, bg=theme.BG)
            cell.grid(row=i // self.SLOT_COLS, column=i % self.SLOT_COLS, sticky="ew",
                      padx=(0, 2), pady=1)
            tk.Label(cell, text=f"第 {slot + 1} 位", bg=theme.BG, fg=theme.MUTED, width=4,
                     anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
            # ⚠️ 空位的按钮文案**保留可点引导**（「（空位 · 点这里选人）」）—— 这是这一页
            #    唯一的"摆位"入口，删了用户就找不到怎么放人。
            btn = ttk.Button(cell, text=seat.get("name") or SLOT_EMPTY_TEXT, width=11,
                             command=lambda b=fac, s=slot: self._pick_slot(b, s))
            btn.pack(side="left", padx=(1, 1))
            key = (shift, fac, slot)
            self._slot_btns[key] = btn
        for col in range(self.SLOT_COLS):
            self.slots_frame.columnconfigure(col, weight=1)
        self._emit_manual(None)

    def _set_slot_button(self, key, name: str) -> None:
        """把某一格的人名按钮文字刷成 `name`（空 → `SLOT_EMPTY_TEXT`）。"""
        btn = self._slot_btns.get(key)
        if btn is None:
            return
        try:
            btn.configure(text=name or SLOT_EMPTY_TEXT)
        except tk.TclError:
            pass

    def _pick_slot(self, facility_index: int, slot: int) -> None:
        """点某一格的人名按钮 → `ask_operator`（`""` = 清空；`None` = 取消）。"""
        shift = self._view_shift()
        dorm = self._current_dorm()
        current = ""
        if dorm is not None:
            for seat in dorm.get("seats", []):
                if int(seat["slot"]) == int(slot):
                    current = str(seat.get("name") or "")
                    break
        picked = ask_operator(self, self._op_names, current,
                              title=f"手动入宿 · 第 {shift + 1} 班 "
                                    f"{self._room_label(shift, facility_index)} 第 {slot + 1} 位")
        self._write_slot(facility_index, slot, picked)

    def _write_slot(self, facility_index: int, slot: int, picked) -> None:
        """把选人结果交给写入口（`None` = 取消，什么都不做）。"""
        if picked is None:
            return
        name = str(picked)
        msg = self._request({"facility_index": int(facility_index),
                             "slot_names": {int(slot): name}}, quiet=True)
        key = (self._view_shift(), int(facility_index), int(slot))
        if key in self._slot_btns:
            self._set_slot_button(key, name)
        if self._job is None:
            self._set_manual_msg(msg)
        self._refresh_manual_state()

    # ------------------------------------------------------------ 写入口
    def _request(self, req: dict, quiet: bool = False) -> str:
        """把一次改动交给写入口，返回一句结果文案。

        `req` 支持三种改法（可同时给，同一格以 `locks` 为准）：
        `slot_names: {位次: 人名或 ""}`、`locks: {位次: True/False}`；都不给 `slot_names`/`locks`
        而只给 `shifts` 时＝只按 `shifts` 落（当前实现不用这一支）。
        `quiet=True` 时**不**刷新「改动落到几个班次」那行（调用方紧接着会刷）。
        """
        if not quiet:
            self._emit_manual(None)
        chosen = self._checked_shifts()
        if not chosen:
            return "⚠ 一个班次都没勾：改动无处落地。"
        if self._on_manual is None:
            return "（手动入宿编辑器没有接上写入口）"
        payload = {"shifts": chosen, "facility_index": int(req["facility_index"])}
        if req.get("slot_names"):
            payload["slot_names"] = {int(k): str(v) for k, v in req["slot_names"].items()}
        if req.get("locks"):
            payload["locks"] = {int(k): bool(v) for k, v in req["locks"].items()}
        if "dorm_names" in req:
            payload["dorm_names"] = dict(req["dorm_names"])
        return str(self._on_manual(payload) or "")

    def _resolve_table_height(self) -> int:
        """表格可视高度：没给就用**内容区剩余**（上面那些说明文字先量一遍）。

        为什么 auto：逐次表的行数随周期数与候选人数浮动，固定高度要么撑爆内容区、
        要么白留一大块。

        ⚠️ 两栏布局（`_two_col`）下**不能**用"整页减已用"：左栏与右栏是**并排**的，
        减出来的高度会把左栏那一份也算进去（于是表格高得离谱、整页被顶出内容区）。
        而这时左栏的 `winfo_reqheight()` **恒为 1**（`pack_propagate(False)` 切断了
        "孩子撑大父容器"），所以只能给一个**估值** `IDLE_TABLE_H_INIT` ——
        建表之后由 `BatchMixin._fit_table_height()` 那条自校正兜底（实测示例排班
        自然高 700 的下限附近，右栏与左栏差不多高）。
        """
        if self._table_h is not None:
            return self._table_h
        self.update_idletasks()
        if getattr(self, "_two_col", None) is not None:
            return IDLE_TABLE_H_INIT
        used = sum(w.winfo_reqheight() for w in self.winfo_children())
        # 表格之后还有一行说明（约 45px，wraplength 会折行）与内边距 → 留 82px
        return max(MIN_TABLE_H, self._page_h - used - 82)

    # ------------------------------------------------------------ 表格
    def _build_table(self, parent) -> None:
        """可滚动的分组表（结构固定，内容随 `self._groups` 重建）。

        `parent`＝两栏布局的**右栏**（吃剩余宽度，`sticky="nsew"`）。
        ⚠️ 用 `pack_propagate(False)` 把高度钉在 `table_height` 上：右栏在 grid 里是
        `sticky="nsew"`，不钉的话画布高度会被"剩余空间"二次解释（实测表格忽高忽低）。
        """
        body = tk.Frame(parent, bg=theme.BG, height=self.table_height)
        body.pack(fill="both", expand=True)
        body.pack_propagate(False)
        self.canvas = tk.Canvas(body, bg=theme.PANEL, highlightthickness=1,
                                highlightbackground=theme.BORDER, height=self.table_height)
        self.scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=theme.PANEL)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        # 滚轮：整张表 + 行 + **滚动条本体**都能滚（见 ui/scroll.py）
        self.vs = VScroll(self.canvas, self.scroll, self.inner, win=self._win)
        self._fill_table()
        self.vs.refresh()          # 行建完 → 立刻重算滚动区间（别等几何事件）
        tk.Label(parent, text="「说明」列是引擎实际做的安排（进了哪间宿舍第几号位、"
                              "与谁互换、或为什么没安排）",
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=IDLE_MANUAL_W,
                 anchor="w").pack(anchor="w", pady=(2, 0))

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
        for title, scope, rows, _t0, _t1 in self._groups:
            head = tk.Frame(self.inner, bg=theme.PANEL_ALT)
            head.pack(fill="x", pady=(2, 0))
            self.vs.join(head)
            tk.Label(head, text=title, bg=theme.PANEL_ALT, fg=theme.TEXT, anchor="w",
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=6)
            rows = list(rows)
            if not rows:
                continue                      # 没有候选的执行点不占表格（否则会留下一串空组头）
            for text, value in (("全选", True), ("全不选", False)):
                btn = ttk.Button(head, text=text, width=6,
                                 command=lambda v=value, s=scope: self._set_group(s, v))
                btn.pack(side="right", padx=(0, 4))
                self._widgets.append(btn)
            for row_i, row_data in enumerate(rows):
                name, mood_text, where, use_d, note, _targets = row_data
                bg = theme.zebra(row_i)                 # 隔行底色
                row = tk.Frame(self.inner, bg=bg)
                row.pack(fill="x", padx=4, pady=ROW_PAD)
                self.vs.join(row)
                tk.Label(row, text=name, bg=bg, fg=theme.TEXT, width=13, anchor="w",
                         font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
                tk.Label(row, text=mood_text, bg=bg, fg=theme.MUTED, width=7,
                         anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
                key = (scope[0], scope[1], name)
                use_d = self.state.get(key, use_d)
                use = tk.BooleanVar(value=bool(use_d))
                dirty = [False]         # 用户动过这一行没有（同键多行时用它决定谁说了算）
                chk = tk.Checkbutton(row, text="参与", variable=use, bg=bg,
                                     activebackground=bg, highlightthickness=0,
                                     font=(theme.FONT_FAMILY, theme.FS_SMALL),
                                     command=lambda d=dirty: (d.__setitem__(0, True),
                                                              self._schedule_rebuild()))
                chk.pack(side="left", padx=(6, 4))
                # 只读说明：引擎这一刻的安排（进了哪 / 为什么没进）
                # ⚠️ **显示层截断到一行**（改前一行 109~120 字、按 `wraplength` 折成 2~5 行
                #    ⇒ 每行高矮不齐、一屏只看得见三四行）；完整原文挂悬停提示，引擎侧不动。
                full = _tidy_note(note)
                lbl = tk.Label(row, text=_clip_note(full), bg=bg, fg=theme.MUTED, anchor="w",
                               justify="left", wraplength=IDLE_MANUAL_W,
                               font=(theme.FONT_FAMILY, theme.FS_SMALL))
                lbl.pack(side="left")
                if len(full) > NOTE_CLIP:
                    _hint(lbl, full)
                self._rows.append((key, use, dirty))
                self._widgets.append(chk)
        self._sync()
        self.canvas.yview_moveto(0)

    # ------------------------------------------------------------ 交互
    def _protected_value(self) -> int:
        """读「锁定位置数」：非法输入就恢复上一次有效值（并回写输入框）。"""
        text = str(self.protected.get()).strip()
        try:
            value = max(0, int(text))
        except (TypeError, ValueError):
            value = self._protected_cache
        self._protected_cache = value
        if text != str(value):
            self.protected.set(str(value))
        return value

    def _refresh_blacklist(self) -> None:
        """把 `self.blacklist` 刷到列表框里（加/删都走它）。"""
        if not hasattr(self, "black_list"):
            return
        self.black_list.delete(0, "end")
        for name in self.blacklist:
            self.black_list.insert("end", name)

    def _add_blacklist(self) -> None:
        """把下拉里选中的干员加入黑名单（已在名单里就不动），然后实时重算。"""
        name = str(self.black_pick.get() or "").strip()
        if not name or name in self.blacklist:
            return
        self.blacklist.append(name)
        self._refresh_blacklist()
        self._schedule_rebuild()

    def _remove_blacklist(self) -> None:
        """把列表框里选中的干员移出黑名单，然后实时重算。"""
        picked = list(self.black_list.curselection())
        if not picked:
            return
        for idx in reversed(picked):
            del self.blacklist[idx]
        self._refresh_blacklist()
        self._schedule_rebuild()

    def _collect(self) -> None:
        """把控件里的当前值收回 `self.state`（只剩"参不参与"）。

        ⚠️ **同一个键可能出现在多组里**（长班的内部换班点与班初共用一份逐人设置）⇒
        不能让"后遍历到的那一行"把用户刚改的那一行盖回去：**用户动过的行优先**，
        都没动过时才按行序取值（此时各行本来就一致）。收完把"动过"标记清掉。
        """
        dirty: dict = {}
        clean: dict = {}
        for key, use, flag in self._rows:
            value = bool(use.get())
            if flag[0]:
                dirty.setdefault(key, value)
            else:
                clean.setdefault(key, value)
            flag[0] = False
        self.state.update(clean)
        self.state.update(dirty)

    def _on_toggle(self) -> None:
        """① 总开关：置灰整张表并实时重算。"""
        self._sync()
        self._schedule_rebuild()

    def _set_group(self, scope, value: bool) -> None:
        """某一组（某次换班执行点）的全选 / 全不选。

        ⚠️ 组键是 `(周期, 班次)`：同班的班初与内部换班点共用一份设置 ⇒ 勾"全选"会把
        这一班**所有执行点**的行一起勾上（这正是"同班共用一份逐人设置"的口径）。
        """
        for key, use, flag in self._rows:
            if key[:2] == tuple(scope):
                use.set(bool(value))
                flag[0] = True
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
        """把当前状态交给调用方重算，并用返回的新分组表重建表格。

        ⚠️ 现在重算是**异步**的（`app.recompute_async`，见 `ui/app.py`）：`on_change` 返回的
        分组表是**改动前**那份，所以这里先用它把表画出来（不闪烁），真正的"新表"由
        `refresh_groups`（设置中心注册的落地回调）在算完之后重建。
        """
        self._collect()
        if self._on_change is not None:
            groups = self._on_change(bool(self.enabled.get()), dict(self.state),
                                     self._protected_value(), list(self.blacklist))
            if groups is not None:
                self._groups = list(groups)
        if self.winfo_exists():
            self._fill_table()

    def refresh_groups(self, groups) -> None:
        """**重算落地后**由设置中心回调：换掉分组表并重建（保留用户当前的选择）。"""
        if groups is None or not self.winfo_exists():
            return
        self._groups = list(groups)
        self._fill_table()

    def refresh_from_provider(self) -> None:
        """异步重算落地后的刷新入口（**绑定方法**，设置中心用 `add_recalc_listener` 注册它）。

        ⚠️ 它必须是绑定方法：`app.add_recalc_listener` 存的是 `weakref.WeakMethod`，
        面板销毁后回调自动失效；lambda 不行（会被立刻回收，而且强引用会吊住控件）。

        ③ 手动入宿那块也在这里跟着刷新（锁态 / 人名的**只读显示**），
        但**用户刚动过的格子不被覆盖**（`_refresh_manual_state` 里的 dirty 标记）。
        """
        if self._groups_provider is not None:
            self.refresh_groups(self._groups_provider())
        # ⚠️ **逐位那几行要整块重建**：`set_facility_slots(manual=True)` 锁的是**整段位次**
        #    （放一个人 ⇒ 同房间其它格子的 ☑ 也会亮起来）。只"改 var 的值"不重建的话，
        #    界面上那几格的 ☑ 会停在旧样子（用户看到的就是"我改的这一位锁了、别的没锁"）。
        if self.shift_pick and self.winfo_exists():
            self._refresh_manual_state(force=True)
            self._build_slots()

    def has_pending_edit(self) -> bool:
        """面板有没有"还在防抖窗口里"的改动？（`app` 的异步重算据此决定"先别落地"）。

        ⚠️ 手动入宿的改动**不走防抖**（一次点选就是一次写），所以这里只看逐次表那个任务；
        但只要有任务在等，`_refresh_manual_state` 也会跟着推迟（别拿旧值盖用户刚写的格子）。
        """
        return self._job is not None

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
        """收成 `(enabled, {(周期, 班次, 干员): 参不参与}, 锁定位置数, 黑名单)`。"""
        self._collect()
        return (bool(self.enabled.get()), dict(self.state),
                self._protected_value(), list(self.blacklist))


class IdleToDormPanel(tk.Frame, IdleToDormMixin):
    """「闲置入宿」设置**内容本体**（设置中心「闲置入宿」分区）。

    改动经 `on_change` **实时生效**（防抖 250ms）；面板不需要"应用"
    （关掉设置中心即接受），也没有"取消回滚"。
    """

    def __init__(self, master, enabled: bool, groups: Sequence,
                 on_change=None, note: str = "", table_height="auto",
                 page_height: int = 0, groups_provider=None,
                 protected_slots: int = 5, blacklist: Sequence = (),
                 all_names: Sequence = (), state_provider=None, on_manual=None,
                 operator_names: Sequence = ()):
        super().__init__(master, bg=theme.BG)
        self._init_idle_body(master, enabled, groups, on_change=on_change, note=note,
                             table_height=table_height, page_height=page_height,
                             groups_provider=groups_provider,
                             protected_slots=protected_slots, blacklist=blacklist,
                             all_names=all_names, state_provider=state_provider,
                             on_manual=on_manual, operator_names=operator_names)

    def destroy(self) -> None:
        """销毁时取消还没跑的重建任务（否则会对着已销毁的控件报 invalid command name）。"""
        self._cancel_job()
        tk.Frame.destroy(self)
