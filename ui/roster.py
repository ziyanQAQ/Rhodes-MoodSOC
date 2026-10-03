"""ui/roster.py —— 「全员一览」条：把排班里的**所有干员**一屏摆出来（不滚动、不翻页）。

为什么需要它：看板是按"当前班次"画的，只出现在**别的班次**里的干员不会出现在看板上；
而"这套排班能不能永动"恰恰要同时盯着所有人。于是把整个周期出现过的干员
（`Schedule.operator_names()`，示例排班 57 名）按固定顺序铺成芯片网格：

- **固定顺序**（班次出现顺序）——不按心情排序，否则拖动滑块时格子会跳来跳去；
  "谁危险"靠颜色（红→绿）与右上角的"最危险 3 人"文字体现。
- 位置标记（`制1` / `宿3` / `中`…）：显示他**在当前班次**在哪；不在本班次时显示 `休` 并置灰。
- **左键** = 对点（右侧曲线切到该干员）；本控件**只做展示与点选**，不改任何数据
  （设心情走右侧「设置心情…」或「设置 → 干员与心情」——2026-10 起右键设心情已撤掉）。
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

    def __init__(self, master, on_pick: Optional[Callable[[str], None]] = None, **kw):
        kw.setdefault("bg", theme.BG)
        super().__init__(master, **kw)
        self.on_pick = on_pick
        self.chips: List[MoodChip] = []
        self.by_name: Dict[str, MoodChip] = {}
        self._entries: List[str] = []
        self._columns = COLUMNS_MAX
        self._rebuild_job = None
        self._closing = False          # 窗口正在销毁 ⇒ 不再排新的 idle 重建（见 `cancel_pending`）

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

    def set_context(self, tags: Dict[str, str], bench=()) -> None:
        """更新"当前班次在哪"的标记（班次切换时调用）。

        `bench`：**「不在基建」**的干员（既不在工作设施、也不在宿舍）——他们不占任何位置，
        位置标记写成绿色的 `不`（而不是 `休`），一眼能看出"她没在基建里、心情不会动"。
        """
        bench = set(bench or ())
        for v in self.chips:
            if not v.operator:
                continue
            if v.operator in bench:
                v.set_tag("不")
                v.dim = True
                continue
            v.set_tag(tags.get(v.operator, "休"))
            v.dim = v.operator not in tags
        self.hint.configure(
            text=f"{len(self.chips)} 名干员　左键=点选看曲线　"
                 f"（位置标记＝当前班次所在房间，「休」=本班次未排班，"
                 f"「不」=不在基建（心情不变））")

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
        if self._closing:                  # 窗口在销毁：排了也没人跑，还会刷 invalid command name
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

    def cancel_pending(self) -> None:
        """**窗口销毁前**必须调：撤掉挂着的 `after_idle` 重建，且**从此不再排新的**。

        ⚠️ 不撤的话，`await idle` 到点时会去调一个**已被销毁**的 Tcl 命令，Tk 直接往 stderr 喷
        `invalid command name "..._deferred_rebuild"`（回调里的 `winfo_exists()` 挡不住它 ——
        错误发生在**进入 Python 回调之前**）。

        ⚠️ 还要**上闩**（`_closing`）：销毁过程中 Tk 仍会派发 `<Configure>`
        （实测 `Tk.destroy()` → 逐个销毁子控件时收到一个真实宽度 1560 的 Configure），
        `_on_resize` 会因此**又排一个新的** idle 重建 —— 只撤一次是不够的（实测撤完还留一个）。
        回归见 `tests/test_ui_app_smoke.TestGC只在主线程跑`。
        """
        self._closing = True
        if self._rebuild_job is not None:
            try:
                self.after_cancel(self._rebuild_job)
            except tk.TclError:
                pass
            self._rebuild_job = None

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
                            on_left=lambda n=name: self._pick(n))
            chip.grid(row=i // columns, column=i % columns, sticky="ew", padx=1, pady=1)
            chip.set(name, mood=None, tag="")
            self.chips.append(chip)
            self.by_name[name] = chip

    def _pick(self, name: str) -> None:
        if self.on_pick:
            self.on_pick(name)

    # ------------------------------------------------------------------ 联动
    def flash(self, name: str) -> bool:
        chip = self.by_name.get(name)
        if chip is None:
            return False
        chip.flash()
        return True
