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
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

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
    #: **位次映射**：`{0 基位次: 干员}`（见 `_slots`）
    _slots: Dict[int, "Operator"] = field(default_factory=dict, init=False, repr=False)
    #: **手动台账**（见 `ManualLedger`）：用户手动动过的位次与人；`None` = 从没手动编辑过
    _manual: Optional["ManualLedger"] = field(default=None, init=False, repr=False)

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
        return len(self.slot_map()) >= self.capacity

    def slot_map(self) -> Dict[int, Operator]:
        """`{0 基位次: 干员}` —— **位次可留空洞**的占位视图（见 `_slots`）。

        没有显式位次（绝大多数设施、老数据）时按 `enumerate(operators)` 现算，等价于旧行为。
        ⚠️ **自愈**：`_slots` 是"位次 → 干员"的快照，若有人绕过 `set_seat` 直接改
        `operators`（历史上确实有过这种写法），它与 `operators` 会失配 —— 检测到失配就
        **退回紧凑视图**（宁可按"位次＝下标"理解，也不给出一个错位的位次）。
        """
        saved = self._slots
        if saved:
            if [id(o) for o in self.operators] == [id(saved[k]) for k in sorted(saved)]:
                return saved
            self._slots = {}                 # 被外部改过 ⇒ 位次不再可信
        return {i: op for i, op in enumerate(self.operators)}

    def slot_of(self, name: str) -> Optional[int]:
        """某干员占的**位次**；不在本设施 → `None`。"""
        for i, op in self.slot_map().items():
            if op.name == name:
                return i
        return None

    def next_open_slot(self) -> Optional[int]:
        """**第一个可入住空位**的 0 基位次；满了（或容量 0）→ `None`。

        ⚠️ 与"列表长度"不是一回事：清空某一位会**留下空洞**（位次不左移），
        所以下一个可入住位可能是中间的一格。老数据（无空洞）下结果与 `len(operators)` 相同。
        ⚠️ **手动钉住的位次不算"可入住"**（＝"这一位保持空着"，只有 API 的 `set_seat_lock`
        能把一个**空位**钉成这样；界面口径是**摆位即上锁、清空即解锁**，造不出这种状态）——
        这条判断的权威版本在 `rules._seat_verdict`；这里镜像一份，方便 `dorm_state` / 面板用。
        """
        cap = self.capacity
        if cap <= 0:
            return None
        led = read_manual(self)
        used = self.slot_map()
        for i in range(cap):
            if i not in used and not led.pins_slot(i):
                return i
        return None

    def all_people(self) -> List[Operator]:
        """进驻 + 副手（全部在场干员）。"""
        return list(self.operators) + list(self.deputies)


# ============================================================================
# ① 手动编辑逻辑的数据表达（解耦三层之一；见 rules 里的「座位裁决」）
# ============================================================================
@dataclass
class ManualLedger:
    """**手动台账** —— "人对这个设施的这些位次做过什么"。

    它是「手动编辑逻辑」唯一的持久化痕迹：**导入不打标**，只有用户在界面上
    动过某个班次某个位置（`store.session.set_slots` 等入口）才会写进来。

    ```json
    {"slots": [0, 2], "names": ["甲", "丙"]}      // 位次（0 基）与被手动放进去的人
    ```

    语义（优先级链「手动 > 自动 > 导入」的第 1 层）：

    - `slots` 里的位次 = **被手动钉住**：自动入宿既不占用它、也不换出里面的人。
      ⚠️ **2026-10 口径**：布局写入口（`set_slots` / `set_facility_slots`）是
      **"摆位即上锁、清空即解锁"** —— 把某位清空 ⇒ 该位次**退出**台账（交还自动入宿）。
      且**只锁"碰过的那一格"（累积）**：界面两条路交来 `touched`、`_write_manual` 按
      "旧 ∩ 现在还在的 ∪ 碰过的"算 ⇒ 同房间**导入进来的人不连坐**（仍可被换出）。
      "锁住一个**空位**"（＝该位次留在台账里、保持空着）现在只有 API 的 `set_seat_lock`
      做得到，那是"预留空位"的能力。
    - `names` 里的人 = **被手动放进去**：自动入宿不许把她换出（哪怕她心情很低）。
    - 解除只有显式路径：清空某位（布局写入口）或 `set_seat_lock(locked=False)` /
      `clear_manual` —— 没有隐式的"碰一下就掉"。

    ⚠️ 它**只被 ① 写、只被 ② 读**：写入点全在 `store/session.py`，读取点全在
    `rules._seat_verdict` 与自动入宿内部。自动入宿换人时**不碰**它（它就地改的是运行副本）。
    """
    slots: set = field(default_factory=set)      # 0 基位次
    names: set = field(default_factory=set)

    def is_empty(self) -> bool:
        return not self.slots and not self.names

    def pins_slot(self, index: int) -> bool:
        return int(index) in self.slots

    def pins_name(self, name: str) -> bool:
        return bool(name) and str(name) in self.names

    def to_dict(self) -> dict:
        return {"slots": sorted(int(i) for i in self.slots),
                "names": sorted(str(n) for n in self.names)}


def build_manual_ledger(raw) -> "ManualLedger":
    """解析设施上的 `manual` 键（缺省 → 空台账＝这份布局纯导入）。

    宽松写法：缺省 / `null` / `{}` → 空；`{"slots": [...], "names": [...]}`。
    """
    if not isinstance(raw, dict):
        return ManualLedger()
    slots = raw.get("slots") or ()
    names = raw.get("names") or ()
    if not isinstance(slots, (list, tuple, set)) or not isinstance(names, (list, tuple, set)):
        raise ValueError(f"manual 应当是 {{'slots': [...], 'names': [...]}}，收到 {raw!r}")
    return ManualLedger(slots={int(i) for i in slots},
                        names={str(n) for n in names if str(n)})


# ---------------------------------------------------------------- 占位原语
def facility_occupancy(raw: dict) -> tuple:
    """由一条设施描述解析**占位** → `(operators 列表, 位次映射)`。

    两种写法（`store.layout.build_base_layout` 与 `store.serialize` 用同一口径）：

    ```json
    {"operators": ["甲", "乙"]}                     // 紧凑：位次 = 下标（老文件、绝大多数设施）
    {"slots": ["甲", null, "丙", null, null]}       // 有位次空洞：空槽写 null（长度 = 容量）
    ```

    ⚠️ `slots` 优先于 `operators`（二者同时出现时以 `slots` 为准）；`slots` 里的空槽
    **不产生干员对象**，只体现在位次映射里（第 `i` 位没人 ⇒ `i` 不在映射中）。
    ⚠️ **历史写法**：`slots` 早先是"容量覆盖"（数字，v4 蓝图的 `dorm_beds` 也走它）。
    数字型 `slots` 仍然当容量读（不是占位数组）；容量覆盖的正式键是 `capacity`。
    """
    raw_slots = raw.get("slots")
    if isinstance(raw_slots, (list, tuple)):
        mapping: Dict[int, object] = {}
        for i, spec in enumerate(raw_slots):
            if spec is None or (isinstance(spec, str) and not spec.strip()):
                continue
            mapping[i] = spec
        return list(mapping.values()), mapping
    specs = list(raw.get("operators") or ())
    return specs, {i: spec for i, spec in enumerate(specs)}


def set_seat(facility: "Facility", index: int, occupant: Optional["Operator"],
             *, invalidate=None) -> None:
    """把 `facility` 的第 `index`（0 基）位设为 `occupant`（`None` = 留空）。

    **空洞的唯一写入口**：维护 `_slots` 与紧凑的 `operators` 列表两者的一致，
    并保证 `operators` 里的对象顺序与位次顺序一致（读的人不必懂 `_slots`）。
    容量越界 → 抛 `ValueError`（调用方应先钳位；静默丢人会掩盖 bug）。
    """
    if index < 0 or index >= int(facility.capacity):
        raise ValueError(f"{facility.display_name} 没有第 {index + 1} 位"
                         f"（容量 {facility.capacity}）")
    mapping = dict(facility.slot_map())
    for key in [k for k, op in mapping.items()
                if op.name == occupant.name] if occupant is not None else []:
        if key != index:
            del mapping[key]                       # 同一个人不占两个位
    if occupant is None:
        mapping.pop(index, None)
    else:
        mapping[index] = occupant
    facility._slots = mapping
    facility.operators = [mapping[k] for k in sorted(mapping)]
    if invalidate is not None:
        invalidate()


def remove_occupant(facility: "Facility", name: str, *, invalidate=None) -> bool:
    """把某人从该设施里摘掉（留空洞，**不左移**）。返回是否真的摘掉了。

    ⚠️ 它**不动手动台账**：台账里"钉住的位次"是用户意图，不会因为人被挪走而消失；
    "手动放进去的人"由调用方（`store/session.py` 的编辑入口）负责同步。
    """
    hit = False
    for key, op in list(facility.slot_map().items()):
        if op.name == name:
            set_seat(facility, key, None)
            hit = True
    if hit and invalidate is not None:
        invalidate()
    return hit


# ---------------------------------------------------------------- 台账原语
def read_manual(facility: "Facility") -> ManualLedger:
    """该设施的**手动台账**（没有就现建一个空的；不写回）。"""
    ledger = getattr(facility, "_manual", None)
    return ledger if ledger is not None else ManualLedger()


def mark_manual(facility: "Facility", index: int, name: str = "") -> ManualLedger:
    """**① 手动编辑逻辑的唯一写入口**：记下"第 `index` 位被手动写成 `name`（可空）"。

    规则（Q2/Q12/Q13 的裁决）：
      · 位次一定进 `slots`（这一位从此由人负责，自动入宿不占不换）；
      · 写了名字 ⇒ 该名字进 `names`，同时把这一位**原来那个名字**移出 `names`；
      · 名字为空（手动清空）⇒ 只留位次标记，原来那个名字移出 `names`（她不再是"手动放的"）。
    """
    facility._slots = dict(facility.slot_map())
    ledger = read_manual(facility)
    facility._manual = ledger
    ledger.slots.add(int(index))
    old = facility._slots.get(int(index))
    if old is not None:
        ledger.names.discard(old.name)
    if name:
        ledger.names.add(str(name))
    return ledger


def clear_manual(facility: "Facility", index: Optional[int] = None,
                 name: str = "") -> None:
    """撤掉手动标记（`index=None` = 清空该设施整份台账）。

    用于"整份替换该班次布局 / 房间等级变小"这类动作（Q37：越界位次连同标记一起丢掉）。
    """
    ledger = read_manual(facility)
    facility._manual = ledger
    if index is None:
        ledger.slots.clear()
        ledger.names.clear()
        return
    ledger.slots.discard(int(index))
    if name:
        ledger.names.discard(str(name))


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

    - `enabled`：这一位参不参与（`False` = 永远不动她，与黑名单同效、但只对这一刻）。
    - `cycle` / `shift`：**1 基**的周期序号 / 班次序号（`None` = 不限）。

    ⚠️ **2026-10 三层解耦后只剩"参不参与"**：曾经的 `swap_with`（手动点名）/ `dorm` / `slot`
    （手动指定位置）**已作废** —— "手动入宿"归**手动编辑逻辑**：在**看板 / 「干员与心情」**里
    把干员放进某个宿舍位次，写的是**那一班的布局 + 手动台账**（`models.ManualLedger`，
    自动入宿从此不占那些位次、不换那些人）。旧写法（JSON / API 里的 `target`、`dorm`、
    `slot`、`swap_with`）**读得进来但被忽略**，只回一条 note，不再有第二种手动入口。
    """

    name: str = ""
    enabled: bool = True
    cycle: Optional[int] = None
    shift: Optional[int] = None

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
                       "protected_slots": 5,
                       "blacklist": ["干员甲"],
                       "per_operator": [{"name": "虎狼丸", "swap_with": "甲"},
                                        {"name": "跃跃", "enabled": false}]},
      "facilities": [ ... ]
    }
    ```

    它是一类**班次开始时的布局事件**（与 `entry_events` 同层，不改"每小时速率"公式）：
    把"这一班完全没有出现在任何设施里、心情还没满"的干员安排进宿舍 ——
    竖向正序填空床；全满后取**心情最低**的候选，换出"锁定区外、心情 ≥ 她、心情最大"的住户。

    口径（三层解耦：**手动编辑 > 自动入宿 > 导入布局**，见 `rules.apply_idle_to_dorm`）：

    - `enabled`：**默认 `True`＝默认就结算**（用户口径："闲置入宿默认是开启的"）。
      没写这个键 ⇒ 开；**显式写 `false` ⇒ 关**（显式配置永远优先）。
      调用方显式开关（CLI / 界面勾选 / `apply_idle_to_dorm(enabled=...)`）同样优先于它。
    - `protected_slots`：**锁定位置数**（文档 §5），默认 `5`。按**竖向正序**（位次优先、
      宿舍序号其次）锁前 N 个位置：自动入宿不换锁定区里的人，锁定区的**空位**照样能入住。
      运行时钳位到 `[0, 当前可用宿舍的总位置数]`（超了按总数生效、不报错）。
    - `blacklist`：**黑名单**（文档 §6）：永远不能**通过闲置入宿进宿舍**的干员 ——
      不进初始队列、不出现手动设置里；**但**排班自带的她照旧在宿舍里、照旧能被换出。
    - `per_operator`：逐个干员的**"参不参与"**（见 `IdleToDormEntry`）。
      ⚠️ **手动入宿不在这里**：要在某个时刻把某人放进某个位次，用**看板 / 「干员与心情」**
      （写布局快照 + 手动台账）。旧写法 `swap_with` / `dorm` / `slot` / `target` 会被忽略。
    - **不设班次数量门槛**（文档 §2 第二版）：1 个、2 个以及 3 个以上班次都执行闲置入宿。
      每个真实班次的班初都会执行；班次时长超过 `12h` 时，班内每个**严格位于班末之前**的
      `12h` 整数倍（`store.schedule.execution_offsets`）也会执行一次内部换班。
      ⚠️ 第一版那条"不同班次数量 < 3 时保留但不生效"的门槛已作废，引擎签名里**没有**
      班次数参数（别再引入 `shift_count` / `MIN_SHIFTS_FOR_IDLE`）。
    """

    enabled: bool = True
    protected_slots: int = 5
    blacklist: List[str] = field(default_factory=list)
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
    "idle_to_dorm": {"enabled": true, "protected_slots": 5, "blacklist": ["某人"],
                     "per_operator": {"跃跃": false}}
    "idle_to_dorm": {"per_operator": [{"name": "跃跃", "enabled": false}]}
    ```

    `per_operator` 只表达**"参不参与"**：`{"名字": false}`、数组
    `[{"name": ..., "enabled": ..., "cycle": 2, "shift": 3}]`（`cycle`/`shift` 是 1 基序号，
    限定"只在哪几次生效"；不写 = 不限）。

    ⚠️ **旧写法会被忽略**（读得进来、不报错、也不再生效）：`{"名字": "某人"}`（手动点名）、
    `swap_with` / `dorm` / `slot` / `target`（手动指定位置/点名）—— 那三件事现在归
    **手动编辑逻辑**：在**看板 / 「干员与心情」**里把人放进某个位次（写布局快照 + 手动台账）。

    顶层还有两个全局字段（文档 §5/§6，都可省）：
    `protected_slots`（锁定位置数，默认 `5`）、`blacklist`（黑名单，默认空）。
    字段名同时接受下划线与小驼峰（`protectedSlots` / `black_list` / `blackList`）。
    """
    if raw is None:
        return IdleToDormConfig()
    if isinstance(raw, bool):
        return IdleToDormConfig(enabled=raw)
    if not isinstance(raw, dict):
        raise ValueError(f"idle_to_dorm 应当是 true/false 或对象，收到 {raw!r}")

    protected = raw.get("protected_slots", raw.get("protectedSlots"))
    if protected is not None:
        protected = int(protected)
        if protected < 0:
            raise ValueError(f"idle_to_dorm.protected_slots 不能为负，收到 {protected!r}")
    black_raw = raw.get("blacklist", raw.get("blackList", raw.get("black_list")))
    if black_raw is None:
        blacklist: List[str] = []
    elif isinstance(black_raw, (list, tuple, set)):
        blacklist = [str(n).strip() for n in black_raw if str(n).strip()]
    elif isinstance(black_raw, str):
        blacklist = [n.strip() for n in black_raw.replace("，", ",").split(",") if n.strip()]
    else:
        raise ValueError(f"idle_to_dorm.blacklist 应当是数组，收到 {black_raw!r}")

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
        if isinstance(value, str) or value is None:
            # 旧写法：`{"名字": "交换对象"}` = 手动点名 —— 已作废，按"参与、全自动"收下
            entries.append(IdleToDormEntry(name=str(name)))
            continue
        if not isinstance(value, dict):
            raise ValueError(f"per_operator[{name!r}] 格式无法识别：{value!r}")
        cyc = value.get("cycle", value.get("cycleIndex"))
        shf = value.get("shift", value.get("shiftIndex"))
        entries.append(IdleToDormEntry(
            name=str(value.get("name", name)),
            enabled=bool(value.get("enabled", True)),
            cycle=(int(cyc) if cyc is not None else None),
            shift=(int(shf) if shf is not None else None)))
    return IdleToDormConfig(
        enabled=(None if raw.get("enabled") is None else bool(raw["enabled"])),
        protected_slots=(5 if protected is None else protected),
        blacklist=blacklist,
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
    # **变量初始值**：场景/导入文件给定的"基建级中间货币"起始量（如 木天蓼=5）。
    # 本项目的变量默认是"从布局里的产出者推导"（`variables.collect_variables`），
    # 这里允许额外给一份**初始值**——上游求解器的 `initial_global` 就是这种输入。
    initial_variables: Dict[str, Decimal] = field(default_factory=dict)
    # **「不在基建」名单**：既不在工作设施、也不在宿舍的干员（场景 JSON 顶层 `detached`）。
    # ⚠️ 他们**不在 `facilities` 里**，所以：不占位、不消耗、不回复、**不参与任何技能计数**
    # （「基建内每有 1 名 XX 干员」数的是进驻者）。唯一的作用是"有个心情值、能被查到/画出来"
    # —— 多班排班里由 `store.schedule.Schedule.detached` 承载，见那里的说明。
    detached: List[str] = field(default_factory=list)

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
        """查找某干员（含副手）所在的设施。

        ⚠️ 走 `_name_index()`（懒建的名字索引）—— 原先是"逐间房扫 `operators` + genexpr"，
        实测一次重算里被调 13 万次、4.66M 次生成器迭代（占 profile 13%）。
        **改了设施成员就一定要 `invalidate_index()`**（`rules` 里换人/摘人/位置对调那几处
        已经调了）：索引是**快照**，不失效就会给出"她还在原来那间"的错答案。
        """
        entry = self._name_index().get(operator_name)
        return None if entry is None else entry[0]

    def _name_index(self) -> Dict[str, tuple]:
        """懒建的**名字索引**：`{名字: (设施, 干员)}`（只在 `facility_of` / `get_operator` 里用）。

        语义与原来的两趟线性扫描**逐字对齐**：先扫所有房间的 `operators`、再扫所有房间的
        `deputies`，同名取**先出现**的那个（`setdefault`）。
        ⚠️ 它是**快照**：设施成员一变就必须 `invalidate_index()`。
        ⚠️ 存在实例上（不是类/全局）：`deepcopy` 会连它一起复制 —— 复制时刻它是自洽的，
        之后对副本的修改由副本自己的 `invalidate_index()` 管。
        """
        idx = self.__dict__.get("_name_index_cache")
        if idx is None:
            idx = {}
            for f in self.facilities:
                for o in f.operators:
                    idx.setdefault(o.name, (f, o))
            for f in self.facilities:                       # 副手：只在 operators 里没有她时才算
                for o in f.deputies:
                    idx.setdefault(o.name, (f, o))
            self.__dict__["_name_index_cache"] = idx
        return idx

    def invalidate_index(self) -> None:
        """**设施成员变了**之后必须调用：丢掉名字索引（下次查询重建）。

        调用点集中在 `mood_soc/rules.py`（闲置入宿换人 / 摘人、进驻事件的位置对调）。
        忘了调 = 查询给出"她还在原来那间"的陈旧答案。
        """
        self.__dict__.pop("_name_index_cache", None)

    def get_operator(self, operator_name: str) -> Optional[Operator]:
        """按名字查找干员（含副手）。⚠️ 同样走名字索引，改成员后要 `invalidate_index()`。"""
        entry = self._name_index().get(operator_name)
        return None if entry is None else entry[1]

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
            occupied = len(f.slot_map())
            if occupied > cap:
                issues.append(f"{f.display_name} 进驻 {occupied} 人，"
                              f"超过 Lv{f.level} 容量 {cap} 人")
            over = [i for i in f.slot_map() if i >= int(cap)]
            if over:
                issues.append(f"{f.display_name} 有第 {max(over) + 1} 位的干员，"
                              f"超过 Lv{f.level} 容量 {cap} 位")
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
