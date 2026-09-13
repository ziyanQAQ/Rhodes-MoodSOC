"""mood_soc/battery.py —— BMS-SOC 安时积分法核心（纯数学层，与游戏规则无关）。

把"心情"抽象为一块电池：
  - 容量（capacity）= 心情上限 24
  - SOC（State of Charge，荷电状态）= 当前心情
  - 放电电流 = 净心情消耗速率（心情 / 时）
  - 充电电流 = 净心情回复速率（心情 / 时）

核心公式（安时积分法 / 库仑计数，Ampere-Hour Integration）：

    SOC(t) = SOC(t0) - ∫_{t0}^{t} I(τ) dτ

其中 I(τ) 为净电流。约定：放电为正、充电为负，即
    I = 心情消耗速率 - 心情回复速率  （>0 心情下降，<0 心情上升）。

离散化后每一步：SOC -= I * Δt，并钳位到 [0, capacity]。

精度策略：全程使用 decimal.Decimal 做十进制精确运算，避免 float 无法精确表示
0.1 / 0.3 / 0.05 等十进制小数带来的累积误差；to_decimal() 是统一的安全转换入口
（float 经字符串转换，杜绝二进制近似混入）。
"""
from decimal import Decimal

ZERO = Decimal("0")
INF = Decimal("Infinity")


def to_decimal(value) -> Decimal:
    """把 int / float / str / Decimal / bool 安全转为 Decimal。

    关键：float 走 str 转换（Decimal(str(0.1)) == Decimal("0.1") 精确），
    而不是 Decimal(0.1)（会把浮点二进制近似一并带进来）。
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    return Decimal(str(value))


def clamp(value, low, high):
    """把 value 限制在 [low, high] 区间（入参应为 Decimal / 可比较的数值）。"""
    return max(low, min(high, value))


def ampere_hour_integration(soc0, net_rate, dt, capacity=Decimal("24")):
    """安时积分法的一步积分（自动把入参转为 Decimal）。

    参数：
        soc0      初始心情（SOC）
        net_rate  净消耗速率（点 / 时）：>0 心情下降，<0 心情上升
        dt        经过的时间（小时）
        capacity  电池容量（心情上限，默认 24）
    返回：
        dt 时间后的 SOC（Decimal），已钳位到 [0, capacity]。
    """
    soc0 = to_decimal(soc0)
    net_rate = to_decimal(net_rate)
    dt = to_decimal(dt)
    capacity = to_decimal(capacity)
    return clamp(soc0 - net_rate * dt, ZERO, capacity)


class MoodBattery:
    """一块"心情电池"，封装安时积分与时间推算（内部全 Decimal）。"""

    def __init__(self, capacity=Decimal("24"), soc=None):
        self.capacity = to_decimal(capacity)
        # 未指定初始 SOC 时，默认满电
        self.soc = to_decimal(soc if soc is not None else capacity)

    # ------------------------------------------------------------------ 积分
    def integrate(self, net_rate, dt):
        """按安时积分法推进 dt 小时，直接更新自身 SOC 并返回。"""
        self.soc = ampere_hour_integration(self.soc, net_rate, dt, self.capacity)
        return self.soc

    # ------------------------------------------------------------------ 推算
    def time_to_empty(self, net_rate):
        """以恒定净消耗速率 net_rate（>0）放电到空（SOC=0）还需多久。

        若 net_rate <= 0（回复不小于消耗，即"休息中 / 永续"），返回 Decimal('Infinity')。
        """
        net_rate = to_decimal(net_rate)
        if net_rate <= ZERO:
            return INF
        return self.soc / net_rate

    def time_to_full(self, net_recovery):
        """以恒定净回复速率 net_recovery（>0）充电到满（SOC=capacity）还需多久。"""
        net_recovery = to_decimal(net_recovery)
        if net_recovery <= ZERO:
            return INF
        return (self.capacity - self.soc) / net_recovery

    @property
    def percent(self):
        """归一化电量百分比（0~100）。"""
        return Decimal("100") * self.soc / self.capacity
