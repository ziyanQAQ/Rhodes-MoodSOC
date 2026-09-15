"""ui/roster.py —— 「全员一览」条：把排班里的**所有干员**一屏摆出来（不滚动、不翻页）。

为什么需要它：看板是按"当前班次"画的，只出现在**别的班次**里的干员不会出现在看板上；
而"这套排班能不能永动"恰恰要同时盯着所有人。于是把整个周期出现过的干员
（`Schedule.operator_names()`，示例排班 57 名）按固定顺序铺成芯片网格：

- **固定顺序**（班次出现顺序）——不按心情排序，否则拖动滑块时格子会跳来跳去；
  "谁危险"靠颜色（红→绿）与右上角的"最危险 3 人"文字体现。
- 位置标记（`制1` / `宿3` / `中`…）：显示他**在当前班次**在哪；不在本班次时显示 `休` 并置灰。
- **左键** = 对点（右侧曲线切到该干员）；**右键** = 设他的心情（周期起点）。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import theme
from .widgets import MoodChip

COLUMNS_MAX = 14


class RosterStrip(tk.Frame):
    """全员一览：芯片网格 + "最危险 3 人"提示。"""

    def __init__(self, master, on_pick: Optional[Callable[[str], None]] = None,
                 on_set_mood: Optional[Callable[[str], None]] = None, **kw):
        kw.setdefault("bg", theme.BG)
        super().__init__(master, **kw)
        self.on_pick = on_pick
        self.on_set_mood = on_set_mood
        self.chips: List[MoodChip] = []
        self.by_name: Dict[str, MoodChip] = {}
        self._entries: List[str] = []
        self._columns = COLUMNS_MAX
        self._rebuild_job = None

        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=theme.PAD)
        tk.Label(head, text="全员一览", bg=theme.BG, fg=theme.TEXT,
                 font=(theme.FONT_FAMILY, theme.FS_TITLE)).pack(side="left")
        self.hint = tk.Label(head, text="", bg=theme.BG, fg=theme.MUTED,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.hint.pack(side="left", padx=(8, 0))
        self.risk = tk.Label(head, text="", bg=theme.BG, fg=theme.DANGER,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL))
        self.risk.pack(side="right")

        self.body = tk.Frame(self, bg=theme.BG)
        self.body.pack(fill="x", padx=theme.PAD, pady=(2, 0))
        self.bind("<Configure>", self._on_resize)

    # ------------------------------------------------------------------ 数据
    def set_operators(self, names: Sequence[str]) -> None:
        """设置要展示的干员（固定顺序），并重建芯片网格。

        这里先按**当前真实宽度**算好列数再建，免得开局用兜底列数建一次、
        布局一落定又重建一次（实测那一次重建要 ~110ms，是"切换时卡一下"的来源之一）。
        """
        self._entries = list(names)
        self._columns = self._columns_for_width(self.winfo_width())
        self._rebuild()

    def set_badges(self, badges: Dict[str, str]) -> None:
        """练度角标：`{干员: "E1"}`（只给非精英化二的；取各周期里**最低**的那档）。"""
        for v in self.chips:
            if v.operator:
                v.set_badge(badges.get(v.operator, ""))

    def _columns_for_width(self, width: int) -> int:
        if width < 200:                       # 还没真正布局，保持上次/兜底列数
            return self._columns or COLUMNS_MAX
        return max(1, min(COLUMNS_MAX, width // (theme.ROSTER_CHIP_W + 6)))

    def set_context(self, tags: Dict[str, str]) -> None:
        """更新"当前班次在哪"的标记（班次切换时调用）。"""
        for v in self.chips:
            if v.operator:
                v.set_tag(tags.get(v.operator, "休"))
                v.dim = v.operator not in tags
        self.hint.configure(
            text=f"{len(self.chips)} 名干员　左键=对点看曲线　右键=设心情　"
                 f"（位置标记＝当前班次所在房间，「休」=本班次未排班）"
                 f"　｜　看板：左键选人/更换/清空·右键设心情")

    def update_moods(self, moods: Dict[str, Decimal], quick: bool = False) -> None:
        """时间滑动时更新所有芯片（含"不在本班次"的：他们的心情由轨迹给出）。"""
        lowest: List[Tuple[Decimal, str]] = []
        for v in self.chips:
            if not v.operator:
                continue
            mood = moods.get(v.operator)
            v.update_mood(mood, quick=quick)
            if mood is not None:
                lowest.append((mood, v.operator))
        if quick:
            return                       # 拖动中不重排/不改提示文字（省一次 configure）
        lowest.sort(key=lambda x: x[0])
        if lowest:
            lo = lowest[0][0]
            head = "　".join(f"{n} {theme.fmt_mood(m)}" for m, n in lowest[:3])
            if lo <= 0:
                self.risk.configure(text=f"⚠ 已红脸：{head}")
            elif lo >= 24:
                self.risk.configure(text="全员满心情")
            else:
                self.risk.configure(text=f"最危险：{head}")

    def set_selected(self, name: str) -> None:
        for v in self.chips:
            v.set_selected(v.operator == name)

    # ------------------------------------------------------------------ 网格
    def _on_resize(self, event) -> None:
        """窗口宽度变了 → 重排列数。

        ⚠️ **不能在 `<Configure>` 处理函数里直接销毁/重建控件**（Tk 正在做几何管理，
        在里面 destroy 会直接让进程崩，实测 0xC0000005）。所以只记下新的列数，
        用 `after_idle` 推到事件循环的空闲时刻再重建。
        """
        if event.width < 200:              # 尚未真正布局完，别用窄宽度把网格压成 1 列
            return
        columns = self._columns_for_width(event.width)
        if columns == self._columns:
            return
        self._columns = columns
        if self._rebuild_job is None:
            self._rebuild_job = self.after_idle(self._deferred_rebuild)

    def _deferred_rebuild(self) -> None:
        self._rebuild_job = None
        if self.winfo_exists():
            self._rebuild()

    def _rebuild(self) -> None:
        if not self._entries:
            return
        columns = self._columns or COLUMNS_MAX
        for w in self.body.winfo_children():
            w.destroy()
        self.chips.clear()
        self.by_name.clear()
        for i in range(columns):
            self.body.columnconfigure(i, weight=1, uniform="roster")
        for i, name in enumerate(self._entries):
            chip = MoodChip(self.body, width=theme.ROSTER_CHIP_W, show_tag=True,
                            on_left=lambda n=name: self._pick(n),
                            on_right=lambda n=name: self._set_mood(n))
            chip.grid(row=i // columns, column=i % columns, sticky="ew", padx=1, pady=1)
            chip.set(name, mood=None, tag="")
            self.chips.append(chip)
            self.by_name[name] = chip

    def _pick(self, name: str) -> None:
        if self.on_pick:
            self.on_pick(name)

    def _set_mood(self, name: str) -> None:
        if self.on_set_mood:
            self.on_set_mood(name)

    # ------------------------------------------------------------------ 联动
    def flash(self, name: str) -> bool:
        chip = self.by_name.get(name)
        if chip is None:
            return False
        chip.flash()
        return True
