"""ui/batch.py —— 「批量设置」对话框：**当前布局里的所有干员 + 心情，一次改完**。

## 为什么需要它

看板上改一个人要「左键选人 / 右键设心情」各点一次；一份 3 班排班有 57 个位置、
57 名干员，逐个点完要上百次点击。这个对话框把同一件事摊成一张表：

| 区域 | 能做什么 |
|---|---|
| **心情** | 全部满心情 / 全部 0 / 统一设为 X / **按当前时刻回填** / 恢复导入值 |
| **干员** | **粘贴一份名单**按房间顺序填入 / 清空本班次 / 逐行点开搜索选人 |
| **表格** | 房间 · 位次 · 干员 · 心情，一次看全、一次改完 |

## 三条口径（写清楚免得被当 bug）

1. **心情列永远是「周期起点（0:00）的心情」**：引擎里心情是跨班连续的库仑积分量，
   一个周期只有一个起点，不存在"某一班自己的起始心情"。表格里改的是那个唯一的值，
   `按当前时刻回填` 就是"把滑块现在这一刻当作新的起点"。
2. **干员列只改「班次下拉里选中的那一班」**：切下拉即可逐班改，改动互不影响。
   不做"一键套用到所有班次"——3 班（12/6/6）的人员本来就不同，一键套容易误伤。
3. **返回值只带"和导入值不同的心情"**：`moods` 是差集，调用方整份替换 `initial_moods`
   即可（`恢复导入值` ⇒ 空差集 ⇒ 手动心情被清空）。

界面只做"收集结果"，算仍然在 `ui/schedule.py` / `mood_soc` 里——本模块不引入新的数值逻辑。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from tkinter import ttk
from typing import Dict, List, Optional, Sequence, Tuple

from mood_soc.config import (MOOD_MAX, MOOD_MIN, OUTPUT_ROOM_TYPES, OUTPUT_SLOT_TOTAL,
                            facility_max_level, facility_slots)

from ui import theme
from ui.dialogs import ask_operator, parse_mood

# 滚轮用的 bindtag：只让表格区域的滚轮生效（**不用 bind_all**，那会抢走整个窗口的滚轮）
TABLE_TAG = "MoodBatchTable"
ROW_PAD = 1


def split_names(text: str) -> List[str]:
    """把「粘贴的名单」切成人名列表：换行 / 逗号 / 顿号 / 空格 / 制表符都算分隔符。"""
    out: List[str] = []
    buf = ""
    for ch in text:
        if ch in "\r\n,，、;；\t ":
            if buf:
                out.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


class BatchDialog(tk.Toplevel):
    """批量设置（模态）：返回 `(changes, moods)`，取消返回 `None`。

    - `changes`：**改过干员的班次** → `{班次下标: 布局}`（场景格式，可逐班喂 `replaced_shift`）
    - `moods`：需要写进 `initial_moods` 的心情（**只含与导入值不同的项**，整份替换即可）
    """

    def __init__(self, parent, schedule, shift_index: int = 0,
                 initial_moods: Optional[Dict[str, Decimal]] = None,
                 imported_moods: Optional[Dict[str, Decimal]] = None,
                 moods_now: Optional[Dict[str, Decimal]] = None,
                 current_t=Decimal("0")):
        super().__init__(parent, bg=theme.BG)
        self.title("批量设置 · 当前布局的干员与心情")
        self.result = None
        self._schedule = schedule
        self._labels = schedule.shift_labels()
        self._shift_index = max(0, min(int(shift_index), len(schedule.shifts) - 1))
        self._current_t = Decimal(str(current_t))
        self._imported: Dict[str, Decimal] = dict(imported_moods or {})
        self._now: Dict[str, Decimal] = dict(moods_now or {})
        # 全排班的起点心情（一个干员一个值，跨班共用）
        self._moods: Dict[str, Decimal] = {
            n: Decimal(str(self._imported.get(n, MOOD_MAX))) for n in schedule.operator_names()}
        for n, v in (initial_moods or {}).items():
            self._moods[n] = Decimal(str(v))
        self._fac_names: List[dict] = []
        self._level_vars: Dict[int, tk.StringVar] = {}   # 房间下标 → 等级下拉
        self._elite: Dict[str, int] = {}                 # 干员 → 精英化（0/1/2），默认 2
        self._elite_vars: Dict[str, tk.StringVar] = {}   # 干员 → 练度下拉
        self._draft: Dict[int, List[dict]] = {}      # 改过的班次：下标 → 工作副本
        self._rows: List[dict] = []                  # 行控件（结构没变时复用）
        self._mood_vars: Dict[str, tk.StringVar] = {}
        self._cells: List[Tuple[int, int]] = []      # 表格里的 (设施下标, 位次)

        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 2))
        tk.Label(head, text="批量设置", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_TITLE)).pack(side="left")
        tk.Label(head, text="　班次", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
        self.shift_var = tk.StringVar(value=self._labels[self._shift_index])
        box = ttk.Combobox(head, textvariable=self.shift_var, state="readonly",
                           values=self._labels, width=20)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda _e: self._on_shift_change())
        tk.Label(head, text="干员改动只作用于这一班；心情是周期起点，全排班共用",
                 bg=theme.BG, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(8, 0))

        self._sync_facilities()          # 先把工作副本准备好（等级/容量区要用）
        self._build_mood_bar()
        self._build_level_bar()
        self._build_op_bar()
        self._build_table()

        self.err = tk.Label(self, text="", bg=theme.BG, fg=theme.DANGER, anchor="w",
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.err.pack(fill="x", padx=theme.PAD)

        btns = tk.Frame(self, bg=theme.BG)
        btns.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, theme.PAD))
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="应用", style="Accent.TButton",
                   command=self._ok).pack(side="right", padx=(0, 6))

        self.bind("<Escape>", lambda _e: self.destroy())
        self._rebuild_rows()
        self._center(parent)
        self._modal(parent)

    # ================================================================ 房间等级区
    def _build_level_bar(self) -> None:
        """逐间房改**等级**（容量随之变化）——上游 `rooms[].phases[lv].maxStationedNum`。

        为什么要在这里：MAA 排班文件不带等级（导入时按人数推断最低可行等级，
        见 `mood_soc/maa.py`），而等级决定"这间房能放几个人"，改完表格行数要跟着变。
        制造站/贸易站/发电站共用 9 个建造位（上游 `layouts.v0.slots` 的 OUTPUT 槽位），
        所以这里顺带把"已用 N/9"写出来。
        """
        box = tk.LabelFrame(self, text="房间等级（决定这间房能放几个人）", bg=theme.BG,
                            fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                            relief="groove", labelanchor="nw")
        box.pack(fill="x", padx=theme.PAD, pady=(0, 4))
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x", padx=theme.GAP, pady=(4, 2))
        for i, fac in enumerate(self._fac_names):
            world = self._schedule.shifts[self._shift_index].world.facilities[i]
            label = world.display_name
            tk.Label(row, text=f"{label} Lv", bg=theme.BG, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(0, 2))
            var = tk.StringVar(value=str(int(fac.get("level", world.level))))
            cb = ttk.Combobox(row, textvariable=var, state="readonly", width=2,
                              values=[str(lv) for lv in
                                      range(1, facility_max_level(world.ftype) + 1)])
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>",
                    lambda _e, k=i, v=var: self._on_level_change(k, v))
            self._level_vars[i] = var
            tk.Label(row, text="　", bg=theme.BG).pack(side="left")
        used = sum(1 for i, f in enumerate(self._fac_names)
                   if self._schedule.shifts[self._shift_index].world.facilities[i].ftype
                   in OUTPUT_ROOM_TYPES)
        self.level_note = tk.Label(box, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                                   font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.level_note.pack(fill="x", padx=theme.GAP, pady=(0, 6))
        self._sync_level_note(used)

    def _sync_level_note(self, used: int = None) -> None:
        if used is None:
            used = sum(1 for i in range(len(self._fac_names))
                       if self._schedule.shifts[self._shift_index].world.facilities[i].ftype
                       in OUTPUT_ROOM_TYPES)
        over = used > OUTPUT_SLOT_TOTAL
        self.level_note.configure(
            text=f"制造站/贸易站/发电站已用 {used}/{OUTPUT_SLOT_TOTAL} 个建造位"
                 + ("（超过上游上限！）" if over else "")
                 + "　｜　改等级会立刻改变下面表格的行数",
            fg=(theme.DANGER if over else theme.MUTED))

    def _on_level_change(self, fac_index: int, var) -> None:
        """改房间等级 → 写进工作副本 → 重建表格（容量变了，行数跟着变）。"""
        self._collect_moods()
        self._fac_names[fac_index]["level"] = int(var.get())
        self._mark_dirty()
        self._rebuild_rows()
        self._sync_level_note()

    # ================================================================ 心情区
    def _build_mood_bar(self) -> None:
        box = tk.LabelFrame(self, text="心情（周期起点 0:00 的心情，与班次无关）", bg=theme.BG,
                            fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                            relief="groove", labelanchor="nw")
        box.pack(fill="x", padx=theme.PAD, pady=(theme.GAP, 4))
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x", padx=theme.GAP, pady=(4, 2))
        ttk.Button(row, text="全部满心情 24", command=lambda: self._set_all(MOOD_MAX)
                   ).pack(side="left")
        ttk.Button(row, text="全部 0", command=lambda: self._set_all(MOOD_MIN)).pack(
            side="left", padx=(6, 0))
        self.uniform = tk.StringVar(value="24")
        ttk.Entry(row, textvariable=self.uniform, width=5).pack(side="left", padx=(10, 2))
        ttk.Button(row, text="全部设为这个值", command=self._set_uniform).pack(side="left")
        ttk.Button(row, text="按当前时刻回填", command=self._fill_from_now).pack(
            side="left", padx=(10, 0))
        ttk.Button(row, text="恢复导入值", command=self._restore_imported).pack(
            side="left", padx=(6, 0))
        tk.Label(box, text="「按当前时刻回填」= 把滑块现在这一刻的实际心情写成新的周期起点"
                           "（调参最省事的一键）。",
                 bg=theme.BG, fg=theme.MUTED, anchor="w",
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(fill="x", padx=theme.GAP,
                                                               pady=(0, 6))

    def _set_all(self, value: Decimal) -> None:
        for n in self._moods:
            self._moods[n] = Decimal(value)
        self._refresh_mood_cells()
        self.err.configure(text="")

    def _set_uniform(self) -> None:
        v = parse_mood(self.uniform.get())
        if v is None:
            self._error("「全部设为」需要一个 0 ~ 24 的数字")
            return
        self._set_all(v)

    def _fill_from_now(self) -> None:
        if not self._now:
            self._error("还没有轨迹可以回填（先导入排班）")
            return
        for n in self._moods:
            if n in self._now:
                self._moods[n] = Decimal(str(self._now[n]))
        self._refresh_mood_cells()
        self.err.configure(text=f"已按 {theme.fmt_clock(self._current_t)} 的实际心情回填")

    def _restore_imported(self) -> None:
        for n in self._moods:
            self._moods[n] = Decimal(str(self._imported.get(n, MOOD_MAX)))
        self._refresh_mood_cells()
        self.err.configure(text="")

    # ================================================================ 干员区
    def _build_op_bar(self) -> None:
        box = tk.LabelFrame(self, text="干员（只改上面选中的这一班）", bg=theme.BG,
                            fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_SMALL), bd=1,
                            relief="groove", labelanchor="nw")
        box.pack(fill="x", padx=theme.PAD, pady=(0, 4))
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x", padx=theme.GAP, pady=(4, 2))
        ttk.Button(row, text="批量粘贴名单…", command=self._paste_names).pack(side="left")
        ttk.Button(row, text="清空本班次", command=self._clear_shift).pack(side="left",
                                                                          padx=(6, 0))
        ttk.Button(row, text="全部设为 E2", command=lambda: self._set_all_elite(2)).pack(
            side="left", padx=(6, 0))
        self.show_empty = tk.BooleanVar(value=True)
        tk.Checkbutton(row, text="显示空位", variable=self.show_empty, bg=theme.BG,
                       activebackground=theme.BG, highlightthickness=0,
                       command=self._rebuild_rows).pack(side="left", padx=(10, 0))
        tk.Label(box, text="「批量粘贴名单」＝一行一个（逗号/空格也行），按房间顺序依次填入；"
                           "点表格里的干员名可以搜索更换。",
                 bg=theme.BG, fg=theme.MUTED, anchor="w",
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(fill="x", padx=theme.GAP,
                                                               pady=(0, 6))

    # ================================================================ 表格
    def _build_table(self) -> None:
        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD)
        self.canvas = tk.Canvas(body, bg=theme.PANEL, highlightthickness=0, height=420,
                                highlightbackground=theme.BORDER)
        self.scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=theme.PANEL)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        # 滚轮只在表格区域内生效（bindtags，不用 bind_all）
        self.canvas.bind_class(TABLE_TAG, "<MouseWheel>", self._on_wheel)
        self._join_table_tag(self.canvas)
        self._join_table_tag(self.inner)

    def _join_table_tag(self, widget) -> None:
        tags = list(widget.bindtags())
        if TABLE_TAG not in tags:
            widget.bindtags(tuple(tags) + (TABLE_TAG,))

    def _on_wheel(self, event):
        try:
            if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
                return "break"
        except tk.TclError:
            return None
        delta = getattr(event, "delta", 0)
        steps = -int(delta / 120) if delta else (-1 if delta > 0 else 1)
        self.canvas.yview_scroll(steps, "units")
        return "break"

    def _sync_facilities(self) -> None:
        """把选中班次的布局拷成可改的形式。

        改过的班次会留在 `self._draft` 里：切到别的班次再切回来，**未应用的改动不会丢**
        （不然"改完第 1 班顺手去看第 2 班"就把第 1 班的改动冲掉了）。
        """
        idx = self._shift_index
        if idx in self._draft:
            self._fac_names = self._draft[idx]
            return
        shift = self._schedule.shifts[idx]
        self._fac_names = []
        for f in shift.facilities:
            fac = dict(f)
            # 干员可能是字符串，也可能是带练度的对象 `{"name": ..., "elite": ...}`
            # （上一轮改过练度就是这样存的）→ 这里统一成"名字列表 + self._elite"
            specs = list(f.get("operators", []))
            names = []
            for spec in specs:
                if isinstance(spec, dict):
                    name = str(spec.get("name", ""))
                    if name:
                        self._elite[name] = int(spec.get("elite", 2))
                else:
                    name = str(spec)
                if name:
                    names.append(name)
            fac["operators"] = names
            self._fac_names.append(fac)
        for op in shift.world.all_operators():           # 练度预填（默认 E2 满练）
            self._elite.setdefault(op.name, int(op.elite))

    def _mark_dirty(self) -> None:
        """记下"这一班的干员被改过"，并把工作副本留给切班次后复用。"""
        self._draft[self._shift_index] = self._fac_names

    def _slot_count(self, fac: dict, fac_index: int) -> int:
        """这一行房画几个位置（容量以外若还有人也画出来，与看板同一口径）。

        ⚠️ 容量按**工作副本里的等级**算（不是模型那一份）——否则在上面改了等级，
        下面表格的行数不会跟着变。上游：`rooms[].phases[lv].maxStationedNum`。
        """
        world = self._schedule.shifts[self._shift_index].world.facilities[fac_index]
        cap = facility_slots(world.ftype, int(fac.get("level", world.level)))
        return max(cap, len(fac.get("operators", [])), 1)

    def _room_name(self, fac_index: int) -> str:
        """房间显示名（用模型侧的 `display_name`，如"制造站#2"）。"""
        return self._schedule.shifts[self._shift_index].world.facilities[fac_index].display_name

    def _row_plan(self) -> List[Tuple[int, int, str, str]]:
        """表格要画哪些行 → `[(设施下标, 位次, 干员名, 房间名或 "")]`（房间名只在该组首行）。"""
        plan: List[Tuple[int, int, str, str]] = []
        for fi, fac in enumerate(self._fac_names):
            ops = list(fac.get("operators", []))
            first = True
            for si in range(self._slot_count(fac, fi)):
                name = ops[si] if si < len(ops) else ""
                if not name and not self.show_empty.get():
                    continue
                plan.append((fi, si, name, self._room_name(fi) if first else ""))
                first = False
        return plan

    def _op_text(self, name: str) -> str:
        """表格里的干员名（非精英化二时带练度角标，与看板一致）。"""
        if not name:
            return "（空位 · 点这里选人）"
        elite = self._elite.get(name, 2)
        return f"{name} E{elite}" if elite < 2 else name

    def _on_elite_change(self, name: str) -> None:
        """改某个干员的精英化（决定他的心情技能能不能生效）。"""
        var = self._elite_vars.get(name)
        if var is None:
            return
        text = var.get().strip()          # "E0"/"E1"/"E2"
        self._elite[name] = int(text[1:]) if len(text) == 2 and text[1:].isdigit() else 2
        self._mark_dirty()                # 练度也要写回布局（否则这一班不会被提交）
        for r in self._rows:
            if r["op"].cget("text").startswith(name):
                r["op"].configure(text=self._op_text(name))
                break

    def _set_all_elite(self, elite: int) -> None:
        """一键把当前班次所有干员设为该精英化（默认口径就是 E2 满练）。"""
        for fac in self._fac_names:
            for n in fac.get("operators", []):
                if n:
                    self._elite[n] = int(elite)
        self._mark_dirty()
        self._refresh_elite_cells()
        self.err.configure(text=f"已把本班次全部干员设为 E{elite}（点「应用」才生效）")

    def _refresh_elite_cells(self) -> None:
        for name, var in self._elite_vars.items():
            var.set(f"E{self._elite.get(name, 2)}")
        for r in self._rows:
            op = r["op"].cget("text").split(" ")[0]
            if op in self._elite:
                r["op"].configure(text=self._op_text(op))

    def _op_spec(self, name: str):
        """写回场景的干员写法：只有**非 E2** 才写成对象（保持 JSON 简洁）。"""
        elite = self._elite.get(name, 2)
        return {"name": name, "elite": elite} if elite != 2 else name

    def _rebuild_rows(self) -> None:
        """按计划画表格；**结构没变就只换内容**（切班次/换人是最常见的路径，
        重建 50 行要 ~290ms，复用只要几毫秒——与看板 `_structure_signature` 同一套思路）。
        """
        plan = self._row_plan()
        if [(p[0], p[1]) for p in plan] == [r["key"] for r in self._rows]:
            self._fill_rows(plan)
            return
        for w in self.inner.winfo_children():
            w.destroy()
        self._rows = []
        for fi, si, name, room in plan:
            row = tk.Frame(self.inner, bg=theme.PANEL)
            row.pack(fill="x", padx=4, pady=ROW_PAD)
            room_lbl = tk.Label(row, text=room, bg=theme.PANEL, fg=theme.TEXT, width=11,
                                anchor="w", font=(theme.FONT_FAMILY, theme.FS_SMALL))
            room_lbl.pack(side="left")
            tk.Label(row, text=f"{si + 1}", bg=theme.PANEL, fg=theme.MUTED, width=3, anchor="w",
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left")
            op = self._op_label(row, fi, si, name)
            op.pack(side="left", fill="x", expand=True)
            entry = ttk.Entry(row, width=6)
            elite = ttk.Combobox(row, state="readonly", width=3,
                                 values=[f"E{i}" for i in range(3)])
            elite.bind("<<ComboboxSelected>>", lambda _e, n=name: self._on_elite_change(n))
            dash = tk.Label(row, text="—", bg=theme.PANEL, fg=theme.MUTED, width=8,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL))
            self._rows.append({"key": (fi, si), "room": room_lbl, "op": op,
                               "entry": entry, "elite": elite, "dash": dash})
            self._join_table_tag(row)
        if not plan:
            tk.Label(self.inner, text="（这一班没有位置）", bg=theme.PANEL, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", padx=6, pady=6)
        self._fill_rows(plan)
        self.canvas.yview_moveto(0)

    def _fill_rows(self, plan) -> None:
        """把计划写进行控件（心情输入框按干员名重新绑定，空位显示 —）。"""
        self._mood_vars.clear()
        self._elite_vars.clear()
        self._cells = [(fi, si) for fi, si, _n, _r in plan]
        for r, (fi, si, name, room) in zip(self._rows, plan):
            r["room"].configure(text=room)
            elite = self._elite.get(name, 2) if name else 2
            r["op"].configure(text=self._op_text(name),
                              fg=(theme.TEXT if name else theme.MUTED))
            if name:
                var = tk.StringVar(value=theme.fmt_mood(self._moods.get(name, MOOD_MAX)))
                self._mood_vars[name] = var
                r["entry"].configure(textvariable=var)
                if not r["entry"].winfo_manager():
                    r["entry"].pack(side="left", padx=(6, 4))
                if r["dash"].winfo_manager():
                    r["dash"].pack_forget()
                ev = tk.StringVar(value=f"E{elite}")            # 练度：决定技能能不能生效
                self._elite_vars[name] = ev
                r["elite"].configure(textvariable=ev)
                if not r["elite"].winfo_manager():
                    r["elite"].pack(side="left", padx=(0, 4))
            else:
                if r["entry"].winfo_manager():
                    r["entry"].pack_forget()
                if r["elite"].winfo_manager():
                    r["elite"].pack_forget()
                if not r["dash"].winfo_manager():
                    r["dash"].pack(side="left", padx=(6, 4))


    def _op_label(self, row, fac_index: int, slot_index: int, name: str) -> tk.Label:
        """干员单元格：可点的文字（点开搜索窗换人 / 选人 / 清空）。"""
        label = tk.Label(row, text=(name or "（空位 · 点这里选人）"), bg=theme.PANEL,
                         fg=(theme.TEXT if name else theme.MUTED), anchor="w", cursor="hand2",
                         font=(theme.FONT_FAMILY, theme.FS_SMALL))
        label.bind("<Button-1>",
                   lambda _e: self._pick_operator(fac_index, slot_index))
        return label

    def _refresh_mood_cells(self) -> None:
        for name, var in self._mood_vars.items():
            var.set(theme.fmt_mood(self._moods.get(name, MOOD_MAX)))

    # ================================================================ 交互
    def _on_shift_change(self) -> None:
        """切班次：先把表格里的心情收进来，再换布局重建行。"""
        self._collect_moods()
        label = self.shift_var.get()
        if label in self._labels:
            self._shift_index = self._labels.index(label)
        self._sync_facilities()
        self._rebuild_rows()
        self.err.configure(text="")

    def _pick_operator(self, fac_index: int, slot_index: int) -> None:
        self._collect_moods()
        ops = self._fac_names[fac_index].setdefault("operators", [])
        current = ops[slot_index] if slot_index < len(ops) else ""
        names = [n for n in self._schedule.operator_names()]
        others = [n for f in self._fac_names for n in f.get("operators", []) if n != current]
        picked = ask_operator(self, names + [n for n in others if n not in names], current)
        if picked is None:
            return                                  # 取消
        while len(ops) <= slot_index:               # 中间的空位用 "" 占住（不能塌缩）
            ops.append("")
        if picked == "":
            ops[slot_index] = ""
        else:
            # 同一个人不能同时占两个位置：先把他从别的位置摘掉
            for f in self._fac_names:
                f["operators"] = [n for n in f.get("operators", []) if n != picked]
            ops = self._fac_names[fac_index].setdefault("operators", [])
            while len(ops) <= slot_index:
                ops.append("")
            ops[slot_index] = picked
        self._mark_dirty()
        self._rebuild_rows()

    def _clear_shift(self) -> None:
        self._collect_moods()
        for f in self._fac_names:
            f["operators"] = []
        self._mark_dirty()
        self._rebuild_rows()
        self.err.configure(text="已清空本班次（点「应用」才生效）")

    def _paste_names(self) -> None:
        self._collect_moods()
        dlg = PasteDialog(self, len(self._all_slots()))
        self.wait_window(dlg)
        self.grab_set()                              # 子对话框收走了 grab，这里拿回来
        if dlg.result is None:
            return
        self._apply_names(*dlg.result)

    def _apply_names(self, names: Sequence[str], clear_first: bool = True) -> None:
        """把一份名单按「房间顺序 + 位次」填进当前班次（重复的人只填第一个位置）。

        这是「批量粘贴名单」的真正逻辑，单独抽出来是为了能不开模态框直接测。
        """
        if clear_first:
            for f in self._fac_names:
                f["operators"] = []
        slots = self._all_slots()                   # [(设施下标, 位次)]
        used = {n for f in self._fac_names for n in f.get("operators", []) if n}
        filled = 0
        for name, (fi, si) in zip(names, slots):
            if name in used:                        # 同一个人只留一个位置（先到先得）
                continue
            used.add(name)
            ops = self._fac_names[fi].setdefault("operators", [])
            while len(ops) <= si:                   # 中间的空位用 "" 占住（不能塌缩）
                ops.append("")
            ops[si] = name
            filled += 1
        for f in self._fac_names:                   # 收掉空位（引擎按顺序读 operators，不留洞）
            f["operators"] = [n for n in f.get("operators", []) if n]
        self._mark_dirty()
        self._rebuild_rows()
        left = max(0, len(names) - len(slots))                 # 位置不够、没排上的
        dup = min(len(names), len(slots)) - filled             # 同名跳过、没填上的
        msg = f"已填入 {filled} 人"
        if dup:
            msg += f"；{dup} 条重名（同名只填一次）"
        if left:
            msg += f"；{left} 人没有位置"
        self.err.configure(text=msg + "（点「应用」才生效）")

    def _all_slots(self) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        for fi, fac in enumerate(self._fac_names):
            for si in range(self._slot_count(fac, fi)):
                out.append((fi, si))
        return out

    # ================================================================ 收结果
    def _collect_moods(self) -> None:
        """把表格里的心情文字收回 `self._moods`（不合法就保留原值，交给 `_ok` 报错）。"""
        for name, var in self._mood_vars.items():
            v = parse_mood(var.get())
            if v is not None:
                self._moods[name] = v

    def _error(self, text: str) -> None:
        self.err.configure(text=text)
        self.bell()

    def _ok(self) -> None:
        bad = [n for n, var in self._mood_vars.items() if parse_mood(var.get()) is None]
        if bad:
            self._error("心情要在 0 ~ 24 之间（检查：" + "、".join(bad[:4]) +
                        ("…" if len(bad) > 4 else "") + "）")
            return
        self._collect_moods()
        # 干员改动：所有被改过的班次（下标 → 布局），调用方逐班 `replaced_shift`
        changes = {i: [dict(f, operators=[self._op_spec(n) for n in f.get("operators", []) if n])
                       for f in facs] for i, facs in self._draft.items()}
        moods = {n: v for n, v in self._moods.items()
                 if Decimal(str(self._imported.get(n, MOOD_MAX))) != v}
        self.result = (changes, moods)
        self.destroy()

    # ================================================================ 窗口杂务
    def _center(self, parent) -> None:
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), 780)
        h = min(max(self.winfo_reqheight(), 560), 820)
        self.geometry(f"{w}x{h}")
        self.update_idletasks()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        self.geometry(f"+{px + max((pw - w) // 2, 0)}+{py + max((ph - h) // 4, 0)}")

    def _modal(self, parent) -> None:
        self.transient(parent)
        self.grab_set()
        self.focus_set()


class PasteDialog(tk.Toplevel):
    """「批量粘贴名单」：一个多行文本框 + 是否先清空。返回 `(names, clear_first)`。"""

    def __init__(self, parent, slots: int):
        super().__init__(parent, bg=theme.BG)
        self.title("批量粘贴名单")
        self.resizable(False, False)
        self.result = None
        self._slots = slots
        tk.Label(self, text=f"一行一个干员名（逗号 / 空格 / 顿号也行）；本班次共 {slots} 个位置。",
                 bg=theme.BG, fg=theme.TEXT, font=(theme.FONT_FAMILY, theme.FS_BODY)
                 ).pack(anchor="w", padx=theme.PAD, pady=(theme.PAD, 2))
        tk.Label(self, text="按房间顺序从上到下填入（控制中枢 → 制造站 → … → 宿舍）；"
                           "多出来的人会被忽略。",
                 bg=theme.BG, fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL)
                 ).pack(anchor="w", padx=theme.PAD)
        self.text = tk.Text(self, width=46, height=12, font=(theme.FONT_FAMILY, theme.FS_SMALL),
                            highlightthickness=1, highlightbackground=theme.BORDER)
        self.text.pack(padx=theme.PAD, pady=theme.GAP)
        self.clear_first = tk.BooleanVar(value=True)
        tk.Checkbutton(self, text="先清空本班次再填（不勾＝只覆盖前面的位置）",
                       variable=self.clear_first, bg=theme.BG, activebackground=theme.BG,
                       highlightthickness=0).pack(anchor="w", padx=theme.PAD)
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

    def _ok(self) -> None:
        names = split_names(self.text.get("1.0", "end"))
        if not names:
            return
        self.result = (names, bool(self.clear_first.get()))
        self.destroy()


def ask_batch(parent, schedule, shift_index: int = 0, initial_moods=None,
              imported_moods=None, moods_now=None, current_t=Decimal("0")):
    """返回 `(changes, moods)`（取消返回 `None`）：改过的班次布局 + 与导入值不同的心情。"""
    dlg = BatchDialog(parent, schedule, shift_index=shift_index, initial_moods=initial_moods,
                      imported_moods=imported_moods, moods_now=moods_now, current_t=current_t)
    parent.wait_window(dlg)
    return dlg.result


__all__ = ["BatchDialog", "PasteDialog", "ask_batch", "split_names"]
