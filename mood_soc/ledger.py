"""mood_soc/ledger.py —— 心情**记录系统**：逐条贡献流水账 + 轴 F 叠加。

## 为什么需要它

重构前，`rules.py` 只算出一个 `net_rate` 数字，**无法回答**"这个速率由谁贡献、按什么规则合成"。
调试技能联动只能靠读代码 + 手算，测试也只能断言数字（数字变了不知道为什么变）。

本模块把计算过程变成**可解释的流水账**：

```
MoodLedger
├── consume: [Contribution, ...]   # 每一条消耗来源
└── recover: [Contribution, ...]   # 每一条回复来源
    → total(Bucket.RECOVER) 按轴 F 合成
    → explain() 输出中文逐条解释
```

## 轴 F（叠加规则）在这里统一落地

| Stacking | 语义 | 例子 |
|---|---|---|
| `SUM` | 直接相加 | 不同来源的中枢回复 |
| `SAME_KIND_MAX` | 同一 `group` 内：**同一技能的多个分句先求和，再跨技能取最高** | 宿舍群体回复（官方文本「同种效果取最高」「叠加后的最终值同种效果取最高」） |
| `CROSS_OWNER_MAX` | **同 `max_group` 内先按 owner 求和，再跨 owner 取最高** | 官方术语 `cc.c.sui2_1`：公事公办/孤光共照/巴别塔之帜 |
| `POOL` | 池分配（值已在计算时按人数平摊，这里只做求和与展示） | 冰酿「小酌怡情」 |
| `ZERO_PRIORITY` | 归零优先：清空同 bucket 内指定 `group` 的贡献 | 消除类（槐琥/令）、菲亚梅塔独占 |

> 设计目标：**新增一条叠加规则 = 加一个 Stacking 分支 + 在数据里标出来**，
> 而不是往 `rules.py` 里再塞一个 `max()`。

数值全部 decimal.Decimal。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional

from .battery import ZERO, to_decimal
from .skill_templates import Stacking


class Bucket(str, Enum):
    """贡献落在哪个桶里（对应净速率的两侧）。"""
    CONSUME = "consume"     # 心情消耗（正=更耗）
    RECOVER = "recover"     # 心情回复（正=更回复）
    EVENT = "event"         # 非速率事件（消除/互换/触发恢复），只记录不参与 rate

    @property
    def label(self) -> str:
        return {"consume": "消耗", "recover": "回复", "event": "事件"}[self.value]


@dataclass(frozen=True)
class Contribution:
    """一条贡献：谁（owner）→ 用哪条技能（skill/template）→ 作用于谁（target）→ 值多少。

    字段说明：
      - `group`：**同种取最高**的分组键。同组内取最高（SAME_KIND_MAX）。
        例：所有「宿舍群体回复」同属 `dorm_group` 组，所以 `max` 而不是相加。
      - `max_group`：**跨干员取最高**的分组键（轴 F3）。同组内先按 owner 求和，再跨 owner 取 max。
      - `pool_share`：池分配时实际分给了几个人（展示用：value 已在计算时平摊）。
      - `exclusive`：独占——该桶内只保留本贡献（菲亚梅塔「自律」无视其它一切来源，含宿舍基础回复）。
      - `zeroes_group`：归零——清空该桶内 `group` 等于此值的贡献（消除类）。
    """
    bucket: Bucket
    label: str
    value: Decimal
    group: str = ""
    stacking: Stacking = Stacking.SUM
    owner: str = ""
    target: str = ""
    skill_id: str = ""
    skill_name: str = ""
    template: str = ""
    max_group: str = ""
    pool_share: int = 0
    exclusive: bool = False
    zeroes_group: str = ""
    detail: str = ""

    def source(self) -> str:
        """人类可读的来源描述。"""
        if self.skill_name and self.owner:
            who = self.owner if self.owner != self.target else f"{self.owner}（自身）"
            return f"[{self.template or '—'}] {who}「{self.skill_name}」"
        return f"[{self.template or '基础'}] {self.label}"

    def to_dict(self) -> dict:
        """JSON 友好的表示（value 转字符串，避免 Decimal 序列化问题）。"""
        d = {
            "bucket": self.bucket.value,
            "group": self.group,
            "stacking": self.stacking.value,
            "label": self.label,
            "value": str(self.value),
        }
        for k in ("owner", "target", "skill_id", "skill_name", "template",
                  "max_group", "detail"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        if self.pool_share:
            d["pool_share"] = self.pool_share
        if self.exclusive:
            d["exclusive"] = True
        if self.zeroes_group:
            d["zeroes_group"] = self.zeroes_group
        return d


@dataclass
class MoodLedger:
    """一名干员的心情流水账：消耗与回复的逐条来源 + 按轴 F 合成的合计。"""
    operator: str
    facility: str = ""
    items: List[Contribution] = field(default_factory=list)
    variables: Optional[object] = None      # VariableLedger（可选）：本次计算用的变量快照

    # ------------------------------------------------------------------ 记录
    def add(self, c: Contribution) -> None:
        self.items.append(c)

    def add_all(self, cs) -> None:
        for c in cs or ():
            self.add(c)

    def of(self, bucket: Bucket) -> List[Contribution]:
        return [c for c in self.items if c.bucket == bucket]

    # ------------------------------------------------------------------ 合成
    def total(self, bucket: Bucket) -> Decimal:
        """按轴 F 把该桶的贡献合成一个值。

        顺序（与规则语义一一对应）：
          1. 独占短路：存在 `exclusive` 贡献 → 只取其中最大值（忽略其它一切，含基础项）。
          2. 归零：把被 `zeroes_group` 指到的 `group` 整组剔除。
          3. 跨干员取最高（`max_group`）：同组内按 owner 求和，再跨 owner 取 max。
          4. 其余按 `stacking`：
             - `SUM`：直接相加；
             - `SAME_KIND_MAX`（同种效果取最高）：同一 `group` 内
               **先按「持有者 + 技能」把该技能的各分句求和**（上游原文：
               「…额外+N 恢复效果（**叠加后的最终值**同种效果取最高）」），
               **再跨技能实例取最高**。
               例：死前必做清单 = max( 0.15 + 0.02×宿舍等级, 其它宿舍群体回复技能 )

               ⚠️ 旧实现按**单个分句**取 max，会把「基础 + 每有 N 额外」少算
               （死前必做清单 Lv5 会得到 0.15 而不是 0.25）。
        """
        items = self.of(bucket)

        exclusive = [c for c in items if c.exclusive]
        if exclusive:
            return max((c.value for c in exclusive), default=ZERO)

        zeroed = {c.zeroes_group for c in items if c.zeroes_group}
        if zeroed:
            items = [c for c in items if c.group not in zeroed]

        total = ZERO
        grouped: Dict[str, Decimal] = {}          # max_group：同 owner 小计
        per_owner: Dict[tuple, Decimal] = {}      # (max_group, owner) -> 小计
        # group -> {(owner, skill) -> 该技能各分句之和}
        same_kind: Dict[str, Dict[tuple, Decimal]] = {}

        for c in items:
            if c.max_group:
                key = (c.max_group, c.owner or c.target or "")
                per_owner[key] = per_owner.get(key, ZERO) + c.value
            elif c.stacking == Stacking.SAME_KIND_MAX:
                g = c.group or c.template or c.label
                # 「同一种效果」= 同一持有者的同一条**技能**（含它的各分句）；
                # 不同技能 / 不同人才互相竞争。故实例键要用 skill_id（去掉 #clause）。
                inst = (c.owner or c.target or "", c.skill_id.split("#", 1)[0] or c.label)
                bucket = same_kind.setdefault(g, {})
                bucket[inst] = bucket.get(inst, ZERO) + c.value
            else:
                total += c.value

        for (mg, _owner), v in per_owner.items():
            grouped[mg] = max(grouped.get(mg, ZERO), v)
        for v in grouped.values():
            total += v
        for insts in same_kind.values():
            total += max(insts.values(), default=ZERO)
        return total

    def net_rate(self) -> Decimal:
        """净速率 = clamp(消耗, 0, ∞) − 回复（与 rules 的口径一致：消耗不为负）。"""
        return max(ZERO, self.total(Bucket.CONSUME)) - self.total(Bucket.RECOVER)

    # ------------------------------------------------------------------ 展示
    def explain(self) -> str:
        """中文逐条解释：这个干员的净速率是怎么来的。"""
        lines = [f"干员 {self.operator}" + (f"（{self.facility}）" if self.facility else "")]
        for bucket in (Bucket.CONSUME, Bucket.RECOVER):
            items = self.of(bucket)
            raw = self.total(bucket)
            total = max(ZERO, raw) if bucket == Bucket.CONSUME else raw
            clamp_note = f"（原始 {raw}，消耗不为负已钳位到 0）" if total != raw else ""
            lines.append(f"{bucket.label}  合计 {total}{clamp_note}")
            hit_max = self._max_group_winners(items)
            for c in items:
                mark = ""
                note = []
                if c.group and c.stacking == Stacking.SAME_KIND_MAX:
                    inst = (c.owner or c.target or "", c.skill_id.split("#", 1)[0] or c.label)
                    best = self._same_kind_winners(items).get((c.group, inst))
                    if best is not None and c.value < best:
                        note.append("同种取最高，被更高者覆盖")
                if c.max_group and (c.max_group, c.owner) not in hit_max:
                    note.append("跨干员取最高，被更高者覆盖")
                if c.pool_share:
                    note.append(f"池分配，由 {c.pool_share} 人均分")
                if note:
                    mark = "  ← " + "；".join(note)
                detail = f"　{c.detail}" if c.detail else ""
                lines.append(f"   {c.value:>8}  {c.source()}{detail}{mark}")
        if self.variables is not None:
            lines.append(self.variables.explain())

        events = self.of(Bucket.EVENT)
        if events:
            lines.append("事件")
            for c in events:
                lines.append(f"   {'':>8}  {c.source()}{('　' + c.detail) if c.detail else ''}")
        # 净速率
        net = self.net_rate()
        if net > ZERO:
            verdict = "心情下降"
        elif net < ZERO:
            verdict = "心情上升"
        else:
            verdict = "心情不变"
        lines.append(f"净速率 = 消耗 − 回复 = {net}"
                     f"（>0 下降 / <0 上升）→ {verdict}，即每小时 {abs(net)} 点")
        return "\n".join(lines)

    @staticmethod
    def _same_kind_winners(items) -> Dict[tuple, Decimal]:
        """`SAME_KIND_MAX` 各技能实例的小计（group, (owner, skill) -> 小计）。

        用于 explain：小计低于同组最高者即"被覆盖"。
        """
        acc: Dict[tuple, Decimal] = {}
        for c in items:
            if c.stacking != Stacking.SAME_KIND_MAX or c.max_group:
                continue
            inst = (c.owner or c.target or "", c.skill_id.split("#", 1)[0] or c.label)
            key = (c.group or c.template or c.label, inst)
            acc[key] = acc.get(key, ZERO) + c.value
        return acc

    @staticmethod
    def _max_group_winners(items) -> set:
        """跨干员取最高时真正生效的 (max_group, owner) 集合。"""
        per_owner: Dict[tuple, Decimal] = {}
        for c in items:
            if c.max_group:
                key = (c.max_group, c.owner or c.target or "")
                per_owner[key] = per_owner.get(key, ZERO) + c.value
        best: Dict[str, Decimal] = {}
        for (mg, _o), v in per_owner.items():
            best[mg] = max(best.get(mg, ZERO), v)
        return {k for k, v in per_owner.items() if v >= best.get(k[0], ZERO)}

    def to_dict(self) -> dict:
        """JSON 友好结构（含逐条贡献与合计）。"""
        return {
            "operator": self.operator,
            "facility": self.facility,
            "consume": {
                "total": str(self.total(Bucket.CONSUME)),
                "items": [c.to_dict() for c in self.of(Bucket.CONSUME)],
            },
            "recover": {
                "total": str(self.total(Bucket.RECOVER)),
                "items": [c.to_dict() for c in self.of(Bucket.RECOVER)],
            },
            "events": [c.to_dict() for c in self.of(Bucket.EVENT)],
            "variables": self.variables.to_dict() if self.variables is not None else None,
            "net_rate": str(self.net_rate()),
        }


__all__ = ["Bucket", "Contribution", "MoodLedger"]
