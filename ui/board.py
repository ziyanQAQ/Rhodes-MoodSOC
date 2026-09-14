"""ui/board.py —— 基建布局看板：每个房间一张卡片，位置上显示**干员名 + 实时心情**。

排布按游戏内的基建概览：控制中枢在最上，中间是制造站 / 贸易站 / 发电站几排，
再往下是会客室·办公室·训练室·加工站，宿舍在最下。具体行序集中在 `ROW_ORDER`，
要调排布**只改这一处**（房间数与容量仍完全由导入的排班决定，不写死）。

性能约定：`set_layout()` 只在"布局变了"时重建控件；时间滑动走 `update_moods()`，
只改文字与颜色（O(位置数)，可跟手）。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from typing import Callable, Dict, List, Optional

from mood_soc.config import FACILITY_LABELS

from . import theme

# 看板行序（按设施中文名；同类型多个房间排在同一行，超过 4 个自动换行）
ROW_ORDER = ("控制中枢", "制造站", "贸易站", "发电站",
             "会客室", "办公室", "训练室", "加工站", "宿舍")
PER_ROW = 4

SLOT_W = 104
SLOT_H = 58
CARD_MIN_W = 130


def _short(name: str, limit: int = 5) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


class SlotView:
    """一个位置（房间 × 座位）的控件集合。"""

    __slots__ = ("fac_index", "slot_index", "operator", "frame", "name_label",
                 "mood_label", "bar", "tag", "bar_w", "bar_fill", "last_mood")

    def __init__(self, fac_index, slot_index, operator, frame, name_label, mood_label, bar, tag):
        self.fac_index = fac_index
        self.slot_index = slot_index
        self.operator = operator
        self.frame = frame
        self.name_label = name_label
        self.mood_label = mood_label
        self.bar = bar
        self.tag = tag


class BaseBoard(tk.Frame):
    """可滚动的基建看板。

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
    def set_layout(self, shift, sub_title: str = "") -> None:
        """按某个班次重建看板（清空旧控件）。"""
        for w in self.inner.winfo_children():
            w.destroy()
        self.slots.clear()
        self.header.configure(text=f"{shift.label}　{sub_title}" if sub_title else shift.label)

        # 按 ROW_ORDER 分组，未在行序里的设施类型排到最后
        groups: Dict[str, List[int]] = {}
        for i, f in enumerate(shift.world.facilities):
            groups.setdefault(FACILITY_LABELS.get(f.ftype, str(f.ftype)), []).append(i)
        ordered = [t for t in ROW_ORDER if t in groups]
        ordered += [t for t in groups if t not in ordered]

        for type_label in ordered:
            row = tk.Frame(self.inner, bg=theme.BG)
            row.pack(fill="x", anchor="w", pady=(0, theme.GAP))
            for n, fi in enumerate(groups[type_label]):
                if n and n % PER_ROW == 0:
                    row = tk.Frame(self.inner, bg=theme.BG)
                    row.pack(fill="x", anchor="w", pady=(0, theme.GAP))
                self._build_card(row, shift, fi)

    def _build_card(self, parent: tk.Frame, shift, fac_index: int) -> None:
        facility = shift.world.facilities[fac_index]
        card = tk.Frame(parent, bg=theme.PANEL, highlightbackground=theme.BORDER,
                        highlightthickness=1)
        card.pack(side="left", padx=(0, theme.GAP), anchor="n")

        head = tk.Frame(card, bg=theme.PANEL_ALT)
        head.pack(fill="x")
        label = FACILITY_LABELS.get(facility.ftype, str(facility.ftype))
        name = facility.name if facility.name and facility.name != label else label
        tk.Label(head, text=f"{name}", bg=theme.PANEL_ALT, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", padx=(6, 2), pady=2)
        tk.Label(head, text=f"Lv{facility.level}", bg=theme.PANEL_ALT, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="left", pady=2)
        tk.Label(head, text=f"{len(facility.operators)}/{facility.capacity}",
                 bg=theme.PANEL_ALT, fg=theme.MUTED,
                 font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(side="right", padx=(2, 6), pady=2)

        strip = tk.Frame(card, bg=theme.PANEL)
        strip.pack(padx=6, pady=6)
        if facility.capacity <= 0 and not facility.operators:
            tk.Label(strip, text="不可进驻", bg=theme.PANEL, fg=theme.MUTED,
                     font=(theme.FONT_FAMILY, theme.FS_SMALL)).pack(padx=8, pady=14)
            return
        # 容量以外若还有干员（历史/越界场景）也画出来，别把人藏起来
        for si in range(max(facility.capacity, len(facility.operators), 1)):
            occupant = facility.operators[si].name if si < len(facility.operators) else None
            self._build_slot(strip, fac_index, si, occupant)

    def _build_slot(self, parent: tk.Frame, fac_index: int, slot_index: int,
                    operator: Optional[str]) -> None:
        frame = tk.Frame(parent, bg=theme.PANEL_ALT, highlightbackground=theme.BORDER,
                         highlightthickness=1, width=SLOT_W, height=SLOT_H)
        frame.pack(side="left", padx=(0, 4))
        frame.pack_propagate(False)

        name_label = tk.Label(frame, text=_short(operator) if operator else "＋ 空位",
                              bg=theme.PANEL_ALT,
                              fg=theme.TEXT if operator else theme.MUTED,
                              font=(theme.FONT_FAMILY, theme.FS_BODY))
        name_label.pack(anchor="w", padx=6, pady=(6, 0))
        mood_label = tk.Label(frame, text="—" if not operator else "", bg=theme.PANEL_ALT,
                              fg=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_BIG, "bold"))
        mood_label.pack(anchor="w", padx=6)
        bar = tk.Canvas(frame, height=4, bg=theme.PANEL_ALT, highlightthickness=0)
        bar.pack(fill="x", padx=6, pady=(1, 4))
        tag = tk.Label(frame, text="", bg=theme.PANEL_ALT, fg=theme.DANGER,
                       font=(theme.FONT_FAMILY, theme.FS_SMALL))
        tag.pack(anchor="w", padx=6, pady=(0, 3))

        view = SlotView(fac_index, slot_index, operator, frame, name_label, mood_label, bar, tag)
        self.slots.append(view)
        view.bar_w = SLOT_W - 12
        view.last_mood = None
        # 心情条：底槽与填充条各画一次、之后只改坐标/颜色（拖动滑块时的高频路径）
        bar.create_rectangle(0, 0, view.bar_w, 4, fill=theme.BORDER, outline="")
        view.bar_fill = bar.create_rectangle(0, 0, 0, 4, fill=theme.OK, outline="")
        for w in (frame, name_label, mood_label, bar, tag):
            w.bind("<Button-1>", lambda _e, v=view: self._click(v))
            w.bind("<Button-3>", lambda _e, v=view: self._right(v))
            w.configure(cursor="hand2")

    def _click(self, view: SlotView) -> None:
        if self.on_slot_click:
            self.on_slot_click(view.fac_index, view.slot_index)

    def _right(self, view: SlotView) -> None:
        if self.on_slot_right:
            self.on_slot_right(view.fac_index, view.slot_index)

    # ------------------------------------------------------------------ 刷新
    def update_moods(self, moods: Dict[str, Decimal]) -> None:
        """时间滑动时只改文字/颜色（不重建控件）。心情没变的位置整条跳过。"""
        for v in self.slots:
            if not v.operator:
                continue
            mood = moods.get(v.operator)
            if mood is not None and v.last_mood is not None and mood == v.last_mood:
                continue                       # 高频拖动时的快速路径
            v.last_mood = mood
            if mood is None:
                v.mood_label.configure(text="—", fg=theme.MUTED)
                v.bar.itemconfigure(v.bar_fill, fill=theme.BORDER)
                v.bar.coords(v.bar_fill, 0, 0, 0, 4)
                v.tag.configure(text="")
                continue
            color = theme.mood_color(mood)
            red = mood <= 0
            v.mood_label.configure(text=theme.fmt_mood(mood), fg=color)
            v.tag.configure(text="红脸·技能失效" if red else "")
            bg = theme.DANGER_SOFT if red else theme.PANEL_ALT
            v.frame.configure(highlightbackground=theme.DANGER if red else theme.BORDER, bg=bg)
            v.name_label.configure(bg=bg)
            v.mood_label.configure(bg=bg)
            v.tag.configure(bg=bg)
            v.bar.configure(bg=bg)
            frac = max(0.0, min(1.0, float(mood) / 24.0))
            v.bar.coords(v.bar_fill, 0, 0, max(1, int(v.bar_w * frac)), 4)
            v.bar.itemconfigure(v.bar_fill, fill=color)
