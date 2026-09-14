"""mood_soc/models.py —— 领域数据模型（纯数据容器，无业务逻辑）。

只负责描述"干员 / 设施 / 基建布局 / 计算结果"长什么样；
具体怎么算，交给 rules / simulator / battery 等模块，保持高内聚、低耦合。

## 布局模型（P1 重构，2026-09）

旧模型假设"每种设施只有一个"（`get_facility()` 只返回第一个），无法表达
4 制造站 / 4 宿舍 / 3 发电站，也没有容量、副手、活动室的概念。
现在：

| 概念 | 承载方式 | 依据（上游 `building_data.json → rooms`） |
|---|---|---|
| 多房间同类型 | `BaseLayout.of_type()` / `count_of_type()` | `rooms[].maxCount`（制造站 5、发电站 3、宿舍 4…） |
| 容量 | `Facility.capacity`（`slots` 覆盖，否则按等级查表） | `rooms[].phases[lv].maxStationedNum` |
| 副手 | `Facility.deputies`（不占进驻位） | 技能文本「基建内（**不包含副手**及活动室使用者）」29 条 |
| 活动室 | `FacilityType.PRIVATE` | `rooms.PRIVATE`（`maxStationedNum = 0`）、`roomsWithoutRemoveStaff` |
| 「处于工作状态」 | `working_operators()`（= 进驻者） | 技能文本出现 15 次 |

**兼容性**：`get_facility()` 保留旧语义（只取第一个）但标注 deprecated；
`all_operators()` 仍只返回**进驻**干员（副手不参与心情消耗），
需要含副手时显式传 `include_deputies=True`。

数值字段统一用 decimal.Decimal，保证十进制精确、可复现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, List, Optional, Sequence, Tuple

from .config import (
    FACILITY_LABELS,
    FACILITY_MAX_COUNT,
    MOOD_MAX,
    FacilityType,
    facility_max_count,
    facility_slots,
)


def build_entry_event_config(raw) -> "EntryEventConfig":
    """把场景 JSON 里的 `entry_events` 解析成 `EntryEventConfig`（宽松解析）。

    接受的写法：
      - `{"enabled": true, "swap_with": "路人", "scope": "anywhere", "restore_back": true, "force": true}`
      - `true` / `false`（只要开关）
      - `"某人"` / `"any"`（只给交换对象，隐含开启；`any` = 自动挑最累的）
      - `None` / 缺省 → `enabled=None`（未配置：直接调 API 视为要结算）
      - `"swap_with"` 为空串 → 视为"不指定"（等同默认的「前一位进驻」）
      - `scope` 也可写成 `"anywhere": true/false`（true → `"anywhere"`）
    """
    if raw is None:
        return EntryEventConfig()
    if isinstance(raw, bool):
        return EntryEventConfig(enabled=raw)
    if isinstance(raw, str):
        return EntryEventConfig(enabled=True, swap_with=raw or None)
    if isinstance(raw, dict):
        target = raw.get("swap_with", raw.get("swapWith"))
        enabled = raw.get("enabled")
        scope = raw.get("scope")
        if scope is None and "anywhere" in raw:
            scope = "anywhere" if raw["anywhere"] else "dorm"
        scope = str(scope or "dorm").strip().lower()
        if scope in ("any", "all", "global", "任意", "全部"):
            scope = "anywhere"
        if scope not in ("dorm", "anywhere"):
            raise ValueError(f'entry_events.scope 只能是 "dorm" 或 "anywhere"，收到 {scope!r}')
        return EntryEventConfig(
            enabled=(None if enabled is None else bool(enabled)),
            swap_with=(str(target) if target else None),
            scope=scope,
            restore_back=bool(raw.get("restore_back", raw.get("restoreBack", True))),
            force=bool(raw.get("force", False)),
        )
    raise ValueError(f"entry_events 配置格式无法识别：{raw!r}")


@dataclass
class Operator:
    """一名干员及其当前状态。

    skill_ids 只存"技能 id"（字符串）而非技能对象，
    这样模型可被深拷贝、可序列化，且技能定义集中在 skills.py。

    elite / level 用于精英化判断：技能是否已解锁由 op.elite >= SkillEquip.unlock_elite
    且 op.level >= SkillEquip.unlock_level 判定；默认 elite=2（满练）保证技能全解锁。
    """
    name: str                                        # 干员名（唯一标识）
    mood: Decimal = MOOD_MAX                         # 当前心情（SOC，0~24，可含小数）
    skill_ids: List[str] = field(default_factory=list)  # 持有的心情类技能 id
    trait: Optional[str] = None                      # 旧字段：单个阵营/特性（如"岁"），仍被 _factions_of 采纳
    factions: Optional[Tuple[str, ...]] = None       # 阵营/标签覆盖；None = 用自动生成的 OPERATOR_FACTIONS
    elite: int = 2                                   # 精英化等级（0=未精英 / 1=精英一 / 2=精英二）
    level: int = 1                                   # 干员等级（用于"等级 30 解锁"等非精英门槛）


@dataclass
class Facility:
    """基建中的一个设施房间（同类型可有多个实例）。

    角色说明：
      - `operators`：**进驻**干员（占位、消耗心情、算"处于工作状态"）。
      - `deputies`：**副手**（不占进驻位；被「不包含副手」类技能排除，且不参与心情消耗）。
      - 活动室（`FacilityType.PRIVATE`）的干员属于"活动室使用者"，
        由 `BaseLayout.base_operators(include_activity_room=False)` 默认排除。
    """
    ftype: FacilityType                              # 设施类型
    level: int = 1                                   # 设施等级
    operators: List[Operator] = field(default_factory=list)  # 进驻的干员
    atmosphere: Optional[Decimal] = None             # 仅宿舍有效：实际氛围；None=按等级满氛围
    name: str = ""                                   # 实例名（如"制造站#2"），空则用类型标签
    deputies: List[Operator] = field(default_factory=list)   # 副手（不占位）
    slots: Optional[int] = None                      # 容量覆盖；None = 按 FACILITY_SLOTS_BY_LEVEL 取该等级
    enabled: bool = True                             # 是否已建成/启用（未启用不计入"每有 N 间"）

    # ------------------------------------------------------------------ 便捷属性
    @property
    def label(self) -> str:
        """设施类型的中文名。"""
        return FACILITY_LABELS.get(self.ftype, str(self.ftype))

    @property
    def display_name(self) -> str:
        """实例显示名（有自定义名用自定义名）。"""
        return self.name or self.label

    @property
    def capacity(self) -> int:
        """可进驻人数：`slots` 优先，否则按上游容量表查该等级。"""
        if self.slots is not None:
            return int(self.slots)
        return facility_slots(self.ftype, self.level)

    def is_full(self) -> bool:
        """进驻人数是否已达容量（容量 0 表示该设施不可进驻）。"""
        return len(self.operators) >= self.capacity

    def all_people(self) -> List[Operator]:
        """进驻 + 副手（全部在场干员）。"""
        return list(self.operators) + list(self.deputies)


@dataclass
class EntryEventConfig:
    """**进驻事件**（M15a 患难之交）的结算配置 —— 来自场景 JSON 的顶层 `entry_events`。

    ```json
    {
      "entry_events": {"enabled": true, "swap_with": "路人"},
      "facilities": [ ... ]
    }
    ```

    - `enabled`：这个布局**默认**要不要结算进驻事件。三态：
      `None` = 没配置（直接调用 `apply_entry_events` 时视为"要结算"）；
      `True` = 默认结算（命令行/界面不用再开开关）；`False` = 这个布局不换心情。
      调用方显式开关（CLI `--entry-events` / 界面勾选）**优先于**它。
    - `swap_with`：与**谁**互换心情。
      `None` = 默认的「前一位进驻」（`Facility.operators` 里排在触发者之前的那一位，即进驻顺序的上一人）；
      干员名 = 指定对象；`"any"`/`"任意"`/`"最累"` = **自动挑全基建心情最低的那位**。
    - `scope`：目标范围。`"dorm"`（默认）= 必须在**同一宿舍**；`"anywhere"` = **基建任意位置**
      （任何设施上的干员都能换）。
    - `restore_back`：换完心情之后要不要把"**被换满心情的那名干员换回原位**"。
      `True`（默认）= 两人各自留在自己的位置上，**只交换心情**；
      `False` = **位置也一起互换**（触发者接管对方岗位，对方进触发者的位置）。
    - `force`：到该换的时候（每班开始）触发者**不满心情**时怎么办。
      `False`（默认）= 这次不换；`True` = **等她回满心情的那一刻再换**（强制换）。
      只有带时间的排班模拟（`ui.schedule.simulate_schedule`）能"等"；一次性 API 不会等待。
    """
    enabled: Optional[bool] = None
    swap_with: Optional[str] = None
    scope: str = "dorm"
    restore_back: bool = True
    force: bool = False


@dataclass
class BaseLayout:
    """基建布局：整个基建的当前快照（即"世界状态"）。"""
    facilities: List[Facility] = field(default_factory=list)
    # 进驻事件（M15a）的默认配置；见 EntryEventConfig
    entry_events: EntryEventConfig = field(default_factory=EntryEventConfig)

    # ================================================================ 单数查询
    def get_facility(self, ftype) -> Optional[Facility]:
        """按类型取**第一个**设施。

        ⚠️ deprecated：多房间布局请用 `of_type()` / `count_of_type()`。
        仅保留给"全局唯一"设施（中枢/会客室/办公室/加工站/训练室）的旧调用点。
        """
        for f in self.facilities:
            if f.ftype == ftype:
                return f
        return None

    def control_center(self) -> Optional[Facility]:
        """返回控制中枢（可能为 None，若布局里没有）。"""
        return self.get_facility(FacilityType.CONTROL_CENTER)

    def facility_of(self, operator_name: str) -> Optional[Facility]:
        """查找某干员（含副手）所在的设施。"""
        for f in self.facilities:
            if any(o.name == operator_name for o in f.operators):
                return f
        for f in self.facilities:
            if any(o.name == operator_name for o in f.deputies):
                return f
        return None

    def get_operator(self, operator_name: str) -> Optional[Operator]:
        """按名字查找干员（含副手）。"""
        for f in self.facilities:
            for o in f.operators:
                if o.name == operator_name:
                    return o
        for f in self.facilities:
            for o in f.deputies:
                if o.name == operator_name:
                    return o
        return None

    def all_operators(self) -> List[Operator]:
        """基建内所有**进驻**干员（扁平化）。副手不参与心情消耗，见 `all_deputies()`。"""
        return [o for f in self.facilities for o in f.operators]

    def all_deputies(self) -> List[Operator]:
        """所有副手。"""
        return [o for f in self.facilities for o in f.deputies]

    # ================================================================ 复数查询
    def of_type(self, ftype) -> List[Facility]:
        """该类型的全部设施（多房间支持）。"""
        return [f for f in self.facilities if f.ftype == ftype]

    def count_of_type(self, ftype, *, only_enabled: bool = True) -> int:
        """该类型**已启用**的房间数（对应技能「每有 1 间发电站」）。"""
        return sum(1 for f in self.of_type(ftype) if f.enabled or not only_enabled)

    def count_in(self, ftypes: Iterable) -> int:
        """多个类型合计的房间数。"""
        return sum(self.count_of_type(t) for t in ftypes)

    def all_dormitories(self) -> List[Facility]:
        """全部宿舍（「所有宿舍」类技能）。"""
        return self.of_type(FacilityType.DORMITORY)

    def facilities_in(self, ftypes: Iterable) -> List[Facility]:
        """落在给定设施集合里的全部房间实例。"""
        wanted = set(ftypes)
        return [f for f in self.facilities if f.ftype in wanted]

    def operators_in(self, ftypes: Iterable, *, include_deputies: bool = False) -> List[Operator]:
        """给定设施集合里的干员（默认只算进驻者）。"""
        out: List[Operator] = []
        for f in self.facilities_in(ftypes):
            out.extend(f.operators)
            if include_deputies:
                out.extend(f.deputies)
        return out

    def working_operators(self, ftypes: Iterable) -> List[Operator]:
        """给定设施集合里**处于工作状态**的干员。

        建模口径：占进驻位且设施已启用 = 处于工作状态（副手不算）。
        上游 15 条 buff 用到「处于工作状态」，如玛恩纳「公事公办」、
        重岳「孤光共照」、维什戴尔「巴别塔之帜」。
        """
        out: List[Operator] = []
        for f in self.facilities_in(ftypes):
            if f.enabled:
                out.extend(f.operators)
        return out

    def base_operators(self, *, include_deputies: bool = False,
                       include_activity_room: bool = False) -> List[Operator]:
        """「基建内每有 1 名 XX 干员」的口径。

        上游原文：**基建内（不包含副手及活动室使用者）**（出现 29 条 buff）。
        故默认排除副手与活动室使用者。
        """
        out: List[Operator] = []
        for f in self.facilities:
            if not f.enabled:
                continue
            if f.ftype == FacilityType.PRIVATE and not include_activity_room:
                continue
            out.extend(f.operators)
            if include_deputies:
                out.extend(f.deputies)
        return out

    # ================================================================ 校验
    def validate(self) -> List[str]:
        """布局合法性自检：房间数量上限 + 进驻人数容量。

        返回问题字符串列表（**不抛异常**，因为历史场景数据可能刻意超容量）。
        上游依据：`rooms[].maxCount` 与 `rooms[].phases[lv].maxStationedNum`。
        """
        issues: List[str] = []
        for ftype in {f.ftype for f in self.facilities}:
            rooms = self.of_type(ftype)
            cap = facility_max_count(ftype)
            if cap >= 0 and len(rooms) > cap:
                issues.append(f"{FACILITY_LABELS.get(ftype, ftype)} 有 {len(rooms)} 间，"
                              f"超过上游上限 {cap} 间")
        for f in self.facilities:
            if len(f.operators) > f.capacity:
                issues.append(f"{f.display_name} 进驻 {len(f.operators)} 人，"
                              f"超过 Lv{f.level} 容量 {f.capacity} 人")
        return issues


@dataclass
class MoodResult:
    """一次测算的汇总结果（single 模式：单个目标干员）。"""
    operator_name: str          # 目标干员名
    facility_label: str         # 所在设施中文名
    initial_mood: Decimal       # 初始心情
    net_rate: Decimal           # 净消耗速率（点 / 时，>0 下降，<0 上升）
    remaining_mood: Decimal     # 目标时段结束后的心情
    sustain_hours: Decimal      # 还能维持/恢复多久（工作=到红脸、宿舍=恢复满心情；Infinity=不变）
    state: str                  # 状态描述（工作中 / 休息中 / 宿舍休息 / 红脸等）
    ledger: Optional[object] = None   # 心情流水账（MoodLedger）：逐条来源，见 ledger.explain()


@dataclass
class OperatorResult:
    """基建布局中单个干员的测算结果（base 模式中的一个条目）。"""
    name: str                   # 干员名
    facility_label: str         # 所在设施中文名
    mood: Decimal               # 心情值（0~24，当前或时段推进后）
    sustain_hours: Decimal      # 布局可维持时长（所有干员一致）；Infinity=可持续/无红脸
    mood_at_end: Optional[Decimal] = None   # 到达布局可维持时长那一刻的心情；无结束时刻（无限）为 None


@dataclass
class BaseResult:
    """整个基建布局的测算结果（base 模式）。"""
    operators: List[OperatorResult] = field(default_factory=list)
    layout_sustain_hours: Decimal = Decimal("0")   # 能维持布局（无人红脸）的最长时长；Infinity=可持续
    bottleneck: Optional[str] = None               # 最先红脸的干员名（瓶颈）；None=无瓶颈
