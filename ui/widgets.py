"""ui/widgets.py —— 通用小控件：**心情芯片**（看板位置与「全员一览」共用的呈现单元）。

一个芯片 = 一条 21px 高的横条：

```
┌────────────────────────────────┐
│▌ 制1  名字            19.5     │  ▌=心情色条（红→绿）  底色=该心情的浅色调
└────────────────────────────────┘
   ↑位置标记（可空；不在本班次时显示"休"）
```

这么小是为了让"**所有房间 + 所有干员一屏看完**"成立：控制中枢 5 人只占 5×21px，
4 间宿舍 20 人也只占 20×21px，不需要滚动。

左键 / 右键回调由调用方给（看板：左=换人、右=设心情；全员一览：左=对点看曲线）。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from typing import Callable, Optional

from . import theme

# 底色（心情色浅色调）的分桶粒度：跨过 2 点才重绘底色。
# 拖动滑块时心情每步只变 ~0.3 点，于是"数值跟手变、底色偶尔重绘"，既直观又不卡。
TINT_BUCKET = Decimal("2")


class MoodChip(tk.Frame):
    """一个位置/一名干员的紧凑显示：心情色条 + 位置标记 + 名字 + 心情值。"""

    def __init__(self, master, width: int = theme.CHIP_MIN_W, height: int = theme.CHIP_H,
                 on_left: Optional[Callable[[], None]] = None,
                 on_right: Optional[Callable[[], None]] = None,
                 show_tag: bool = False):
        super().__init__(master, width=width, height=height,
                         bg=theme.PANEL_ALT, highlightthickness=1,
                         highlightbackground=theme.BORDER, cursor="hand2")
        self.pack_propagate(False)
        self.operator: Optional[str] = None
        self.tag_text = ""
        self.last_mood = None
        self.dim = False
        self.selected = False
        self._paint_key = None
        self._tint_bucket = None

        self.bar = tk.Frame(self, width=theme.CHIP_BAR_W, bg=theme.BORDER)
        self.bar.pack(side="left", fill="y")
        self.tag = tk.Label(self, text="", bg=theme.PANEL_ALT, fg=theme.MUTED, width=3,
                            font=(theme.FONT_FAMILY, theme.FS_SMALL), anchor="w")
        if show_tag:
            self.tag.pack(side="left", padx=(3, 0))
        self.value = tk.Label(self, text="", bg=theme.PANEL_ALT, fg=theme.MUTED, width=5,
                              font=(theme.FONT_MONO, theme.FS_SMALL), anchor="e")
        self.value.pack(side="right", padx=(0, 4))
        self.name = tk.Label(self, text="", bg=theme.PANEL_ALT, fg=theme.TEXT,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL), anchor="w")
        self.name.pack(side="left", fill="x", expand=True, padx=(4, 0))

        for w in (self, self.bar, self.tag, self.name, self.value):
            if on_left:
                w.bind("<Button-1>", lambda _e: on_left())
            if on_right:
                w.bind("<Button-3>", lambda _e: on_right())

    # ------------------------------------------------------------------ 内容
    def set(self, operator: Optional[str], mood=None, tag: str = "", dim: bool = False,
            name_limit: int = 5, badge: str = "") -> None:
        """设置内容。`operator=None` = 空位；`mood=None` = 该干员此刻不在基建内。

        `badge`：跟着名字显示的小角标（**只有非精英化二才给**，如 `E1`）——
        练度不够会让技能不生效，"一眼看出谁没满练"比翻设置有用。
        """
        self.operator = operator
        self.tag_text = tag
        self.tag.configure(text=tag)
        self.dim = dim
        self._paint_key = None                     # 强制重绘
        self._badge = badge
        self._name_limit = name_limit
        if operator is None:
            self.name.configure(text="＋ 空位", fg=theme.MUTED)
            self.last_mood = mood
            self.value.configure(text="")
            self.bar.configure(bg=theme.BORDER)
            self._paint_bg(theme.PANEL_ALT, theme.BORDER)
            return
        self.name.configure(text=self._name_text(), fg=theme.TEXT)
        self.update_mood(mood)

    def _name_text(self) -> str:
        """名字 + 练度角标（角标占位，所以有角标时名字截得更短）。"""
        operator = self.operator or ""
        badge = getattr(self, "_badge", "")
        limit = getattr(self, "_name_limit", 5)
        if badge:
            limit = max(2, limit - len(badge) - 1)
        label = operator if len(operator) <= limit else operator[: limit - 1] + "…"
        return f"{label} {badge}" if badge else label

    def set_badge(self, badge: str) -> None:
        """只更新练度角标（不动心情）——「全员一览」重建网格后用得上。"""
        if badge == getattr(self, "_badge", ""):
            return
        self._badge = badge
        if self.operator:
            self.name.configure(text=self._name_text(), fg=theme.TEXT)

    def update_mood(self, mood, dim: Optional[bool] = None, quick: bool = False) -> None:
        """更新心情。

        - `quick=True`：**拖动时的跟手路径** —— 只改数值文字与色条（2 次 Tcl 调用），
          底色仅当跨过 `TINT_BUCKET` 分桶时才重绘。整屏 107 个芯片也只花几毫秒。
        - `quick=False`：完整上色（数值 + 色条 + 底色 + 红脸描边）。
        """
        if dim is not None:
            self.dim = dim
        if mood is None:
            if self._paint_key == (None, self.dim):
                return
            self._paint_key = (None, self.dim)
            self.last_mood = None
            self.value.configure(text="—", fg=theme.MUTED)
            self.bar.configure(bg=theme.BORDER)
            self._paint_bg(theme.PANEL_ALT, theme.BORDER)
            self._tint_bucket = None
            return

        key = (str(mood), self.dim)
        if key != self._paint_key:
            self._paint_key = key
            self.last_mood = mood
            red = Decimal(str(mood)) <= 0
            self.value.configure(text=theme.fmt_mood(mood),
                                 fg=theme.MUTED if self.dim else theme.mood_ink(mood))
            self.bar.configure(bg=(theme.blend(theme.mood_color(mood), "#ffffff", 0.5)
                                   if self.dim else theme.mood_color(mood)))
            if red:
                self._paint_bg(theme.DANGER_SOFT, theme.DANGER)
                self._tint_bucket = "red"
                return
        if quick and self._tint_bucket is not None and self._tint_bucket != "red":
            bucket = int(Decimal(str(mood)) // TINT_BUCKET)
            if bucket == self._tint_bucket:
                return                                  # 底色不用动，跟上就好
        red = Decimal(str(mood)) <= 0
        if red:
            self._paint_bg(theme.DANGER_SOFT, theme.DANGER)
            self._tint_bucket = "red"
        elif self.dim:
            self._paint_bg(theme.PANEL_ALT, theme.BORDER)
            self._tint_bucket = "dim"
        else:
            self._paint_bg(theme.mood_tint(mood), theme.BORDER)
            self._tint_bucket = int(Decimal(str(mood)) // TINT_BUCKET)

    def set_tag(self, tag: str) -> None:
        if tag != self.tag_text:
            self.tag_text = tag
            self.tag.configure(text=tag)

    def _paint_bg(self, bg: str, border: str) -> None:
        self.configure(bg=bg, highlightbackground=self._border_for(border))
        for w in (self.name, self.value, self.tag):
            w.configure(bg=bg)

    def _border_for(self, border: str) -> str:
        """选中态优先显示强调色描边。"""
        return theme.ACCENT if self.selected else border

    def set_selected(self, selected: bool) -> None:
        """选中态（「全员一览」里表示"当前曲线看的就是他"）。"""
        self.selected = selected
        if selected:
            self.configure(highlightbackground=theme.ACCENT, highlightthickness=2)
        else:
            red = self.last_mood is not None and Decimal(str(self.last_mood)) <= 0
            self.configure(highlightthickness=1,
                           highlightbackground=theme.DANGER if red else theme.BORDER)

    def flash(self) -> None:
        """短暂高亮（"从全员一览跳到看板位置"的视觉提示）。

        ⚠️ 定时器挂在 **toplevel** 上而不是本控件上：看板重建时本控件会被销毁，
        挂在控件上的 `after` 会变成 Tk 的 `invalid command name` 噪声。
        """
        self.configure(highlightbackground=theme.ACCENT, highlightthickness=2)
        self.winfo_toplevel().after(1200, self._unflash)

    def _unflash(self) -> None:
        if self.winfo_exists():            # 控件可能已被销毁（换班次/退出）
            self.set_selected(self.selected)
