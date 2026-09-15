"""ui/theme.py —— 界面的配色 / 字体 / 间距令牌（"简洁明了"集中在一处改）。

只放"长什么样"，不放任何交互逻辑。tkinter 的 ttk 在 Windows 上默认 `vista` 主题，
圆角与阴影不可控、控件看起来偏"系统"；这里统一切到 `clam` 并自己配一套扁平配色，
观感更干净、跨机器一致。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

# ============================================================================
# 配色（浅色、低饱和背景 + 单一强调色）
# ============================================================================
BG = "#f4f5f7"           # 窗口底色
PANEL = "#ffffff"        # 卡片 / 面板
PANEL_ALT = "#fafbfc"    # 次级面板（表头、条带）
BORDER = "#e2e5ea"       # 分隔线 / 卡片描边
TEXT = "#1f2328"         # 主文字
MUTED = "#6b7280"        # 次要文字
ACCENT = "#2f6fed"       # 强调色（选中、曲线、滑块）
ACCENT_SOFT = "#e8efff"  # 强调色的浅底
OK = "#16a34a"
DANGER = "#dc2626"
DANGER_SOFT = "#fdecec"
SHIFT_BAND = ("#f7f8fa", "#eef1f5")   # 班次交替底色（让"几班"一眼可辨）
RED_FACE_BAND = "#fdecec"             # 红脸区间底色

# 心情 → 颜色（0 红脸 → 24 满）
_MOOD_STOPS = (
    (Decimal("0"), "#dc2626"),
    (Decimal("6"), "#f97316"),
    (Decimal("12"), "#f59e0b"),
    (Decimal("18"), "#22c55e"),
    (Decimal("24"), "#15803d"),
)

# 字体（Windows 上都有；拿不到就退回 Tk 默认）
FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"
FS_TITLE = 11
FS_BODY = 10
FS_SMALL = 9
FS_BIG = 13

# 间距
PAD = 10
GAP = 8
CARD_PAD = 8
RADIUS = 8

# 紧凑芯片（位置/干员的统一呈现单元）：一屏能放下"所有房间 + 所有干员"的关键
CHIP_H = 21             # 芯片高度
CHIP_MIN_W = 92         # 芯片最小宽度（看板里的位置）
ROSTER_CHIP_W = 116     # 「全员一览」条里的芯片宽度
CHIP_BAR_W = 4          # 芯片左侧的心情色条宽度


def _rgb(color: str):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, int(round(c)))):02x}" for c in rgb)


def blend(color_a: str, color_b: str, ratio: float) -> str:
    """两色线性混合（ratio=0 取 a，ratio=1 取 b）。"""
    a, b = _rgb(color_a), _rgb(color_b)
    return _hex(tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3)))


def mood_tint(value) -> str:
    """心情 → **芯片底色**（心情色向白大量混，保证黑字可读）。"""
    return blend(mood_color(value), "#ffffff", 0.84)


def mood_ink(value) -> str:
    """心情 → **芯片上的文字/描边色**（心情色加深，红脸更醒目）。"""
    return blend(mood_color(value), "#111827", 0.35)


def mood_color(value) -> str:
    """心情值 → 颜色（红色→橙色→琥珀→绿，线性插值）。"""
    v = Decimal(str(value))
    v = Decimal("0") if v < 0 else (Decimal("24") if v > 24 else v)
    for i in range(len(_MOOD_STOPS) - 1):
        x0, c0 = _MOOD_STOPS[i]
        x1, c1 = _MOOD_STOPS[i + 1]
        if x0 <= v <= x1:
            if x1 == x0:
                return c1
            r = (v - x0) / (x1 - x0)
            a = _rgb(c0)
            b = _rgb(c1)
            return _hex(tuple(a[k] + (b[k] - a[k]) * float(r) for k in range(3)))
    return _MOOD_STOPS[-1][1]


def fmt_mood(value, places: int = 2) -> str:
    """心情值的**显示边界**（唯一舍入处）：0.01 精度，去掉多余的 0。

    引擎内部是 Decimal 精确运算，"跨事件分割"会留下 `16.19999999999999999999999999`
    这类 28 位尾巴——那是精度极限、不是算错，故只在显示时舍入。
    """
    q = Decimal(str(value)).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return str(int(q))
    return f"{q:f}".rstrip("0").rstrip(".")


def fmt_clock(hours, cycle: Decimal = Decimal("24")) -> str:
    """把"周期内时刻"格式化为 `HH:MM`（支持跨周期：36.5 → `12:30（第2天）`）。"""
    h = Decimal(str(hours))
    day = int(h // cycle)
    within = h - cycle * day
    hh = int(within)
    mm = int((within - hh) * 60)
    if mm == 60:                      # 舍入兜底
        hh, mm = hh + 1, 0
    label = f"{hh:02d}:{mm:02d}"
    return f"{label}（第{day + 1}天）" if day else label


def fmt_clock_short(hours, cycle: Decimal = Decimal("24")) -> str:
    """只要 `HH:MM`（**不带**「（第N天）」）——图表横轴刻度用。

    为什么单独一个：横轴刻度间距只有几十像素，带上天数的标签（约 90px）必然和左右
    刻度叠在一起（末尾那条 `24:00` 尤其明显）。跨天信息改由"另起一行标第N天 + 天分界线"表达。
    """
    h = Decimal(str(hours)) % cycle
    hh = int(h)
    mm = int((h - hh) * 60)
    if mm == 60:                      # 舍入兜底
        hh, mm = hh + 1, 0
    return f"{hh:02d}:{mm:02d}"


def fmt_rate(value, places: int = 2) -> str:
    """心情变化速率 → 人话（**点/时**）：`↓2/时`（在降）/ `↑1.5/时`（在升）/ `0/时`（不变）。

    口径同引擎：`I = 消耗 − 回复`，`I > 0` 下降。这里按"看曲线的人"的习惯加箭头。
    """
    v = Decimal(str(value))
    if v > 0:
        return "↓" + fmt_mood(v, places) + "/时"
    if v < 0:
        return "↑" + fmt_mood(-v, places) + "/时"
    return "0/时"


def fmt_hours(hours) -> str:
    """小时数的紧凑显示（12 / 12.5 / 0.25）。"""
    return fmt_mood(hours, 3) + "h"


def fmt_mins(hours) -> str:
    """把"小时"换算成人话的时长（0.25 → `15 分钟`；1.5 → `1 小时 30 分`）。"""
    minutes = int((Decimal(str(hours)) * 60).to_integral_value(rounding=ROUND_HALF_UP))
    if minutes < 60:
        return f"{minutes} 分钟"
    h, m = divmod(minutes, 60)
    return f"{h} 小时" if m == 0 else f"{h} 小时 {m} 分"
