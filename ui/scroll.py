"""ui/scroll.py —— 竖向滚动的**统一做法**（Canvas + Scrollbar + 滚轮 + 键盘）。

界面里有几处"内容可能比可视区高"的表：看板、全员一览、干员与心情表、闲置入宿表。
它们的滚动都要做对同样四件事：

1. **滚轮只在指针位于这块表上时生效**——不能用 `bind_all`（那会抢走整个窗口的滚轮，
   悬停在曲线上滚一下看板也跟着滚，见 `documents/07-图形界面.md` §7.1）；
2. **滚动条本体也要能滚**（鼠标停在滚动条上滚滚轮，人的预期是也可以滚）；
3. **滚不动就别吃掉事件**——内容装得下时 `return None`，让事件继续传下去，
   免得出现"滚了没反应"的错觉；
4. **一次滚动要够快**（`UNITS_PER_NOTCH = 3` 行）：61 行的表按 1 行/格要从头滚到尾
   60+ 次；3 行/格是"能快速上下浏览"的底线，再配 `Shift`（整页）/ `Ctrl`（10 行）
   与 `PageUp/PageDown/Home/End`。

做法是给这块表的控件挂一个**本实例专属的 bindtag**（`MoodVScroll<id>`），
再用 `bind_class(那个 tag, "<MouseWheel>", ...)` 接事件。
⚠️ tag 必须每个实例一个：两张表若共用同一个 tag，后注册的那个处理函数会**同时接管**
两张表的滚轮（`bind_class` 是按 tag 全局生效的）。
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional

# 每实例一个 tag 的前缀（真正的 tag = 前缀 + id(self)）
TAG_PREFIX = "MoodVScroll"

#: 一次滚轮事件滚几行（**全局口径**：所有可滚动表共用）。
#: 为什么是 3：1 行/格时 61 行的表要滚 60+ 次才到底（"滚不动"的体感），
#: 3 行/格一次能看清跳了几行，又不会跳过内容。
UNITS_PER_NOTCH = 3
#: `Ctrl+滚轮` 的加速档（一次 10 行）
FAST_UNITS = 10
#: "滚完了"的判定间隔（毫秒）：滚轮停下这么久、位置还在原地，才算滚动结束
#: （期间 `VScroll.scrolling` 为 `True`，调用方可以据此关掉悬停高亮等装饰）
SCROLL_IDLE_MS = 120


class VScroll:
    """把「Canvas + 可选滚动条 + 内层 Frame」这一套接成可滚动的表。

    用法：

    ```python
    self.canvas = tk.Canvas(body, ...)
    scroll = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
    self.canvas.configure(yscrollcommand=scroll.set)
    self.inner = tk.Frame(self.canvas)
    self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
    self.vs = VScroll(self.canvas, scroll, self.inner)
    ...
    self.vs.join(w)            # ⚠️ 每个"行容器"都要 join；没有行容器就逐格 join（见下）
    ```

    ## 接线：**凡是可能出现指针的表内控件都要 join**

    `join(w)` 做的是"往 `w` 的 bindtags 末尾追加本实例的 tag"，事件因此沿
    「控件 → 控件类 → 所属 toplevel → all → 本 tag」走到 `on_wheel`。
    链子**不含父控件**，所以：

    ⚠️ 踩过的三条，别再改回去：
      · **只挂 `canvas` / `inner` 不够**。指针停在某个 `Label` 上时，事件走的是
        那个 Label 自己的链（Label → Label 类 → toplevel → all），**到不了**挂在
        `inner` 上的 tag（实测：行里的 `op` 标签 bindtags 不含本 tag → 滚轮无效）。
      · **有"行 Frame"的表**：`join(row)` 一次就够 —— 行里的标签事件会先在**行**的
        bindtags 链上跑一遍（行有本 tag），处理函数在上面就 `break` 掉了。不用逐格挂。
      · **没有行 Frame 的表**（`ui/batch.py` 的表格现在是"所有单元格共享一个 grid"，
        为了跨行对齐）：**必须逐格 `join`** —— 一行 6 个控件 × 78 行 ≈ 470 次
        `bindtags` 重建，实测整表重建 254ms，其中这部分可以忽略；反之漏挂的表现是
        "指针停在文字上滚不动、停在行间空隙反而能动"。
    """

    def __init__(self, canvas: tk.Canvas, scrollbar: Optional[object] = None,
                 inner: Optional[tk.Widget] = None, win: Optional[int] = None,
                 base_tag: str = TAG_PREFIX, units_per_notch: int = UNITS_PER_NOTCH,
                 keyboard: bool = True):
        self.canvas = canvas
        self.scrollbar = scrollbar
        self.inner = inner
        self.win = win                      # canvas.create_window(...) 的 item id
        self.units_per_notch = max(1, int(units_per_notch))
        self.tag = f"{base_tag}{id(self)}"
        #: 正在滚动？（调用方可以据此把"悬停高亮"这类装饰暂时关掉——见 `ui/batch.py`）
        self.scrolling = False
        self._scroll_job = None
        self._size_sig = None               # 上次的 (内容高, 内容宽, 可视高)：没变就不重设
        canvas.bind_class(self.tag, "<MouseWheel>", self.on_wheel)
        if keyboard:
            # 键盘也能滚（鼠标停在表上就能用，不必先点一下）：整页 / 首尾
            for seq, handler in (("<Prior>", self.page_up), ("<Next>", self.page_down),
                                 ("<Home>", self.to_top), ("<End>", self.to_bottom)):
                canvas.bind_class(self.tag, seq, handler)
        # 内容高度变化 → 刷新滚动区间（调用方也可以自己绑，这里是兜底）
        if inner is not None:
            inner.bind("<Configure>", lambda _e: self.refresh(settle=False), add="+")
        # 容器三个 + **每一行**（行由调用方在重建时 `join(row)`）：
        # 事件的 bindtags 链不含父控件，所以只挂容器挡不住"指针停在行里的标签上"。
        for w in (canvas, scrollbar, inner):
            if w is not None:
                self.join(w)
        # ⚠️ 还要**延后刷一次**：控件刚建出来时几何还没落定，那时算不出内容高度，
        #    `scrollregion` 会一直是 1×1 → 滚动条拖不动、滚轮也没反应
        #    （实测：闲置入宿那张表就踩了这个）。
        canvas.after_idle(self.refresh)

    # ------------------------------------------------------------------ 接线
    def refresh(self, settle: bool = True) -> None:
        """重算滚动区间（重建过表内容的调用方应当显式调一次）。

        ⚠️ 三个坑，都是实测踩出来的：
        1. 用 **`winfo_reqheight()`** 而不是 `bbox("all")`：几何没落定时 `bbox` 会给 1×1；
        2. 读之前要 `update_idletasks()`（`settle=True`），否则"请求尺寸"还没算出来；
        3. 还要 **`itemconfigure(win, height=…)`**：canvas 的 window item **不会**自己
           跟着子控件长高（它保持创建时的大小），所以必须显式把内容高度写回去。
        """
        if self.inner is None:
            return
        try:
            if settle:
                self.inner.update_idletasks()
            h = self.inner.winfo_reqheight()
            w = max(self.inner.winfo_reqwidth(), self.canvas.winfo_width())
            # **尺寸签名缓存**：内容高宽/可视高度都没变就直接返回。
            # 滚动过程中 `inner` 会不停收到 `<Configure>`（视口在动），每次都
            # `itemconfigure` + `scrollregion` 是白花的（实测这部分就是"滚一下顿一下"）。
            sig = (h, w, self.canvas.winfo_height())
            if sig == self._size_sig:
                return
            self._size_sig = sig
            if h > 1:
                if self.win is not None:
                    self.canvas.itemconfigure(self.win, height=h)
                self.canvas.configure(scrollregion=(0, 0, w, h))
                return
            self._size_sig = None
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        except tk.TclError:
            self._size_sig = None

    def join(self, widget: tk.Widget) -> None:
        """把某个控件纳入滚轮作用域（滚动条、行、行里的标签都要）。"""
        try:
            tags = list(widget.bindtags())
        except tk.TclError:
            return
        if self.tag not in tags:
            widget.bindtags(tuple(tags) + (self.tag,))

    def _on_inner_configure(self, _event=None) -> None:
        self.refresh()

    # ------------------------------------------------------------------ 滚轮
    def fits(self) -> bool:
        """内容装得下（不需要滚动）？"""
        if self.inner is None:
            return True
        try:
            return self.inner.winfo_reqheight() <= self.canvas.winfo_height()
        except tk.TclError:
            return True

    @staticmethod
    def _steps(event) -> int:
        """一次滚轮事件的方向与格数（Windows 是 ±120 的倍数；X11 用 Button-4/5）。"""
        delta = getattr(event, "delta", 0) or 0
        if delta:
            return -int(delta / 120) or (1 if delta > 0 else -1)
        num = getattr(event, "num", 0)
        return -1 if num == 4 else (1 if num == 5 else 0)

    def _units(self, event) -> int:
        """这次要滚几行：默认 `units_per_notch` 行；`Shift` = 整页；`Ctrl` = `FAST_UNITS` 行。

        `Shift` 优先于 `Ctrl`（两者都按住时按"整页"理解，页比 10 行更符合直觉）。
        """
        steps = self._steps(event)
        if not steps:
            return 0
        state = getattr(event, "state", 0) or 0
        if state & 0x0001:                       # Shift
            return self._page_lines() * (1 if steps > 0 else -1)
        if state & 0x0004:                       # Control
            return FAST_UNITS * (1 if steps > 0 else -1)
        return steps * self.units_per_notch

    def _page_lines(self) -> int:
        """一屏大约几行（拿可视高度除以单行高度）。

        下限取 `2 × units_per_notch`：小窗口里算出来只有 3~4 行时，"整页"和普通滚轮
        几乎没区别，Shift 就没意义了（实测过）。真正的整页由 `yview_scroll` 自己钳位。
        """
        row_h = self._row_height()
        try:
            visible = max(self.canvas.winfo_height(), 1)
        except tk.TclError:
            visible = 1
        return max(2 * self.units_per_notch, visible // max(row_h, 1) - 1)

    def _row_height(self) -> int:
        """单行高度（取内层第一个子控件；取不到就当 24px）。"""
        if self.inner is None:
            return 24
        try:
            kids = self.inner.winfo_children()
            if kids:
                return max(kids[0].winfo_reqheight(), 1)
        except tk.TclError:
            pass
        return 24

    def on_wheel(self, event):
        """滚轮处理：滚不动就**不吃事件**（返回 None）。"""
        if self.fits():
            return None
        units = self._units(event)
        if not units:
            return None
        return self._scroll_lines(units)

    def _scroll_lines(self, lines: int):
        """按**行**滚（而不是按像素）：滚起来"一格一格"，视觉稳。

        滚动期间把 `scrolling` 置位（调用方据此暂时关掉悬停高亮等装饰，省掉几十次重绘），
        滚完（120ms 内位置不再变）自动复位。
        """
        try:
            self.canvas.yview_scroll(int(lines), "units")
        except tk.TclError:
            return None
        self._mark_scrolling()
        return "break"

    def _mark_scrolling(self) -> None:
        self.scrolling = True
        try:
            self._scroll_pos = int(self.canvas.canvasy(0))
            if self._scroll_job is not None:
                self.canvas.after_cancel(self._scroll_job)
            self._scroll_job = self.canvas.after(SCROLL_IDLE_MS, self._check_scroll_idle)
        except tk.TclError:
            self.scrolling = False

    def _check_scroll_idle(self) -> None:
        """120ms 后检查位置是否还在变：不动了才算"滚完"。"""
        self._scroll_job = None
        try:
            pos = int(self.canvas.canvasy(0))
        except tk.TclError:
            self.scrolling = False
            return
        if pos != getattr(self, "_scroll_pos", pos):
            self._mark_scrolling()          # 还在滚 → 再等一轮
            return
        self.scrolling = False

    # ------------------------------------------------------------------ 键盘
    def page_up(self, _event=None):
        return self._scroll_lines(-self._page_lines())

    def page_down(self, _event=None):
        return self._scroll_lines(self._page_lines())

    def to_top(self, _event=None):
        try:
            self.canvas.yview_moveto(0.0)
        except tk.TclError:
            return None
        return "break"

    def to_bottom(self, _event=None):
        try:
            self.canvas.yview_moveto(1.0)
        except tk.TclError:
            return None
        return "break"


__all__ = ["VScroll", "TAG_PREFIX", "UNITS_PER_NOTCH", "FAST_UNITS", "SCROLL_IDLE_MS"]
