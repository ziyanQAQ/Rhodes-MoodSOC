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


ENTRY_WHEN_MODES = ("immediate", "wait", "full")
# 「什么时候换」的中文说明（界面与文档共用一套说法）
ENTRY_WHEN_LABELS = {
    "immediate": "强制立刻换（不管双方心情）",
    "wait": "到点没满就等她回满再换",
    "full": "只在她满心情时换（游戏原口径）",
}


def normalize_entry_when(value) -> Optional[str]:
    """把 `when` 的各种写法归一成 `"immediate"`/`"wait"`/`"full"`；认不出返回 None。"""
    if value is None:
        return None
    text = str(value).strip().lower()
    aliases = {
        "immediate": "immediate", "now": "immediate", "force": "immediate",
        "立即": "immediate", "立刻": "immediate", "强制": "immediate",
        "wait": "wait", "wait_full": "wait", "waitfull": "wait", "等待": "wait",
        "full": "full", "when_full": "full", "whenfull": "full", "满心情": "full",
    }
    return aliases.get(text, text if text in ENTRY_WHEN_MODES else None)


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
        when = normalize_entry_when(raw.get("when", raw.get("换")))
        if when is None:
            if "when" in raw and raw["when"] is not None:
                raise ValueError(f'entry_events.when 只能是 {ENTRY_WHEN_MODES} 之一，'
                                 f"收到 {raw['when']!r}")
            # 兼容旧字段：写了 force 就按旧语义（true→等她满 / false→只在她满时换）
            when = ("wait" if raw["force"] else "full") if "force" in raw else "immediate"
        return EntryEventConfig(
            enabled=(None if enabled is None else bool(enabled)),
            swap_with=(str(target) if target else None),
            scope=scope,
            restore_back=bool(raw.get("restore_back", raw.get("restoreBack", True))),
            force=bool(raw.get("force", False)),
            when=when,
            per_shift=build_entry_shift_overrides(raw.get("per_shift", raw.get("perShift"))),
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
    entry_swapped: bool = False                      # 本次布局快照里是否已结算过进驻事件（防重复结算）


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
class EntryShiftOverride:
    """**某个班次**的进驻事件覆盖（字段为 `None` = 沿用全局 `EntryEventConfig`）。

    排班是"多班轮换"（如 12h + 6h + 6h），而"这个班要不要换心情、换给谁、什么时候换"
    经常每班不同（MAA 的排班文件里也是每个 plan 各自带一份 `Fiammetta` 设置）。
    所以支持按班次覆盖：

    ```json
    "entry_events": {
      "enabled": true, "scope": "anywhere",
      "per_shift": [
        {"enabled": true,  "swap_with": "巫恋", "when": "immediate"},
        {"enabled": true,  "swap_with": "any"},
        {"enabled": false}
      ]
    }
    ```

    - `key`：定位班次 —— 1 基序号（`1`/`"1"`，对应第 1/2/3 班）或**班次名**（如 `"Shift 2 · 6h"`）。
      用列表写法时按位置自动编号。
    - `swap_with`：`None` = 继承全局；`""`（JSON 里的 `null`/空串）= **明确"不指定"**
      （回到默认口径：同宿舍「前一位进驻」）。
    - `when`：`"immediate"`（强制立刻换）/ `"wait"`（等她回满再换）/ `"full"`（只在她满心情时换）；
      `None` = 继承全局。
    """
    key: object = 0                      # int（1 基序号）或 str（班次名 / 数字串）
    enabled: Optional[bool] = None
    swap_with: Optional[str] = None
    scope: Optional[str] = None
    restore_back: Optional[bool] = None
    force: Optional[bool] = None
    when: Optional[str] = None

    def matches(self, index: int, label: str = "") -> bool:
        """是否命中第 `index`（0 基）个班次。"""
        key = self.key
        if isinstance(key, bool):
            return False
        if isinstance(key, int):
            return key == index + 1
        text = str(key).strip()
        if text.isdigit():
            return int(text) == index + 1
        return bool(label) and text == label


@dataclass
class IdleToDormEntry:
    """**某一次进驻**（周期 × 班次 × 干员）在「闲置入宿」里的设置。

    默认（不写 `cycle`/`shift`）= 对该干员的**所有**班次/周期生效；
    写了就只在对应的那几次生效（更具体的优先，见 `IdleToDormConfig.entry_for`）。

    - `enabled`：这一位参不参与（`False` = 永远不动他）。
    - `swap_with`：宿舍满了时**与谁互换**（必须是那一刻宿舍里心情满的那位）。
      `None`/`""` = **自动**（挑一个满心情的宿舍干员）。
    - `dorm` / `slot`：**指定放进哪一间宿舍的空位**（与 `swap_with` 互斥，写了 `dorm` 就按它）。
      `dorm` 是 1 基的**宿舍序号**（第 1 间 = `1`，界面写作「宿舍01」）；
      `slot` 是 1 基位次（`None` = 那间最靠前的空位）。
      ⚠️ 宿舍位次在模型里**没有机制差异**（回复只看宿舍等级/氛围/人数），而且 `operators` 是紧凑列表、
      不表示"洞"，所以引擎总是放进**最靠前的空位**；`slot` 与它不一致时会在说明里注明。
      指定的那间**没有空位**了 → **跳过这一位**（严格按指定，与"指定的人不在宿舍"同一套规矩）。
    - `cycle` / `shift`：**1 基**的周期序号 / 班次序号（`None` = 不限）。
      为什么要有这两维：心情跨班跨周期连续，所以"这一刻谁没满、谁在宿舍且满了"**每次都不同**，
      候选与可交换对象都不一样（实测示例排班 3 个周期的闲置入宿事件分别落在 12/18h、24/42h、66h）。
    """

    name: str = ""
    enabled: bool = True
    swap_with: Optional[str] = None
    cycle: Optional[int] = None
    shift: Optional[int] = None
    dorm: Optional[int] = None      # 1 基宿舍序号（指定"放进哪一间宿舍的空位"）
    slot: Optional[int] = None      # 1 基位次；None = 那间最靠前的空位

    def matches(self, name: str, cycle: Optional[int] = None,
                shift: Optional[int] = None) -> bool:
        """这一条设置对"第 `cycle` 周期的第 `shift` 班的 `name`"是否适用。

        带作用域的设置**只在调用方给出了对应序号时**才匹配（所以不带作用域的旧调用不会误命中）。
        """
        if not self.name or self.name != name:
            return False
        if self.cycle is not None and self.cycle != cycle:
            return False
        if self.shift is not None and self.shift != shift:
            return False
        return True

    def specificity(self) -> int:
        """具体程度：周期+班次都写 = 2，只写一个 = 1，都不写 = 0（越大越优先）。"""
        return (1 if self.cycle is not None else 0) + (1 if self.shift is not None else 0)


@dataclass
class IdleToDormConfig:
    """**闲置入宿**配置 —— 来自场景 JSON 的顶层 `idle_to_dorm`。

    ```json
    {
      "idle_to_dorm": {"enabled": true,
                       "per_operator": [{"name": "虎狼丸", "swap_with": "甲"},
                                        {"name": "跃跃", "enabled": false}]},
      "facilities": [ ... ]
    }
    ```

    它是一类**班次开始时的布局事件**（与 `entry_events` 同层，不改"每小时速率"公式）：
    把"不在工作、也不在宿舍、心情还没满"的干员安排进宿舍恢复心情——
    先看宿舍有没有**空位**，没有空位才**与宿舍里心情已满的那位互换**。

    - `enabled`：三态。`None` = 没配置（`apply_idle_to_dorm` 直接调用时**默认不结算**，
      因为它会动布局）；`True` = 默认结算；`False` = 这个布局不做这件事。
      调用方显式开关（CLI `--idle-to-dorm` / 界面勾选）优先于它。
    - `per_operator`：逐个干员的参与与交换对象（见 `IdleToDormEntry`）。
    """

    enabled: Optional[bool] = None
    per_operator: List["IdleToDormEntry"] = field(default_factory=list)

    def entry_for(self, name: str, cycle: Optional[int] = None,
                  shift: Optional[int] = None) -> Optional["IdleToDormEntry"]:
        """这一刻（`cycle` 周期的 `shift` 班）该干员的有效设置 → **最具体的那一条**。

        优先级：周期+班次都写 > 只写一个 > 都不写（全局）；同分时**后写的赢**。
        没写过 = 参与、自动挑目标（返回 `None`）。
        """
        best: Optional["IdleToDormEntry"] = None
        best_score = -1
        for e in self.per_operator:
            if not e.matches(name, cycle, shift):
                continue
            if e.specificity() >= best_score:
                best, best_score = e, e.specificity()
        return best


def build_idle_to_dorm_config(raw) -> IdleToDormConfig:
    """解析 JSON 顶层的 `idle_to_dorm`（宽松写法：`true`/`false`/对象）。

    ```json
    "idle_to_dorm": true
    "idle_to_dorm": {"enabled": true, "per_operator": {"虎狼丸": "甲", "跃跃": false}}
    "idle_to_dorm": {"per_operator": [{"name": "虎狼丸", "swap_with": "甲"}]}
    ```

    `per_operator` 支持三种写法：`{"名字": "交换对象"}`、`{"名字": false}`（不参与）、
    或数组 `[{"name": ..., "enabled": ..., "swap_with": ..., "cycle": 2, "shift": 3}]`。
    数组写法里可以带 `cycle` / `shift`**限定只在哪几次生效**（1 基序号；不写 = 不限）。
    """
    if raw is None:
        return IdleToDormConfig()
    if isinstance(raw, bool):
        return IdleToDormConfig(enabled=raw)
    if not isinstance(raw, dict):
        raise ValueError(f"idle_to_dorm 应当是 true/false 或对象，收到 {raw!r}")

    per_raw = raw.get("per_operator", raw.get("perOperator"))
    items: List[tuple] = []
    if isinstance(per_raw, dict):
        items = list(per_raw.items())
    elif isinstance(per_raw, list):
        for item in per_raw:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError(f"per_operator 数组里的每一项都要有 name：{item!r}")
            items.append((item["name"], item))
    elif per_raw is not None:
        raise ValueError(f"idle_to_dorm.per_operator 应当是数组或对象，收到 {per_raw!r}")

    entries: List[IdleToDormEntry] = []
    for name, value in items:
        if isinstance(value, bool):
            entries.append(IdleToDormEntry(name=str(name), enabled=value))
            continue
        if isinstance(value, str):
            entries.append(IdleToDormEntry(name=str(name), enabled=True,
                                           swap_with=value.strip() or None))
            continue
        if value is None:
            entries.append(IdleToDormEntry(name=str(name)))
            continue
        if not isinstance(value, dict):
            raise ValueError(f"per_operator[{name!r}] 格式无法识别：{value!r}")
        target = value.get("swap_with", value.get("swapWith"))
        cyc = value.get("cycle", value.get("cycleIndex"))
        shf = value.get("shift", value.get("shiftIndex"))
        dorm = value.get("dorm", value.get("dormIndex"))
        slot = value.get("slot", value.get("slotIndex"))
        entries.append(IdleToDormEntry(
            name=str(value.get("name", name)),
            enabled=bool(value.get("enabled", True)),
            swap_with=(str(target).strip() or None) if target is not None else None,
            cycle=(int(cyc) if cyc is not None else None),
            shift=(int(shf) if shf is not None else None),
            dorm=(int(dorm) if dorm is not None else None),
            slot=(int(slot) if slot is not None else None)))
    return IdleToDormConfig(
        enabled=(None if raw.get("enabled") is None else bool(raw["enabled"])),
        per_operator=entries)


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
    - `force`：**旧字段**（`True` = 等她回满再换；`False` = 只在她满心情时换）。保留兼容，
      新写法请用 `when`。
    - `when`：**什么时候换**（三选一，默认 `"immediate"`）：
      - `"immediate"`（**强制立刻换**，默认）——只要开了并设了对象，到点就换，
        **不管她满不满、也不管对方心情是多少**（哪怕两边都是 24，也照做，位置该换也换）；
      - `"wait"` —— 到点若她不满心情，就**等她回满的那一刻再换**；
      - `"full"` —— **只在她满心情时换**（游戏原文口径），不满就这一次不换。
      ⚠️ 兼容规则：没写 `when` 时，若写了 `force` 则按旧语义（`true`→`wait`、`false`→`full`），
      两者都没写才是新默认 `immediate`。
    - `per_shift`：**按班次覆盖**上列各项（见 `EntryShiftOverride`）——
      3 班排班就可以写"第 1 班换给巫恋、第 2 班自动挑最累的、第 3 班不用"。
    """
    enabled: Optional[bool] = None
    swap_with: Optional[str] = None
    scope: str = "dorm"
    restore_back: bool = True
    force: bool = False
    when: str = "immediate"
    per_shift: List["EntryShiftOverride"] = field(default_factory=list)


def build_entry_shift_overrides(raw) -> List["EntryShiftOverride"]:
    """解析 `entry_events.per_shift` → `[EntryShiftOverride, ...]`。

    两种写法：

    ```json
    "per_shift": [ {"enabled": true, "swap_with": "巫恋"}, {"enabled": false} ]   // 列表＝按班次位置
    "per_shift": { "1": {...}, "Shift 2 · 6h": {...} }                            // 字典＝按序号或班次名
    ```

    `swap_with` 的三种写法：**不写** = 继承全局；写 `null`/`""` = 明确"不指定"（回默认口径）；
    写人名/`"any"` = 就按它。
    """
    if raw is None:
        return []
    items: List[tuple] = []
    if isinstance(raw, list):
        items = [(i + 1, v) for i, v in enumerate(raw)]
    elif isinstance(raw, dict):
        items = list(raw.items())
    else:
        raise ValueError(f"entry_events.per_shift 应当是数组或对象，收到 {raw!r}")

    out: List[EntryShiftOverride] = []
    for key, value in items:
        if value is None:
            out.append(EntryShiftOverride(key=key))
            continue
        if isinstance(value, bool):
            out.append(EntryShiftOverride(key=key, enabled=value))
            continue
        if isinstance(value, str):
            out.append(EntryShiftOverride(key=key, enabled=True, swap_with=value))
            continue
        if not isinstance(value, dict):
            raise ValueError(f"entry_events.per_shift[{key!r}] 格式无法识别：{value!r}")
        scope = value.get("scope")
        if scope is None and "anywhere" in value:
            scope = "anywhere" if value["anywhere"] else "dorm"
        if scope is not None:
            scope = str(scope).strip().lower()
            if scope in ("any", "all", "global", "任意", "全部"):
                scope = "anywhere"
            if scope not in ("dorm", "anywhere"):
                raise ValueError(f'per_shift[{key!r}].scope 只能是 "dorm" 或 "anywhere"，'
                                 f"收到 {scope!r}")
        swap_with = None
        if "swap_with" in value or "swapWith" in value:
            target = value.get("swap_with", value.get("swapWith"))
            swap_with = str(target).strip() if target else ""      # "" = 明确不指定
        when = normalize_entry_when(value.get("when"))
        if when is None and "when" in value and value["when"] is not None:
            raise ValueError(f"per_shift[{key!r}].when 只能是 {ENTRY_WHEN_MODES} 之一，"
                             f"收到 {value['when']!r}")
        if when is None and "force" in value and value["force"] is not None:
            when = "wait" if value["force"] else "full"            # 兼容旧字段
        out.append(EntryShiftOverride(
            key=key,
            enabled=(None if value.get("enabled") is None else bool(value["enabled"])),
            swap_with=swap_with,
            scope=scope,
            restore_back=(None if value.get("restore_back", value.get("restoreBack")) is None
                          else bool(value.get("restore_back", value.get("restoreBack")))),
            force=(None if value.get("force") is None else bool(value["force"])),
            when=when,
        ))
    return out


def resolve_entry_config(cfg: "EntryEventConfig", index: int, label: str = "",
                         overrides: Optional[List["EntryShiftOverride"]] = None
                         ) -> "EntryEventConfig":
    """把**全局配置**与**该班次的覆盖**合并成"这一班的有效配置"。

    `overrides=None` 时用 `cfg.per_shift`；命中多个则按顺序依次覆盖（后写的赢）。
    返回的配置**不含** `per_shift`（已经解析完了）。
    """
    out = EntryEventConfig(enabled=cfg.enabled, swap_with=cfg.swap_with, scope=cfg.scope,
                           restore_back=cfg.restore_back, force=cfg.force, when=cfg.when)
    for ov in (cfg.per_shift if overrides is None else overrides):
        if not ov.matches(index, label):
            continue
        if ov.enabled is not None:
            out.enabled = ov.enabled
        if ov.swap_with is not None:
            out.swap_with = ov.swap_with or None      # "" → 回默认口径
        if ov.scope is not None:
            out.scope = ov.scope
        if ov.restore_back is not None:
            out.restore_back = ov.restore_back
        if ov.when is not None:
            out.when = ov.when
        if ov.force is not None:                      # 旧字段兜底
            out.force = ov.force
            if ov.when is None:
                out.when = "wait" if ov.force else "full"
    return out


@dataclass
class BaseLayout:
    """基建布局：整个基建的当前快照（即"世界状态"）。"""
    facilities: List[Facility] = field(default_factory=list)
    # 进驻事件（M15a）的默认配置；见 EntryEventConfig
    entry_events: EntryEventConfig = field(default_factory=EntryEventConfig)
    # 闲置入宿（把未满的闲置干员安排进宿舍）的配置；见 IdleToDormConfig
    idle_to_dorm: IdleToDormConfig = field(default_factory=IdleToDormConfig)

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
        """布局合法性自检：房间数量上限 + **建造位总量** + 进驻人数容量。

        返回问题字符串列表（**不抛异常**，因为历史场景数据可能刻意超容量）。
        上游依据：`rooms[].maxCount`（单类型上限）、`layouts.v0.slots` 的 `category=OUTPUT`
        槽位数 = 9（制造站/贸易站/发电站共用）、`rooms[].phases[lv].maxStationedNum`（容量）。
        """
        from .config import OUTPUT_ROOM_TYPES, OUTPUT_SLOT_TOTAL, facility_max_count, \
            facility_max_level, facility_slots

        issues: List[str] = []
        for ftype in {f.ftype for f in self.facilities}:
            rooms = self.of_type(ftype)
            cap = facility_max_count(ftype)
            if cap >= 0 and len(rooms) > cap:
                issues.append(f"{FACILITY_LABELS.get(ftype, ftype)} 有 {len(rooms)} 间，"
                              f"超过上游上限 {cap} 间")

        # 制造站 / 贸易站 / 发电站共用 9 个建造位（上游 layouts.v0.slots → category=OUTPUT）
        output_rooms = [f for f in self.facilities if f.ftype in OUTPUT_ROOM_TYPES]
        if len(output_rooms) > OUTPUT_SLOT_TOTAL:
            detail = "、".join(f"{f.display_name}"
                               for f in output_rooms)
            issues.append(f"制造站/贸易站/发电站共 {len(output_rooms)} 间，超过建造位总量 "
                          f"{OUTPUT_SLOT_TOTAL}（上游 layouts.v0.slots 的 OUTPUT 槽位）：{detail}")

        for f in self.facilities:
            max_lv = facility_max_level(f.ftype)
            if f.level > max_lv:
                issues.append(f"{f.display_name} 等级 Lv{f.level} 超过上游最高等级 Lv{max_lv}")
            # 容量按**当前等级**算（等级可改；上游 phases[lv].maxStationedNum）
            cap = facility_slots(f.ftype, f.level) if f.slots is None else f.slots
            if len(f.operators) > cap:
                issues.append(f"{f.display_name} 进驻 {len(f.operators)} 人，"
                              f"超过 Lv{f.level} 容量 {cap} 人")
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
