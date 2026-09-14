"""ui/board.py —— 基建看板：每个房间一张卡片，位置上是**紧凑心情芯片**（名字 + 实时心情）。

## 为什么重做过一次布局

旧版每个位置是一个 104×58 的大格子（名字/数值/心情条/红脸标签四个控件），
一间 5 人宿舍就占 290px 高 —— 一屏根本装不下全部房间，必须滚动才能看到宿舍，
"看到所有干员"成了空话。

现在每个位置是 **21px 高的芯片**（`ui/widgets.MoodChip`：左侧心情色条 + 名字 + 数值），
于是：

| 房间 | 旧高度 | 新高度 |
|---|---|---|
| 控制中枢（5 人） | ~300px | 5×21 + 表头 ≈ 126px |
| 4 间宿舍（20 人） | 4×300px（同一行也放不下） | 5×21 + 表头 ≈ 126px |

行排布：**按设施类型一行**（控制中枢 / 制造站 / 贸易站 / 发电站 / 宿舍），
会客室·办公室·训练室·加工站这类"通常各一间"的小房间**拼在同一行**；
每行内的卡片等宽铺满（`columnconfigure(weight=1, uniform=...)`），
所以窗口拉宽时自动变宽、不会被挤到滚动条外面。滚动条保留，仅当布局特别大时兜底。

> 想调排布：改 `ROW_ORDER` / `SMALL_TYPES` / `PER_ROW` 即可。房间数与容量仍完全由导入的排班决定。

交互：**左键**位置 → 选人/更换/清空；**右键**位置 → 设该干员的心情（周期起点）。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from mood_soc.config import FACILITY_LABELS

from . import theme
from .widgets import MoodChip

# 看板行序（按设施中文名）
ROW_ORDER = ("控制中枢", "制造站", "贸易站", "发电站",
             "会客室", "办公室", "训练室", "加工站", "宿舍")

# 这些类型单独占一整行、**芯片横排**（控制中枢 5 人横着排只占 1 行高）
FULL_WIDTH_TYPES = ("控制中枢",)

# 两列分组：左＝工作区（产出设施），右＝辅助与休息区。
# 这样"控制中枢 + 两列"的总高度约 350px，一屏必定放得下（旧版单列要 695px，得滚动）。
COLUMN_GROUPS = (
    ("制造站", "贸易站", "发电站"),
    ("会客室", "办公室", "训练室", "加工站", "宿舍"),
)

# 这些类型"通常各一间"，可以拼在同一行
SMALL_TYPES = ("会客室", "办公室", "训练室", "加工站")
SMALL_ROW_MAX = 4          # 小房间拼行时每行最多几张卡
PER_ROW = 4                # 同类型房间多于此数则换行

# 房间缩写（「全员一览」里的位置标记，如 制1 / 宿3 / 中）
ROOM_ABBR = {
    "控制中枢": "中", "制造站": "制", "贸易站": "贸", "发电站": "电",
    "会客室": "会", "办公室": "办", "训练室": "训", "加工站": "加", "宿舍": "宿",
}


def facility_tag(facility, ordinal: int = 0) -> str:
    """房间的位置标记：单间设施不带序号（"中"），多间带序号（"制2"）。"""
    abbr = ROOM_ABBR.get(FACILITY_LABELS.get(facility.ftype, ""), "?")
    return f"{abbr}{ordinal}" if ordinal else abbr


class SlotView:
    """一个位置（房间 × 座位）：持有它的芯片与元数据。"""

    __slots__ = ("fac_index", "slot_index", "operator", "chip", "mood_label")

    def __init__(self, fac_index: int, slot_index: int, operator: Optional[str], chip: MoodChip):
        self.fac_index = fac_index
        self.slot_index = slot_index
        self.operator = operator
        self.chip = chip
        self.mood_label = chip.value          # 兼容旧调用（测试读的是这个）


class BaseBoard(tk.Frame):
    """可滚动的基建看板（紧凑芯片布局）。

    `on_slot_click(fac_index, slot_index)`：**左键**（选人/更换/清空）
    `on_slot_right(fac_index, slot_index)`：**右键**（设置该位置干员的心情）
    """

    def __init__(self, master, on_slot_click: Optional[Callable[[int, int], None]] = None,
                 on_slot_right: Optional[Callable[[int, int], None]] = None, **kw):
        kw.setdefault("bg", theme.BG)
        super().__init__(master, **kw)
        self.on_slot_click = on_slot_click
        self.on_slot_right = on_slot_right
        self.slots: List[SlotView] = []
        self.chip_by_operator: Dict[str, MoodChip] = {}

        self.header = tk.Label(self, text="（未导入排班）", bg=theme.BG, fg=theme.TEXT,
                               font=(theme.FONT_FAMILY, theme.FS_TITLE), anchor="w")
        self.header.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, 4))

        body = tk.Frame(self, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=theme.PAD, pady=(0, theme.PAD))
        self.canvas = tk.Canvas(body, bg=theme.BG, highlightthickness=0)
        self.scroll = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=theme.BG)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ 布局
    def _group(self, shift) -> List[tuple]:
        """按设施类型分组 → [(类型名, [设施下标...])]，顺序按 ROW_ORDER。"""
        groups: List[tuple] = []
        for i, f in enumerate(shift.world.facilities):
            label = FACILITY_LABELS.get(f.ftype, str(f.ftype))
            for g in groups:
                if g[0] == label:
                    g[1].append(i)
                    break
            else:
                groups.append((label, [i]))
        ordered = [(t, idxs) for t, idxs in groups if t in ROW_ORDER]
        ordered += [(t, idxs) for t, idxs in groups if t not in ROW_ORDER]
        return ordered

    def _rows_of_column(self, groups: List[tuple]) -> List[List[int]]:
        """一列内把房间切成若干行（小房间拼行、同类型超过 PER_ROW 换行）。"""
        rows: List[List[int]] = []
        pending: List[int] = []
        for label, idxs in groups:
            if label in SMALL_TYPES and len(idxs) <= 2:
                pending.extend(idxs)
                if len(pending) >= SMALL_ROW_MAX:
                    rows.append(pending)
                    pending = []
                continue
            if pending:
                rows.append(pending)
                pending = []
            for i in range(0, len(idxs), PER_ROW):
                rows.append(idxs[i:i + PER_ROW])
        if pending:
            rows.append(pending)
        return rows

    def _split_columns(self, groups: List[tuple]) -> List[List[tuple]]:
        """按 `COLUMN_GROUPS` 把"非整行"的设施分到几列（未列出的类型并入最后一列）。"""
        columns: List[List[tuple]] = [[] for _ in COLUMN_GROUPS]
        for label, idxs in groups:
            for c, names in enumerate(COLUMN_GROUPS):
                if label in names:
                    columns[c].append((label, idxs))
                    break
            else:
                columns[-1].append((label, idxs))
        return [c for c in columns if c]

    def set_layout(self, shift, sub_title: str = "") -> None:
        """按某个班次重建看板（清空旧控件）。"""
        for w in self.inner.winfo_children():
            w.destroy()
        self.slots.clear()
        self.chip_by_operator.clear()
        n_slots = sum(len(f.operators) for f in shift.world.facilities)
        self.header.configure(
            text=f"{shift.label}　{sub_title}　·　{len(shift.world.facilities)} 间房 / "
                 f"{n_slots} 个位置 / {len(shift.operators)} 名干员"
                 if sub_title else shift.label)

        groups = self._group(shift)
        # ① 铺满整行的设施（控制中枢）：芯片横排
        for label, idxs in [(t, i) for t, i in groups if t in FULL_WIDTH_TYPES]:
            for chunk in [idxs[i:i + PER_ROW] for i in range(0, len(idxs), PER_ROW)]:
                self._build_row(self.inner, shift, chunk, horizontal=True)
        # ② 其余分两列
        rest = [(t, i) for t, i in groups if t not in FULL_WIDTH_TYPES]
        columns = self._split_columns(rest)
        if columns:
            holder = tk.Frame(self.inner, bg=theme.BG)
            holder.pack(fill="both", expand=True, anchor="w")
            for col_groups in columns:
                col = tk.Frame(holder, bg=theme.BG)
                col.pack(side="left", fill="both", expand=True, padx=(0, theme.GAP))
                for idxs in self._rows_of_column(col_groups):
                    self._build_row(col, shift, idxs, horizontal=False)

    def _build_row(self, parent: tk.Frame, shift, idxs: List[int], horizontal: bool) -> None:
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x", pady=(0, 6), anchor="w")
        for c in range(len(idxs)):
            row.columnconfigure(c, weight=1, uniform=f"{id(row)}")
        for c, fi in enumerate(idxs):
            self._build_card(row, shift, fi, horizontal=horizontal).grid(
                row=0, column=c, sticky="nsew", padx=(0, theme.GAP))

    def _build_card(self, parent: tk.Frame, shift, fac_index: int,
                    horizontal: bool = False) -> tk.Frame:
        facility = shift.world.facilities[fac_index]
        label = FACILITY_LABELS.get(facility.ftype, str(facility.ftype))
        same_type = [f for f in shift.world.facilities if f.ftype == facility.ftype]
        ordinal = same_type.index(facility) + 1 if len(same_type) > 1 else 0

        card = tk.Frame(parent, bg=theme.PANEL, highlightbackground=theme.BORDER,
                        highlightthickness=1)
        head = tk.Frame(card, bg=theme.PANEL_ALT)
        head.pack(fill="x")
        tk.Label(head, text=facility_tag(facility, ordinal), bg=theme.PANEL_ALT, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(6, 4), pady=1)
        tk.Label(head, text=f"{label} Lv{facility.level}", bg=theme.PANEL_ALT, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", pady=1)
        tk.Label(head, text=f"{len(facility.operators)}/{facility.capacity}",
                 bg=theme.PANEL_ALT, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="right", padx=(2, 6), pady=1)

        strip = tk.Frame(card, bg=theme.PANEL)
        strip.pack(fill="x", padx=4, pady=3)
        if facility.capacity <= 0 and not facility.operators:
            tk.Label(strip, text="不可进驻", bg=theme.PANEL, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(anchor="w", pady=6)
            return card
        # 容量以外若还有干员（越界场景）也画出来，别把人藏起来
        slots = max(facility.capacity, len(facility.operators), 1)
        for si in range(slots):
            occupant = facility.operators[si].name if si < len(facility.operators) else None
            self._build_slot(strip, fac_index, si, occupant, horizontal)
        return card

    def _build_slot(self, parent: tk.Frame, fac_index: int, slot_index: int,
                    operator: Optional[str], horizontal: bool = False) -> None:
        """横向卡片（控制中枢铺满整行）→ 芯片横排；竖向卡片 → 芯片纵向堆叠。"""
        chip = MoodChip(parent, width=theme.CHIP_MIN_W,
                        on_left=lambda: self._click(fac_index, slot_index),
                        on_right=lambda: self._right(fac_index, slot_index))
        if horizontal:
            chip.pack(side="left", fill="x", expand=True, padx=(0, 3))
        else:
            chip.pack(fill="x", pady=1)
        chip.set(operator)
        view = SlotView(fac_index, slot_index, operator, chip)
        self.slots.append(view)
        if operator:
            self.chip_by_operator[operator] = chip

    def _click(self, fac_index: int, slot_index: int) -> None:
        if self.on_slot_click:
            self.on_slot_click(fac_index, slot_index)

    def _right(self, fac_index: int, slot_index: int) -> None:
        if self.on_slot_right:
            self.on_slot_right(fac_index, slot_index)

    # ------------------------------------------------------------------ 刷新
    def update_moods(self, moods: Dict[str, Decimal], quick: bool = False) -> None:
        """时间滑动时只改芯片上的数值与颜色（不重建控件）。

        `quick=True` 是拖动时的跟手路径（见 `ui/widgets.MoodChip.update_mood`）。
        """
        for v in self.slots:
            if v.operator:
                v.chip.update_mood(moods.get(v.operator), quick=quick)

    def highlight(self, operator: str) -> bool:
        """高亮某干员的位置（「全员一览」点人时联动），返回是否在本班次看板上。"""
        chip = self.chip_by_operator.get(operator)
        if chip is None:
            return False
        chip.flash()
        return True
