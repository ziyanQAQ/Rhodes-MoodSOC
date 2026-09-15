"""ui/chart.py —— 心情曲线图（tkinter Canvas 手绘，无第三方绘图库）。

要求是"精确直观"，所以：

- **曲线用折叠线的原始节点画**（`Trajectory.times/moods` 就是事件时刻的精确取值），
  不做等距采样——等距采样会把折线的拐点抹平，反而不精确。
- 横轴是时间（可覆盖多个周期），纵轴心情 0~24；每 6 点一条主网格，班次边界画虚线并标注，
  红脸区间铺红底、曲线本身也变红加粗。
- 鼠标悬停给出**十字准星 + 精确读数**（时刻 / 心情 / 所在设施），最低点自动标注。
"""
from __future__ import annotations

import tkinter as tk
from decimal import Decimal
from typing import Callable, Optional

from . import theme

M_LEFT, M_RIGHT, M_TOP, M_BOTTOM = 48, 18, 28, 34
DAY_ROW_H = 14            # 跨天时，刻度下面再留一行写「第N天」
TICK_MIN_GAP = 46         # 相邻刻度至少这么多像素（`HH:MM` 约 34px + 间距），免得叠字


class MoodChart(tk.Canvas):
    """干员心情曲线控件（`set_data` 给数据，`set_cursor` 只移动当前时刻线）。"""

    def __init__(self, master, **kw):
        kw.setdefault("bg", theme.PANEL)
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", theme.BORDER)
        super().__init__(master, **kw)
        self.traj = None
        self.name = ""
        self.current_t = Decimal("0")
        self.info_provider: Optional[Callable[[Decimal], str]] = None   # t -> 额外信息（所在设施）
        self.on_hover: Optional[Callable] = None                        # (t, mood) -> None
        self._hover_t: Optional[Decimal] = None
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)

    # ------------------------------------------------------------------ 数据
    def set_data(self, traj, name: str):
        self.traj = traj
        self.name = name
        self.redraw()

    def set_cursor(self, t) -> None:
        """只更新"当前时刻"竖线（时间滑动时高频调用，不重画整张图）。"""
        self.current_t = Decimal(str(t))
        if self.traj is None:
            return
        self._draw_cursor()

    def clear(self):
        self.traj = None
        self.delete("all")

    # ------------------------------------------------------------------ 坐标
    def _multi_day(self) -> bool:
        """曲线是否跨天（覆盖超过一个周期）——跨天才需要刻度下面那一行「第N天」。"""
        return bool(self.traj is not None and self.traj.schedule is not None
                    and self.traj.total_hours > self.traj.schedule.cycle_hours)

    def _geom(self):
        w = max(self.winfo_width(), 2)
        h = max(self.winfo_height(), 2)
        x0, x1 = M_LEFT, w - M_RIGHT
        bottom = M_BOTTOM + (DAY_ROW_H if self._multi_day() else 0)
        y0, y1 = M_TOP, h - bottom           # y0=心情24，y1=心情0
        return x0, x1, y0, y1

    def _tx(self, t: Decimal, x0: int, x1: int, total: Decimal) -> float:
        return x0 + float(t / total) * (x1 - x0)

    def _ty(self, mood: Decimal, y0: int, y1: int) -> float:
        m = Decimal(str(mood))
        m = Decimal("0") if m < 0 else (Decimal("24") if m > 24 else m)
        return y1 - float(m / Decimal("24")) * (y1 - y0)

    def _inv_t(self, x, x0, x1, total: Decimal) -> Decimal:
        if x1 <= x0:
            return Decimal("0")
        r = (x - x0) / (x1 - x0)
        r = 0.0 if r < 0 else (1.0 if r > 1 else r)
        return (total * Decimal(str(round(r, 9))))

    # ------------------------------------------------------------------ 绘制
    def redraw(self) -> None:
        self.delete("all")
        w = self.winfo_width()
        if w < 60 or self.traj is None:
            self._draw_placeholder()
            return
        traj = self.traj
        total = traj.total_hours
        x0, x1, y0, y1 = self._geom()
        sched = traj.schedule

        # —— 班次交替底色 + 班次边界 ——
        n_shifts = len(sched.shifts)
        t = Decimal("0")
        idx = 0
        while t < total:
            shift = sched.shifts[idx % n_shifts]
            end = min(t + shift.hours, total)
            ax, bx = self._tx(t, x0, x1, total), self._tx(end, x0, x1, total)
            self.create_rectangle(ax, y0, bx, y1, fill=theme.SHIFT_BAND[idx % 2], outline="")
            self.create_line(bx, y0 - 6, bx, y1, fill=theme.BORDER, dash=(3, 3))
            if bx - ax >= 60:            # 太窄就别写，免得和相邻班次的标题叠在一起
                self.create_text((ax + bx) / 2, y0 - 14,
                                 text=f"{shift.label}  {theme.fmt_hours(shift.hours)}",
                                 fill=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL))
            t, idx = end, idx + 1

        # —— 红脸区间底色 ——
        for a, b in traj.red_face_spans(self.name):
            self.create_rectangle(self._tx(a, x0, x1, total), y0,
                                  max(self._tx(b, x0, x1, total), self._tx(a, x0, x1, total) + 1), y1,
                                  fill=theme.RED_FACE_BAND, outline="")

        # —— 水平网格（每 6 点）+ 纵轴刻度 ——
        for mood in (Decimal("0"), Decimal("6"), Decimal("12"), Decimal("18"), Decimal("24")):
            y = self._ty(mood, y0, y1)
            self.create_line(x0, y, x1, y, fill=theme.BORDER, dash=(2, 4) if mood else ())
            self.create_text(x0 - 8, y, text=theme.fmt_mood(mood), anchor="e",
                             fill=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL))

        # —— 时间刻度（只写 HH:MM；步长按"最多 12 条 + 间距 ≥ 46px"自动选）——
        step_h = self._time_step(total, x1 - x0)
        tick = Decimal("0")
        while tick <= total + Decimal("0.0001"):
            x = self._tx(tick, x0, x1, total)
            self.create_line(x, y1, x, y1 + 4, fill=theme.BORDER)
            # ⚠️ 刻度**不带**「（第N天）」：带上就约 90px 宽，必然和左右刻度叠字
            #    （末尾那条 24:00 最明显）。跨天信息由下面那一行「第N天」表达。
            self.create_text(x, y1 + 16, text=theme.fmt_clock_short(tick, sched.cycle_hours),
                             fill=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL))
            tick += step_h

        # —— 跨天：天分界线（实线）+ 每天区间下面居中标「第N天」——
        if self._multi_day():
            day_start = Decimal("0")
            day_no = 1
            while day_start < total:
                day_end = min(day_start + sched.cycle_hours, total)
                da, db = self._tx(day_start, x0, x1, total), self._tx(day_end, x0, x1, total)
                if day_start > 0:
                    self.create_line(da, y0, da, y1 + 4, fill=theme.MUTED, dash=(4, 2))
                self.create_text((da + db) / 2, y1 + 30, text=f"第{day_no}天",
                                 fill=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_SMALL))
                day_start, day_no = day_end, day_no + 1

        # —— 曲线：直接用事件节点画折线（不采样，保拐点）——
        pts = []
        for tt, vv in zip(traj.times, traj.moods[self.name]):
            pts.extend((self._tx(tt, x0, x1, total), self._ty(vv, y0, y1)))
        if len(pts) >= 4:
            self.create_line(*pts, fill=theme.ACCENT, width=2, joinstyle="round")
        for a, b in traj.red_face_spans(self.name):
            seg = [(self._tx(tt, x0, x1, total), self._ty(vv, y0, y1))
                   for tt, vv in zip(traj.times, traj.moods[self.name]) if a <= tt <= b]
            if len(seg) >= 4:
                self.create_line(*[c for p in seg for c in p], fill=theme.DANGER, width=3)

        # —— 最低点标注（"精确直观"）——
        lo, lo_t, hi, hi_t = traj.bounds(self.name)
        for value, at, label, color in ((lo, lo_t, "最低", theme.DANGER if lo <= 0 else theme.MUTED),
                                        (hi, hi_t, "最高", theme.OK)):
            cx, cy = self._tx(at, x0, x1, total), self._ty(value, y0, y1)
            self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=color, outline="")
            self.create_text(cx + 6, cy - 10 if label == "最高" else cy + 11,
                             text=f"{label} {theme.fmt_mood(value)}", anchor="w",
                             fill=color, font=(theme.FONT_FAMILY, theme.FS_SMALL))

        # —— 进驻事件（换心情）时刻：一条短竖线 + ⇄ 标记 ——
        for mark in traj.marks:
            if mark.kind != "entry" or "未执行" in mark.label:
                continue
            x = self._tx(mark.t, x0, x1, total)
            self.create_line(x, y0, x, y0 + 12, fill=theme.ACCENT, width=2)
            self.create_text(x, y0 - 4, text="⇄", fill=theme.ACCENT,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL))

        # —— 闲置入宿（未满的闲置干员进宿舍）时刻：短竖线 + 床标记 ——
        for mark in traj.marks:
            if mark.kind != "idle" or "未执行" in mark.label:
                continue
            x = self._tx(mark.t, x0, x1, total)
            self.create_line(x, y0, x, y0 + 8, fill=theme.OK, width=2)
            self.create_text(x, y0 + 14, text="宿", fill=theme.OK,
                             font=(theme.FONT_FAMILY, theme.FS_SMALL))

        self._draw_cursor()

    def _time_step(self, total: Decimal, span: float) -> Decimal:
        """选一个"整"刻度步长：最多约 12 条，且相邻间距 ≥ `TICK_MIN_GAP` 像素（防叠字）。"""
        for step in (Decimal("1"), Decimal("2"), Decimal("3"), Decimal("6"), Decimal("12"),
                     Decimal("24"), Decimal("48"), Decimal("72")):
            if total / step <= 12 and float(step / total) * span >= TICK_MIN_GAP:
                return step
        # 候选都不满足（比如特别长的周期）→ 按"能放下几条"反推，至少留 2 格
        n = max(2, int(span // TICK_MIN_GAP))
        return total / n

    def _draw_cursor(self) -> None:
        self.delete("cursor")
        if self.traj is None or self.winfo_width() < 60:
            return
        traj = self.traj
        total = traj.total_hours
        x0, x1, y0, y1 = self._geom()
        t = self.current_t
        if t < 0 or t > total:
            return
        x = self._tx(t, x0, x1, total)
        y = self._ty(traj.mood_at(self.name, t), y0, y1)
        self.create_line(x, y0, x, y1, fill=theme.ACCENT, dash=(4, 3), tags="cursor")
        self.create_oval(x - 4, y - 4, x + 4, y + 4, fill=theme.PANEL,
                         outline=theme.ACCENT, width=2, tags="cursor")

    def _draw_placeholder(self) -> None:
        self.create_text(max(self.winfo_width(), 2) / 2, max(self.winfo_height(), 2) / 2,
                         text="导入排班后在此显示心情曲线",
                         fill=theme.MUTED, font=(theme.FONT_FAMILY, theme.FS_BODY))

    # ------------------------------------------------------------------ 悬停
    def _on_motion(self, event):
        if self.traj is None or self.winfo_width() < 60:
            return
        x0, x1, y0, y1 = self._geom()
        if event.x < x0 or event.x > x1:
            self._on_leave(None)
            return
        t = self._inv_t(event.x, x0, x1, self.traj.total_hours)
        self._hover_t = t
        mood = self.traj.mood_at(self.name, t)
        self.delete("hover")
        y = self._ty(mood, y0, y1)
        self.create_line(x0, y, x1, y, fill=theme.MUTED, dash=(2, 4), tags="hover")
        self.create_oval(event.x - 3, y - 3, event.x + 3, y + 3,
                         fill=theme.TEXT, outline="", tags="hover")
        extra = self.info_provider(t) if self.info_provider else ""
        rate = self.traj.rate_at(self.name, t)
        text = (f"{theme.fmt_clock(t, self.traj.schedule.cycle_hours)}　"
                f"心情 {theme.fmt_mood(mood)}　速率 {theme.fmt_rate(rate)}")
        if extra:
            text += f"　{extra}"
        self.create_text(x0 + 6, y0 + 4, text=text, anchor="nw", fill=theme.TEXT,
                         font=(theme.FONT_FAMILY, theme.FS_SMALL), tags="hover")
        if self.on_hover:
            self.on_hover(t, mood)

    def _on_leave(self, _event):
        self.delete("hover")
        self._hover_t = None
