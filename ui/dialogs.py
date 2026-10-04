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

# 「闲置入宿」页的纵向尺寸：`IDLE_TABLE_H_INIT` 是逐次表的**初始可视高度**
# （建表格之前只能给个估值，见 `_resolve_table_height`）；建表之后由它自己的
# `_fit_table_height()` 自校正。
#
# ⚠️ **2026-10「锁定入宿」工单**：这一页原先还有 ③ 手动入宿编辑器（左栏固定 380px、
#    与逐次表**左右并排**），现在 ③ 整块搬去独立分区 `LockPanel` ⇒ 表**独占整页宽**，
#    `IDLE_MANUAL_W` / `SLOT_CELL_W` / `SLOT_EMPTY_TEXT` / `MANUAL_CAVEAT*` 与
#    `SLOT_COLS` 那一整套**随之删除**（矩阵的尺寸常量见下面 `MATRIX_*`）。
IDLE_TABLE_W = 700                  # 逐次表那一行的说明文字折行宽（独占整页后变宽）
IDLE_TABLE_H_INIT = 320
MIN_TABLE_H = 190

# 「锁定入宿」那行格子的**固定口径**（`LockPanel._draw_cell` 挂在下行按钮的悬停提示上）：
# ⚠️ 2026-10 口径＝**摆位即上锁、清空即解锁**（用户原话：「放上去之后自动上锁。**不需要
#    手动上锁**」「解锁时只需要**将该位置空**就可以了」「锁功能只作为内部自动入宿进行位置
#    判定时使用，而**不对外输出暴露**」）—— 界面上**没有**逐位 `☑ 锁`、也没有「全部解锁」。
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

# ---------------------------------------------------------------------------
# 「锁定入宿」矩阵（设置中心**独立整页**分区，2026-10）：
# 行＝位次、列＝班次，**每格上下两行**（上行「当前」只读、下行「我的指定」可点）。
#
# 尺寸是**实测口径**（改了行里的控件就要重量一遍）：
# · `MATRIX_CELL_H` = 一格的高度（两行：`FS_SMALL` 的灰字 18px ＋ 按钮 26px ＋ 上下留白 4px）；
# · `MATRIX_ROW_W`   = 首列（「第 N 位」）的宽度；
# · 列宽按**可视宽**自适应，夹到 `[MATRIX_MIN_COL_W, MATRIX_MAX_COL_W]`：下限保证
#   "班次多时横向滚动而不是把字挤没"，上限保证"只有 1~2 班时列不会宽得离谱"；
# · `MATRIX_H_INIT` 是画布的初始高度，建完表由 `LockPanel._fit_height()` 自校正到
#   "整页 ≤ `ui.settings.PAGE_H`"。
MATRIX_CELL_H = 46
MATRIX_HEAD_H = 24
MATRIX_ROW_W = 76
MATRIX_MIN_COL_W = 84
MATRIX_MAX_COL_W = 150
MATRIX_H_INIT = 320
MATRIX_H_MIN = 120
#: 「我的指定」没有指定时的占位（**别和空串混**：空串是"这一格没人"）。
MATRIX_EMPTY = "—"
#: 「批量填写…」的班次下拉里那个"全部"选项
BATCH_ALL_SHIFTS = "全部班次"

#: 选人框「**恢复默认（回到导入时）**」的返回值**哨兵**（任务 C）。
#: ⚠️ **不能用 `""`**：`""` 已经是「清空该位置」的既有返回值（`None` ＝取消），
#: 三个含义必须分得开 —— 混用会让"清空"与"恢复默认"变成同一个动作。
#: 只有开了 `restore_default=True` 的调用点才可能拿到它（`LockPanel` 那一处）。
RESTORE_DEFAULT = "__restore_default__"



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
    """选人：搜索框 + 列表（支持键盘上下/回车，双击确认）。

    `restore_default=True` 时**多一个**「恢复默认（回到导入时）」按钮（任务 C）：
    返回 `RESTORE_DEFAULT` 哨兵。**默认关** —— 只有「锁定入宿」矩阵那一处开它
    （「＋ 添加干员…」/`on_slot_left`/「干员与心情」位置列都**不该**多出这一项：
    它们没有"导入原位"的语义，点了也没用）。
    """

    def __init__(self, parent, names: Sequence[str], current: str = "", title: str = "选择干员",
                 restore_default: bool = False):
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
        if restore_default:
            # ⚠️ 与「清空该位置」**并存**（用户口径）：清空＝交还自动入宿；
            #    恢复默认＝这一格回到导入时的样子（并把我挪过的人送回原位）。
            self.restore_btn = ttk.Button(btns, text="恢复默认（回到导入时）",
                                          command=self._restore)
            self.restore_btn.pack(side="left", padx=(6, 0))
            _hint(self.restore_btn,
                  "把这一格恢复成**导入时**的样子：解除这一格的「我的指定」，"
                  "把导入时原本坐这一格的人放回来，并把我挪动过的人送回各自的导入原位"
                  "（原位被占 ⇒ 换回去）。只对这一格、这一个班次生效。")
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

    def _restore(self) -> None:
        """「恢复默认（回到导入时）」：返回哨兵（**不是** `""`，见 `RESTORE_DEFAULT`）。"""
        self.result = RESTORE_DEFAULT
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def ask_operator(parent, names: Sequence[str], current: str = "",
                 title: str = "选择干员",
                 restore_default: bool = False) -> Optional[str]:
    """返回选中的干员名；`""` 表示「清空该位置」；`None` 表示取消；
    开了 `restore_default` 时还可能返回 `RESTORE_DEFAULT`（「恢复默认（回到导入时）」）。

    ⚠️ `title` 是**转发形参**：`OperatorPicker.__init__` 本来就有它（第 4 个位置参数），
    而本壳原先没接 —— 三处调用点（位次按钮 / 「＋ 添加干员…」/ `on_slot_left`）
    都传了 `title=`，于是 `TypeError: ask_operator() got an unexpected keyword argument
    'title'`，而 **Tk 吞掉回调异常只打 stderr** ⇒ 用户看到的是「点了完全没反应」、
    台账一字不动。别把 `title` 插到 `current` **前面**（那会打乱既有位置参数顺序）。
    ⚠️ `restore_default` 同理必须是**转发形参**（工单 §3 任务 C）：拼错名字、或忘了往下传，
      那一项就会变成"点了没反应"。
    """
    dlg = OperatorPicker(parent, names, current, title=title, restore_default=restore_default)
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

    引擎规则（`mood_soc/rules.apply_idle_to_dorm`；三层解耦：**锁定入宿 > 自动入宿 > 导入布局**）：

    | 层 | 谁 | 本面板管不管 |
    |---|---|---|
    | ① 锁定入宿 | 设置中心的**独立分区「锁定入宿」**（`LockPanel`：行＝位次 × 列＝班次）把某人钉在某个宿舍位次（写班次布局 + 手动台账） | **不管**（它在另一个分区；见 `LockPanel`） |
    | ② 自动入宿 | 竖向正序填空床 → 全满则取**心情最低**的候选，换出"锁定区外、心情 ≥ 她、心情最大"的住户 | 本面板显示它这一刻打算安排谁 |
    | ③ 全局配置 | 总开关 / 锁定位置数 / 黑名单 / 逐人"不参与" | ① ② 与 ④ 那张逐次表 |

    | 控件 | 落到引擎 |
    |---|---|
    | ① 启用闲置入宿 | `IdleToDormConfig.enabled` |
    | ② 锁定位置数 | `IdleToDormConfig.protected_slots`（按竖向正序锁前 N 个位置） |
    | ② 黑名单 | `IdleToDormConfig.blacklist`（永远不能**通过闲置入宿进宿舍**的人） |
    | 每行的「参与」 | `per_operator[(周期,班次,干员)].enabled` |

    **逐次表**按时间排（第 1 周期第 1 班 → …），**一个换班执行点一组**：真实班初一组，
    长班（> 12h）的每个**内部换班点**各一组（标题带 `（12h 内部换班）`）；组内只放那一刻
    **真的有候选**的人；每行只有「参与」可改，另给一列**只读提示**（这一位会被安排去哪、
    或为什么没安排）。⚠️ **同班各执行点共用同一份逐人设置**：组里的 `(周期, 班次, 干员)`
    键相同 ⇒ 改任一组会同步影响同班其他执行点。

    ⚠️ **锁定入宿（旧称手动入宿）2026-10 搬去独立分区**（工单 §2.1）：原先它是本页的
    **③ 子面板**（勾班次 → 选宿舍 → 逐位选人，与本页的逐次表左右并排）；现在是一整页
    `LockPanel`（行＝位次 × 列＝班次），本页只剩 ①②④。两条入口（「锁定入宿」矩阵、
    「干员与心情」的位置列）写的都是同一份台账 —— 走 `Session.place_operator` 的语义
    （**钉人时先把她从本班别处摘掉**）。

    ⚠️ **锁在界面上不再暴露**（2026-10 用户口径：「放上去之后自动上锁。**不需要手动上锁**」
    「解锁时只需要**将该位置空**就可以了」「锁功能只作为内部自动入宿进行位置判定时使用，
    而**不对外输出暴露**」）：逐位 `☑ 锁`、「全部解锁…」按钮都已删除 ——
    **摆位即上锁、清空该位即解锁**（⚠️ 且**只锁你碰过的那一格**、累积：同房间导入进来的人
    不受影响，见 `_write_manual`）。`Session.set_seat_lock` / `clear_seat_locks` 仍保留
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
                        all_names: Sequence = ()):
        """把状态收好并建出整块控件（宿主的 `__init__` 里调用；`self` 必须是 tk 容器）。

        `groups_provider`：无参可调用，返回**当前**分组表（设置中心传 `app.idle_groups`）。
        异步重算落地后由 `refresh_from_provider()` 用它取新表 —— 见那个方法。
        `protected_slots` / `blacklist`：全局口径的初值（来自 `Session`）。
        `all_names`：可以加入黑名单的干员名（下拉的候选池）。

        ⚠️ **2026-10「锁定入宿」工单**：手动入宿编辑器**整块搬去独立分区**
        （`LockPanel`：行＝位次 × 列＝班次），所以这里的 `state_provider` / `on_manual` /
        `operator_names` 三个参数**连同 ③ 区一起删掉**了 —— 这一页只剩
        「① 总开关 + ② 锁定位置数 + 黑名单 + ④ 逐次表」，表也改成**独占整页宽**
        （不再与 ③ 并排两栏）。

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

        # ================= ④ 逐次表（**独占整页宽**）=================
        # ⚠️ 2026-10「锁定入宿」工单：原先这里左栏是 ③ 手动入宿编辑器、右栏是这张表
        #    （两栏并排省纵向空间）。③ 整块搬去独立分区之后，表改成**独占整页宽** ——
        #    逐次表本来就是横着排的（干员 / 心情 / 参与 / 说明），独占之后一行放得下更多字，
        #    「说明」列也不必再按半栏宽截断。
        tk.Label(self, text="④ 逐次设置：每行只有「参与」可改；「说明」是只读的引擎结果",
                 bg=theme.BG, fg=theme.TEXT, padx=0, justify="left",
                 wraplength=IDLE_TABLE_W, anchor="w").pack(anchor="w", pady=(0, 2))
        self.table_height = self._resolve_table_height()
        self._build_table(self)

        if note:
            tk.Label(self, text=note, bg=theme.BG, fg=theme.MUTED, justify="left",
                     wraplength=600, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                     ).pack(anchor="w", **pad)
        self._sync()


    def _resolve_table_height(self) -> int:
        """表格可视高度：没给就用**内容区剩余**（上面那些说明文字先量一遍）。

        为什么 auto：逐次表的行数随周期数与候选人数浮动，固定高度要么撑爆内容区、
        要么白留一大块。

        ⚠️ **2026-10 起这一页是单列**（锁定入宿搬走之后不再两栏并排），所以"整页减已用"
        直接成立 —— 改前两栏并排时它会把左栏那一份也算进去（表格高得离谱、整页被顶出
        内容区），只能给估值 `IDLE_TABLE_H_INIT`。
        """
        if self._table_h is not None:
            return self._table_h
        self.update_idletasks()
        if not self._page_h:
            return IDLE_TABLE_H_INIT
        used = sum(w.winfo_reqheight() for w in self.winfo_children())
        # 表格之后还有一行说明（约 45px，wraplength 会折行）与内边距 → 留 82px
        return max(MIN_TABLE_H, self._page_h - used - 82)

    # ------------------------------------------------------------ 表格
    def _build_table(self, parent) -> None:
        """可滚动的分组表（结构固定，内容随 `self._groups` 重建）。

        `parent`＝**本页自己**（2026-10 起独占整页宽；改前是两栏布局的右栏）。
        ⚠️ 用 `pack_propagate(False)` 把高度钉在 `table_height` 上：不钉的话画布高度会被
        "剩余空间"二次解释（实测表格忽高忽低）。
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
                 bg=theme.BG, fg=theme.MUTED, justify="left", wraplength=IDLE_TABLE_W,
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
                               justify="left", wraplength=IDLE_TABLE_W,
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

        ⚠️ 2026-10：原先这里还要跟着重建 **③ 手动入宿**那几行逐位控件；③ 搬去独立分区
        「锁定入宿」之后，本页只重建逐次表（那几行逐位控件由 `LockPanel` 自己刷）。
        """
        if self._groups_provider is not None:
            self.refresh_groups(self._groups_provider())

    def has_pending_edit(self) -> bool:
        """面板有没有"还在防抖窗口里"的改动？（`app` 的异步重算据此决定"先别落地"）。

        ⚠️ 锁定入宿的改动**不走防抖**（一次点选就是一次写），所以这里只看逐次表那个任务。
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
                 all_names: Sequence = ()):
        super().__init__(master, bg=theme.BG)
        self._init_idle_body(master, enabled, groups, on_change=on_change, note=note,
                             table_height=table_height, page_height=page_height,
                             groups_provider=groups_provider,
                             protected_slots=protected_slots, blacklist=blacklist,
                             all_names=all_names)

    def destroy(self) -> None:
        """销毁时取消还没跑的重建任务（否则会对着已销毁的控件报 invalid command name）。"""
        self._cancel_job()
        tk.Frame.destroy(self)


class BatchFillDialog(tk.Toplevel):
    """「批量填写…」：班次下拉（含「全部班次」）＋ 多行输入框 ＋ 「清空该宿舍」。

    返回 `(shifts, names, clear_first)`；取消返回 `None`。
    ⚠️ 名字的切分**复用 `ui/batch.py::split_names`**（一行一个 / 逗号 / 顿号 / 空格都行）——
    别在这里另写一套解析口径（工单 §2.4）。
    """

    def __init__(self, parent, shift_labels: Sequence[str], capacity: int):
        super().__init__(parent, bg=theme.BG)
        self.title("批量填写锁定入宿")
        self.resizable(False, False)
        self.result = None
        self._labels = [str(x) for x in shift_labels]
        tk.Label(self, text=f"一行一个干员名（逗号 / 空格 / 顿号也行）；"
                            f"按位次正序填进第 1..k 位（这间宿舍 {capacity} 位）。",
                 bg=theme.BG, fg=theme.TEXT, justify="left", wraplength=380,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(anchor="w", padx=theme.PAD,
                                                                pady=(theme.PAD, 2))
        row = tk.Frame(self, bg=theme.BG)
        row.pack(fill="x", padx=theme.PAD, pady=(0, theme.GAP))
        tk.Label(row, text="班次", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.shift_var = tk.StringVar(value=BATCH_ALL_SHIFTS)
        n = len(self._labels)
        values = [BATCH_ALL_SHIFTS] + [f"第 {i + 1} 班" for i in range(n)]
        ttk.Combobox(row, textvariable=self.shift_var, state="readonly", width=12,
                     values=values).pack(side="left", padx=(6, 0))
        self.text = tk.Text(self, width=40, height=10,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL),
                            highlightthickness=1, highlightbackground=theme.BORDER)
        self.text.pack(padx=theme.PAD)
        self.clear_first = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="先清空该宿舍（不勾＝只覆盖前面的位次）",
                       variable=self.clear_first, bg=theme.BG, activebackground=theme.BG,
                       highlightthickness=0).pack(anchor="w", padx=theme.PAD)
        self.err = tk.Label(self, text="", bg=theme.BG, fg=theme.DANGER, anchor="w",
                            justify="left", wraplength=380,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.err.pack(fill="x", padx=theme.PAD)
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

    def selected_shifts(self) -> List[int]:
        """下拉选中的班次下标（「全部班次」⇒ 0..n-1）。"""
        pick = str(self.shift_var.get())
        if pick == BATCH_ALL_SHIFTS:
            return list(range(len(self._labels)))
        for i in range(len(self._labels)):
            if pick == f"第 {i + 1} 班":
                return [i]
        return []

    def _ok(self) -> None:
        from ui.batch import split_names           # 复用同一份解析口径（别另写一套）
        names = split_names(self.text.get("1.0", "end"))
        if not names:
            self.err.configure(text="请先填至少一个名字")
            return
        self.result = (self.selected_shifts(), names, bool(self.clear_first.get()))
        self.destroy()


class LockPanel(tk.Frame):
    """「**锁定入宿**」设置页：**行＝位次、列＝班次**的矩阵（2026-10 新分区）。

    这一页的语义（工单 §2/§3）：**把某人钉到「本班 · 某宿舍 · 某位次」**，
    写入时**先把她从本班其它设施里摘掉**（`Session.place_operator` 的语义，
    见 `ui/app.py::apply_lock_dorm`）；下一班没有这条台账 ⇒ 自动解锁。

    ## 形态

    ```
    宿舍  [宿舍#1（5 位） ▾]   [批量填写…]
    ┌────────┬───────────┬───────────┬───────────┐
    │ 宿舍#1 │ 第 1 班   │ 第 2 班   │ 第 3 班   │   ← 列＝班次（列宽按可视宽自适应）
    ├────────┼───────────┼───────────┼───────────┤
    │ 第 1 位│ 菲亚梅塔  │ —         │ …         │   ← 上行＝「当前」：**只读、灰字**
    │        │〔  泡泡 〕 │〔   —   〕 │ …         │   ← 下行＝「我的指定」：**可点**
    ├────────┼───────────┼───────────┼───────────┤
    │ 第 2 位│ …         │ …         │ …         │
    └────────┴───────────┴───────────┴───────────┘
    ```

    · **「当前」**＝该班**第一个执行点、换班之后**那一刻的引擎世界（`Trajectory.world_at`
      落在执行点上取右侧，工单 §2.5；**不与主界面滑块联动**）。
    · **「我的指定」**＝你为这一班这一位指定的人（没指定 ⇒ `—`）；点它开选人框
      （框里除了「清空该位置」，还有「**恢复默认（回到导入时）**」＝把这一格还原成导入时的
      样子、并把我挪过的人送回原位，见 `Session.restore_seat`）。
    · 列数＝班次数：列宽按**可视宽度** `(width - 首列) // 班次数` 自适应，再夹到
      `[MATRIX_MIN_COL_W, MATRIX_MAX_COL_W]`；装不下时**横向滚动**（工单 §2.2）。
    · 整页高度 ≤ `PAGE_H`：建完表**自己量一遍**，超了就把画布压低到下限
      （与 `ui/batch.py` 的 `_fit_table_height` 同一套路）。

    ⚠️ 这里**没有锁控件**（用户口径）：**摆位即上锁、清空该位即解锁** ——
    「我的指定」写 `—`（选人框的「清空该位置」）就是交还自动入宿。
    ⚠️ 本类**只建控件、只收状态**：写入口是 `on_manual`（`app.apply_lock_dorm`），
    只读数据是 `view_provider`（`app.lock_dorm_view`）。
    """

    def __init__(self, master, on_manual, view_provider, page_height: int = 0):
        super().__init__(master, bg=theme.BG)
        self._on_manual = on_manual
        self._view_provider = view_provider
        self._page_h = int(page_height) or 0
        self._view: dict = {}
        self._dorm_labels: List[str] = []
        self._slot_btns: dict = {}
        self._cur_labels: dict = {}
        self._shifts: List[int] = []

        top = tk.Frame(self, bg=theme.BG)
        top.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 2))
        tk.Label(top, text="宿舍", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_BODY)).pack(side="left")
        self.dorm_var = tk.StringVar()
        self.dorm_combo = ttk.Combobox(top, textvariable=self.dorm_var, state="readonly",
                                       width=24, values=[])
        self.dorm_combo.pack(side="left", padx=(6, 8))
        self.dorm_combo.bind("<<ComboboxSelected>>", lambda _e: self.rebuild())
        ttk.Button(top, text="批量填写…", command=self._open_batch).pack(side="left")

        tk.Label(self, text="行＝位次、列＝班次；上行「当前」是那一刻引擎里坐着的人（只读），"
                            "下行「我的指定」点一下就能放人 / 清空",
                 bg=theme.BG, fg=theme.MUTED, justify="left",
                 wraplength=760, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                 ).pack(anchor="w", padx=theme.PAD)

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(theme.GAP, 0))
        self.canvas = tk.Canvas(body, bg=theme.PANEL, highlightthickness=1,
                                highlightbackground=theme.BORDER, height=MATRIX_H_INIT)
        self.hbar = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.hbar.set)
        self.hbar.pack(side="bottom", fill="x")
        self.canvas.pack(side="top", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=theme.PANEL)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._vs = VScroll(self.canvas, None, self.inner, win=self._win)
        self.canvas.bind("<Shift-MouseWheel>", self._on_hwheel)

        self.msg = tk.Label(self, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                            justify="left", wraplength=760,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.msg.pack(fill="x", padx=theme.PAD, pady=(2, theme.PAD))
        self.rebuild()

    # ------------------------------------------------------------------ 数据
    def _view_now(self) -> dict:
        return dict(self._view_provider() or {})

    def _current_dorm(self):
        """当前宿舍那一项（下拉变量存**标签**，靠 `_dorm_labels` 反查）。"""
        label = str(self.dorm_var.get() or "")
        dorms = list(self._view.get("dorms", []))
        for d in dorms:
            if f"{d['name']}（{d['capacity']} 位）" == label:
                return d
        return dorms[0] if dorms else None

    def _names_by_shift(self, slot: int) -> Dict[int, str]:
        """`{班次下标: 这一格上"我的指定"}` —— 测试与回执用它（比翻控件稳）。"""
        out: Dict[int, str] = {}
        for shift in self._shifts:
            btn = self._slot_btns.get((int(shift), int(slot)))
            if btn is None:
                continue
            try:
                text = str(btn.cget("text"))
            except tk.TclError:
                text = ""
            out[int(shift)] = "" if text == MATRIX_EMPTY else text
        return out

    def _current_by_shift(self, slot: int) -> Dict[int, str]:
        """`{班次下标: 这一格上"当前"显示的名字}`（空 = 那一刻没人）。"""
        out: Dict[int, str] = {}
        for shift in self._shifts:
            lab = self._cur_labels.get((int(shift), int(slot)))
            if lab is None:
                continue
            try:
                text = str(lab.cget("text"))
            except tk.TclError:
                text = ""
            out[int(shift)] = "" if text == MATRIX_EMPTY else text
        return out

    # ------------------------------------------------------------------ 建表
    def rebuild(self) -> None:
        """按 `view_provider` 重建整张矩阵（换宿舍 / 重算落地后都走它）。"""
        self._view = self._view_now()
        labels = [f"{d['name']}（{d['capacity']} 位）" for d in self._view.get("dorms", [])]
        self._dorm_labels = labels
        self.dorm_combo.configure(values=labels)
        # ⚠️ 变量存**标签**（`宿舍#1（5 位）`），下标靠 `_current_dorm` 反查 —— 绝不显示裸数字
        if self.dorm_var.get() not in labels:
            self.dorm_var.set(labels[0] if labels else "")
        self._fill()
        self._fit_height()

    def _fill(self) -> None:
        for w in self.inner.winfo_children():
            w.destroy()
        self._slot_btns = {}
        self._cur_labels = {}
        self._shifts = [int(s) for s in self._view.get("shifts", [])]
        dorm = self._current_dorm()
        if not self._shifts:
            tk.Label(self.inner, text="（还没有导入排班：导入后可以在这里按位次锁定入宿）",
                     bg=theme.PANEL, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).grid(
                         row=0, column=0, sticky="w", padx=8, pady=8)
            self.msg.configure(text="")
            return
        if dorm is None:
            tk.Label(self.inner, text="（这份排班里没有宿舍）", bg=theme.PANEL, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).grid(
                         row=0, column=0, sticky="w", padx=8, pady=8)
            return
        self._draw(dorm)

    def _col_width(self) -> int:
        """一列的宽度：按**可视宽**自适应，夹到 `[MATRIX_MIN_COL_W, MATRIX_MAX_COL_W]`。

        ⚠️ 下限保证"班次多时横向滚动而不是把字挤没"；上限保证"只有 1~2 班时列不会宽得离谱"。
        """
        n = max(1, len(self._shifts))
        try:
            avail = max(self.canvas.winfo_width(), 640) - MATRIX_ROW_W
        except tk.TclError:
            avail = 640
        return max(MATRIX_MIN_COL_W, min(MATRIX_MAX_COL_W, avail // n))

    def _draw(self, dorm: dict) -> None:
        """画整张矩阵（行＝位次、列＝班次）。"""
        capacity = int(dorm.get("capacity") or 0)
        col_w = self._col_width()
        # 表头行（列＝班次）
        head = tk.Frame(self.inner, bg=theme.HEADER_BG, width=MATRIX_ROW_W,
                        height=MATRIX_HEAD_H)
        head.grid(row=0, column=0, sticky="nsew")
        head.grid_propagate(False)
        self._vs.join(head)
        tk.Label(head, text=f"{dorm['name']} · 位次", bg=theme.HEADER_BG, fg=theme.MUTED,
                 anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(
                     side="left", padx=6)
        for j, shift in enumerate(self._shifts):
            cell = tk.Frame(self.inner, bg=theme.HEADER_BG, width=col_w, height=MATRIX_HEAD_H)
            cell.grid(row=0, column=j + 1, sticky="nsew")
            cell.grid_propagate(False)
            self._vs.join(cell)
            tk.Label(cell, text=f"第 {shift + 1} 班", bg=theme.HEADER_BG, fg=theme.TEXT,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=6)
        for r in range(capacity):
            bar = theme.zebra(r)
            lab = tk.Frame(self.inner, bg=bar, width=MATRIX_ROW_W, height=MATRIX_CELL_H)
            lab.grid(row=r + 1, column=0, sticky="nsew")
            lab.grid_propagate(False)
            self._vs.join(lab)
            tk.Label(lab, text=f"第 {r + 1} 位", bg=bar, fg=theme.MUTED, anchor="w",
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=6)
            for j, shift in enumerate(self._shifts):
                self._draw_cell(r, j, int(shift), col_w, bar)
        self.inner.columnconfigure(0, minsize=MATRIX_ROW_W)
        self.inner.rowconfigure(0, minsize=MATRIX_HEAD_H)
        self._vs.refresh()

    def _draw_cell(self, r: int, j: int, shift: int, col_w: int, bg: str) -> None:
        """一格＝上下两行：上行「当前」（只读灰字）、下行「我的指定」（可点按钮）。

        ⚠️ **两行都要 `_vs.join`**（`VScroll` 的 bindtags 链不含父控件）：只挂外层 Frame
        的话"指针停在文字上滚不动，停在空隙反而能动"（`ui/batch.py` 踩过）。
        """
        cell = tk.Frame(self.inner, bg=bg, width=col_w, height=MATRIX_CELL_H)
        cell.grid(row=r + 1, column=j + 1, sticky="nsew", padx=(1, 0), pady=(1, 0))
        cell.grid_propagate(False)
        self._vs.join(cell)
        dorm = self._current_dorm() or {}
        cur = str((dorm.get("current") or {}).get(shift, {}).get(r + 1) or "")
        cur_lab = tk.Label(cell, text=cur or MATRIX_EMPTY, bg=bg, fg=theme.MUTED, anchor="w",
                           font=(theme.FONT_FAMILY, theme.FS_SMALL))
        cur_lab.pack(fill="x", padx=3, pady=(1, 0))
        self._vs.join(cur_lab)
        self._cur_labels[(shift, r)] = cur_lab
        _hint(cur_lab, f"第 {shift + 1} 班 第 {r + 1} 位 · 该班换班那一刻引擎里坐着的人"
                       f"（只读）：{cur or '没人'}")
        mine = str((dorm.get("assigned") or {}).get(shift, {}).get(r + 1) or "")
        btn = ttk.Button(cell, text=mine or MATRIX_EMPTY,
                         command=lambda s=shift, k=r: self._pick_slot(s, k))
        btn.pack(fill="x", padx=2, pady=(0, 2))
        self._vs.join(btn)
        self._slot_btns[(shift, r)] = btn
        _hint(btn, f"第 {shift + 1} 班 第 {r + 1} 位 · 点它选人 / 清空 / 恢复默认（回到导入时）。\n"
                   f"{MANUAL_CAVEAT_HINT}")

    # ------------------------------------------------------------------ 刷新
    def refresh_view(self) -> None:
        """异步重算落地后刷新（只重画，不重建数据源）。

        ⚠️ 面板内那句回执会被这里冲掉（重算落地后重建）—— 永久的那句在**状态栏**
        （`app._status_after_recalc`，工单 §2.3 已知）。
        """
        if not self.winfo_exists():
            return
        self.rebuild()

    def notify_view_change(self, *_a) -> None:
        """`ui/app.py` 在"重算落地 / 换时刻"之后调它（与「干员与心情」同一口径）。"""
        self.refresh_view()

    def has_pending_edit(self) -> bool:
        """本面板没有防抖改动（点一格就是一次写），恒 False。"""
        return False

    # ------------------------------------------------------------------ 写入
    def _pick_slot(self, shift: int, slot: int) -> None:
        """点某一格 → `ask_operator`（`""`＝清空该位；`RESTORE_DEFAULT`＝恢复默认；`None`＝取消）。

        ⚠️ **只有这一处**开 `restore_default=True`：「恢复默认（回到导入时）」的四个动作
        都在"这一格 + 这一个班次"上，别的选人框（添加干员 / 位置列）没有导入原位的语义。
        """
        dorm = self._current_dorm()
        if dorm is None:
            return
        current = ""
        for seat in dorm.get("seats", []):
            if int(seat["slot"]) == int(slot):
                current = str(seat.get("name") or "")
                break
        picked = ask_operator(self, list(self._view.get("op_names") or ()), current,
                              title=f"锁定入宿 · 第 {shift + 1} 班 "
                                    f"{dorm['name']} 第 {slot + 1} 位",
                              restore_default=True)
        self._write_slot(shift, int(dorm["index"]), slot, picked)

    def _write_slot(self, shift: int, facility_index: int, slot: int, picked) -> str:
        """把一格的结果交给写入口（`None`＝取消，什么都不做）；返回回执文案。"""
        if picked is None:
            return ""
        if picked == RESTORE_DEFAULT:
            # 任务 C：恢复默认走**独立**的请求形态（`restore`）—— 它不是"写一个人名"，
            # 而是"这一格回到导入时 + 把当事人送回原位"，别塞进 `slot_names` 里冒充人名。
            msg = str(self._on_manual({
                "mode": "pin",
                "restore": {"shifts": [int(shift)], "facility_index": int(facility_index),
                            "slots": [int(slot)]},
            }) or "")
            self._set_msg(msg)
            return msg
        name = str(picked)
        msg = str(self._on_manual({
            "mode": "pin",
            "placement": {"shifts": [int(shift)], "facility_index": int(facility_index),
                          "slot_names": {int(slot): name}},
        }) or "")
        self._set_msg(msg)
        return msg

    def _set_msg(self, text: str) -> None:
        try:
            self.msg.configure(text=text or "")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 批量填写
    def _open_batch(self) -> None:
        """「批量填写…」：班次下拉 + 多行名字 → 按位次正序落到第 1..k 位（工单 §2.4）。"""
        dorm = self._current_dorm()
        if dorm is None or not self._shifts:
            self._set_msg("（还没有宿舍 / 班次：先导入排班）")
            return
        dlg = BatchFillDialog(self, self._view.get("shift_labels") or [], dorm["capacity"])
        self.wait_window(dlg)
        if dlg.result is None:
            return
        shifts, names, clear_first = dlg.result
        msg = self._on_manual({
            "mode": "pin",
            "batch": {"shifts": list(shifts), "facility_index": int(dorm["index"]),
                      "names": list(names), "clear_first": bool(clear_first)},
        })
        self._set_msg(str(msg or ""))
        self.refresh_view()

    # ------------------------------------------------------------------ 尺寸
    def _fit_height(self) -> None:
        """把画布高度压到"整页 ≤ `PAGE_H`"（自校正；与 `ui/batch.py` 同一套路）。"""
        if not self._page_h:
            return
        self.update_idletasks()
        used = sum(w.winfo_reqheight() for w in self.winfo_children()
                   if w is not self.canvas and w is not self.hbar)
        room = self._page_h - used
        h = max(MATRIX_H_MIN, min(MATRIX_H_INIT, room))
        try:
            self.canvas.configure(height=h)
        except tk.TclError:
            return
        self._vs.refresh()

    def _on_hwheel(self, event):
        """`Shift+滚轮` → 横向滚动（班次多时列装不下）。"""
        try:
            self.canvas.xview_scroll(-1 if (event.delta or 0) > 0 else 1, "units")
        except tk.TclError:
            return None
        return "break"

    def destroy(self) -> None:
        self._cancel_jobs()
        tk.Frame.destroy(self)

    def _cancel_jobs(self) -> None:
        """销毁时撤掉挂着的 after 任务（否则会对着已销毁的控件报 invalid command name）。"""
        try:
            job = getattr(self._vs, "_scroll_job", None)
            if job is not None:
                self.canvas.after_cancel(job)
                self._vs._scroll_job = None
        except (tk.TclError, AttributeError):
            pass

