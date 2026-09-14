# 心情引擎架构诊断与重构设计

> 文档目的：回答「当前心情机制**是否具备可拓展性**」，并给出重构设计。
> 结论先行：**数据层已经可拓展（六轴模板就绪），运行时不可拓展**——所以重构范围是**运行时**
> （布局模型 / 效果派发 / 记录层），**不是**重写数学层（`battery.py`）与数据管道。

---

## 一、诊断结论

| 维度 | 状态 | 证据 |
|---|---|---|
| 数学层（安时积分） | ✅ 可拓展 | `battery.py` 与游戏规则无关，纯钳位积分 |
| 数据层（技能库） | ✅ 可拓展 | 六轴模板已就位：242 clause 全挂 `template_id`，755 buff 全登记 |
| **运行时派发** | ❌ **不可拓展** | `template_id` 在数据里，**`rules.py` 从不读它**；派发靠 `SkillKind` 的 if/else |
| **布局模型** | ❌ **不可拓展** | `get_facility()` 只返回第一个同类型设施；无容量 / 副手 / 工作状态 / 活动室 |
| **记录系统** | ❌ **不存在** | 只有一个 `net_rate` 数字 + `note` 原文；无法回答"这个速率由谁贡献、按什么规则合成" |
| 特殊机制 | ⚠️ 半成品 | 阵营表曾手工且**错误**（已修）；26 种变量（热情值等）无载体 |

### 1.1 运行时派发：模板与代码双轨

- 数据侧：`Skill.template_id ∈ {M01…M17, X01…X11}`（六轴分类，已完整）。
- 代码侧：`SkillKind ∈ {self_consume, facility_consume, …, eliminate_self}`（8 种在用）。
- **两者互不知情**：`rules.py` 里 `SkillKind.` 出现 15 次，`template_id` 出现 **0 次**。
- 后果：新增一个机制 = 改 `SkillKind` 枚举 + 在 `compute_consumption`/`_work_recovery`/`_dorm_recovery`
  **三处**加分支 + 在生成器 `FAMILY_KIND` 加映射 + 同步两份文档。这就是"不可拓展"的定义。

### 1.2 聚合规则散落成硬编码

| 规则（轴 F） | 当前实现位置 | 问题 |
|---|---|---|
| F1 求和 | 各函数内 `total +=` | 与环境耦合 |
| F2 同种取最高 | `_dorm_recovery` 内 `max(...)` ×3 | 写死；无法表达"哪几种算同种" |
| F3 跨干员取最高 | `_work_recovery` 内 `grouped[group] = max(...)` | 后加的特例 |
| F6 池分配 | `_icebrew_recovery` 独立函数 | 冰酿专属，不通用 |
| F7 归零优先 | `_dorm_recovery` 顶部 `exclusive` 短路 | 菲亚梅塔专属，不通用 |

→ 没有统一的 `Accumulator`，每加一条叠加规则就多一处 `max/min/短路`（`rules.py` 里 `max(` 9 次、`min(` 4 次）。

### 1.3 布局模型：只支持"每种设施一个"

```python
def get_facility(self, ftype):   # 只返回第一个
```

实测：布局放 2 个制造站 + 2 个宿舍时，`facility_of()`（遍历全部）**能**逐干员算对，
但任何"按设施类型聚合"的技能无处落地。上游需求规模（755 buff 实测）：

| 需求 | 上游条数 | 现状 |
|---|---|---|
| 「处于工作状态」 | 15 | 无概念：默认所有进驻者都在工作 |
| 「不包含副手」 | 29 | 无概念 |
| 「活动室使用者」 | 23 | `FacilityType` 里**没有 PRIVATE**（上游 `rooms` 有） |
| 「每有 N 间发电站」 | 2 clause | 无"按类型计数"能力 |
| 「所有宿舍 / 所有制造站」 | 5 / 7 | 靠逐设施迭代侥幸可用，非通用能力 |
| 设施等级折算（宿舍每级、办公室等级） | 4 clause | `Facility.level` 有，但技能读不到 |

### 1.4 一个已确认的错误：加工站 / 训练室被当成普通工作设施

```
加工站 路人X  消耗=0.75   训练室 路人X  消耗=0.75     ← 实测
```
`compute_consumption` 只排除宿舍，其余一律 `BASE_CONSUMPTION = 1.0`。
但**加工站的心情是按次消耗（配方心情消耗）**，不搓材料就是 0/h；训练室的协助位另有规则。
同时这 9 条训练室 + 34 条加工站心情技能**根本没进技能库**（登记在台账里，`modeled=no`）。

### 1.5 特殊机制

| 机制 | 状态 |
|---|---|
| 阵营/标签 | ⚠️ 曾是手工表，**已修**（见 §三）：`cc.g.*`/`cc.tag.*` 28 组自动生成 |
| 变量（热情值/人间烟火/感知信息/无声共鸣/因果业报/木天蓼/魔物料理/乌萨斯特饮/外势实地/巫术结晶…） | ❌ 26 种全部无载体 → 依赖它们的 43 条 buff + 31 条 `partial/hold` 子句无法生效 |
| 心情落差 | ❌ 无载体（`A3` 档 6 条的唯一来源） |
| 事件类（消除/独占/互换/触发恢复/元修正） | ⚠️ 消除与独占已实现；`M15a/M15b/M17` 未实现 |
| 跨设施聚合（room1/room2/room3） | ✅ `config` 已按官方术语定义 |

---

## 二、目标架构

```
                          ┌───────────────────────────────┐
  resources/*.txt  ──────▶│  数据层（已就绪）              │
  (上游派生物 + 模板)      │  Skill / SkillEquip / 六轴模板 │
                          └──────────────┬────────────────┘
                                         │ template_id 派发
                          ┌──────────────▼────────────────┐
                          │  effects.py（新）              │
                          │  Effect handler 注册表         │
                          │  M01→M17 / X01→X11 各一个 handler│
                          └──────────────┬────────────────┘
                                         │ 产生 Contribution
   layout.py（新）──────────────────────▶│
   多房间 / 容量 / 副手 / 活动室 / 按类型聚合 │
                          ┌──────────────▼────────────────┐
   variables.py（新）─────▶│  Accumulator（新）             │
   变量账本（热情值等）      │  按轴 F 合并：SUM / MAX / POOL │
                          └──────────────┬────────────────┘
                                         │
                          ┌──────────────▼────────────────┐
                          │  ledger.py（新）               │
                          │  MoodLedger：逐条贡献流水账     │
                          │  → explain() 人类可读解释       │
                          └──────────────┬────────────────┘
                                         │
                          rules.py（重写为薄编排层）→ MoodResult(含 ledger)
```

### 2.1 五个核心抽象

**(1) `layout.py` —— 布局是一等公民**

```python
@dataclass
class Facility:
    ftype: FacilityType
    name: str                     # "制造站#2"（同类型可多个）
    level: int = 1
    operators: list[Operator] = []      # 进驻（占位）
    deputies: list[Operator] = []       # 副手（不占位，被"不包含副手"排除）
    slots: int = 0                      # 容量（0=不校验）
    atmosphere: Decimal | None = None   # 仅宿舍
    enabled: bool = True                # 停电/未建造

class BaseLayout:
    def of_type(self, ftype) -> list[Facility]      # 多房间
    def count_of_type(self, ftype) -> int           # 每有 N 间
    def all_dormitories(self) -> list[Facility]     # 所有宿舍
    def operators_in(self, ftypes) -> list[Operator]
    def working_operators(self, ftypes) -> list[Operator]   # "处于工作状态"
    def base_operators(self, include_deputies=False, include_activity_room=False)  # 基建内每有 1 名
```
新增 `FacilityType.PRIVATE`（活动室）。`get_facility()` 保留但标 deprecated（兼容旧调用与场景）。

**(2) `effects.py` —— 模板驱动的效果管道**

```python
class Bucket(Enum): CONSUME, RECOVER, EVENT

@dataclass(frozen=True)
class Effect:
    template: str          # "M08"
    bucket: Bucket
    stacking: Stacking     # 轴 F
    scope: Domain          # 轴 B
    targets: Target        # 轴 C
    def contribute(self, ctx: EffectContext) -> Iterable[Contribution]: ...

EFFECTS: dict[str, Effect]      # template_id -> handler
```
`rules.py` 退化为编排：
```python
def compute_recovery(world, op, facility):
    acc = Accumulator()
    for skill, owner in active_skills_reaching(world, op, facility):
        for c in EFFECTS[skill.template_id].contribute(ctx):
            acc.add(c)
    return acc.total(Bucket.RECOVER)
```
**新增机制 = 数据（模板行）+ 一个 handler**，不再改三处函数。

**(3) `Accumulator` —— 轴 F 统一落地**

```python
class Accumulator:
    def add(self, c: Contribution) -> None
    def total(self, bucket) -> Decimal
```
按 `stacking` 分组合并：`SUM` / `SAME_KIND_MAX`（同 template 取最高）/
`CROSS_OWNER_MAX`（同 `max_group` 内先按 owner 求和再取最高）/ `POOL`（平分给 recipients）/
`ZERO_PRIORITY`（归零优先）。
→ `_icebrew_recovery`、菲亚梅塔短路、`max_group` 特例**全部消失**，变成数据。

**(4) `ledger.py` —— 心情记录系统（本轮 #3 的核心诉求）**

```python
@dataclass(frozen=True)
class Contribution:
    bucket: Bucket
    template: str          # "M08"
    skill_id: str          # "control_dorm_rec_000#1"
    skill_name: str        # "领袖"
    owner: str             # "焰影苇草"
    target: str            # "刺玫"
    value: Decimal
    scope: str             # "所有宿舍（同种取最高）"
    stacking: Stacking
    detail: str            # 人类可读

@dataclass
class MoodLedger:
    operator: str
    consume: list[Contribution]
    recover: list[Contribution]
    def explain(self) -> str        # 中文逐条解释 + 合计 + 净速率
```
`MoodResult` 增加 `ledger` 字段；CLI 增加 `--explain`。
→ 直接回答"**为什么这个干员的净速率是 0.7**"，也让测试可以断言"贡献构成"而不只是数字。

**(5) `variables.py` —— 变量账本**

```python
@dataclass
class VariableLedger:
    values: dict[str, Decimal]      # "热情值" -> 20
    sources: dict[str, list[str]]   # 谁产的
```
技能声明 `produces` / `consumes`；阈值条件读它（`热情值 >= 40`、`每有 8 点热情值`）。
→ 解锁 43 条变量相关 buff + 剩余 31 条 `partial/hold` 中的大部分。

---

## 三、已完成（P1 布局 + P2 记录层 + P3 变量）

### P3 —— 变量账本（`variables.py` + `variable_producers.txt`）

- 官方术语表 26 种变量里，**3 种会影响心情**（人间烟火 / 热情值 / 无声共鸣），
  另有派生变量 **心情落差**（= 24 − 当前心情）。
- `VariableLedger`：变量值 + 产出者来源（可解释）；`basis_count()` 支持 4 种计数基准
  （`flat` / `dorm_operator` / `recruit_slot` / `sui_non_dorm`）。
- 消费端：`Skill.var_name` + `var_per`（每有 N 点 → 值 × floor(变量/N)）/ `var_min`（≥ N 才生效）。
- **不生效子句 31 → 25**，6 条变量消费子句从 `hold` 转 `apply`：
  孤光共照#2、跋山涉水#2、万里传书#2、无词颂歌#2、生活的重压、演技的怪物。
- 实测：热情值 40（若叶睦+祐天寺若麦+八幡海铃）→ 丰川祥子「生活的重压」+0.05 生效；
  人间烟火 50（夕+令+岁×4）→ 重岳「孤光共照」从 0.05 升到 0.15。



### P1 —— 布局模型（`models.py` / `config.py` / `scenario.py`）

- `FacilityType.PRIVATE`（活动室）补齐；上游 `rooms` 有 12 种 roomType（含 PRIVATE/ELEVATOR/CORRIDOR）。
- 容量与房间数**取自上游**：`FACILITY_MAX_COUNT`（`rooms[].maxCount`）、
  `FACILITY_SLOTS_BY_LEVEL`（`rooms[].phases[lv].maxStationedNum`）。
  实测：中枢 [1,2,3,4,5]、制造站 [1,2,3]×5 间、发电站 [1,1,1]×3 间、宿舍 [5]×4 间、活动室 0×6 间。
- 多房间查询：`of_type()` / `count_of_type()` / `all_dormitories()` / `facilities_in()`；
  对象查询：`working_operators()`（「处于工作状态」15 条 buff）、
  `base_operators()`（「基建内（**不包含副手及活动室使用者**）」29 条 buff）。
- `Facility.deputies`（副手）+ `slots`/`enabled`/`name`；`BaseLayout.validate()` 自检容量与房间数。
- **修掉一个真错误**：`compute_consumption` 只排除宿舍、其余一律 1.0，把**加工站算成 0.75/h**。
  现在按 `config.base_consumption(ftype)`：加工站 0（心情是按次消耗）、活动室 0、宿舍 0。

### P2 —— 记录层（`ledger.py` + `rules.py` 重写）

- `Contribution`：一条来源 = 谁（owner）+ 哪条技能（skill/template）+ 作用于谁（target）+ 值 + 叠加规则。
- `MoodLedger.total()`：轴 F 统一落地 —— `SUM` / `SAME_KIND_MAX`（同 group 取最高）/
  `CROSS_OWNER_MAX`（同 `max_group` 内先按 owner 求和再取最高）/ `POOL` / 归零 / 独占。
- `rules.py` 重写为**唯一计算路径**：先记 Contribution，再由 ledger 合成。
  重构前散落的 `max()`（9 处）、菲亚梅塔短路、冰酿专属函数、消除布尔判断，全部变成数据 + 一个 Stacking 分支。
- `mood_ledger(world, name).explain()` + CLI `--explain`：中文逐条解释，并标出
  「同种取最高，被更高者覆盖」「跨干员取最高，被更高者覆盖」。
- **流水账当场抓出 2 条数据错误**（数字上被 `max()` 掩盖，流水账里现形）：
  1. 刺玫「芬芳疗养·β」漏标 `enhanced=1` → α 与 β 同时生效；
  2. 反过来也查了一遍 —— 结论是 39 名多阶段槽位干员的 `replaces` 链**完整**（含赫拉格的三阶段链）。
  为此新增 `classify_skills.py → audit_enhanced()`：对照上游**槽位结构**校验提升链
  （同槽位多个 `buffData` = 提升链；除最低阶段外都应 `enhanced=1`）。
  **判定依据此前只能靠猜，现在有上游结构可依。**

### 第一批 —— 阵营/标签自动生成

| 项 | 内容 |
|---|---|
| ✅ 阵营/标签自动生成 | `scripts/generate_factions.py` 读上游 `termDescriptionDict` → `resources/factions.txt`（28 组 / 231 条） |
| ✅ 修掉系统性阵营错误 | 旧手工表把**米诺斯**6 人误标为「萨尔贡」；`岁` 只收 3/7。现全部以上游为准 |
| ✅ 消除类改按阵营 | `target_trait` → `target_faction`，`_factions_of()` 支持 Operator 与 `trait`/`factions` 覆盖 |
| ✅ 修掉被固化的测试 | `test_coop_with_faction_cc` 原先用**火神**（米诺斯）当"萨尔贡"同驻者；现用蜜蜡，并断言火神**不**触发 |
| ✅ 23 个新阵营可用 | 萨米、拉特兰、格拉斯哥帮、怪物猎人小队、泡影国狩猎小队… → 6 条 `M09` per-target 技能有数据可依 |

## 四、待办（按依赖顺序）

| 阶段 | 内容 | 解锁 |
|---|---|---|
| **P1** | `layout.py` 完整布局（多房间/容量/副手/活动室/按类型聚合）+ 修 加工站/训练室 base 消耗 | 目标 ① |
| **P2** | `effects.py` + `Accumulator` + `ledger.py`，`rules.py` 重写为薄编排 | 目标 ③ |
| ~~P3~~ | ~~变量账本（人间烟火/热情值/无声共鸣）~~ ✅ | 目标 ② |
| ~~P4a~~ | ~~可数条件（12 条）+ 「同种取最高」粒度修正 + 替换链 bug~~ ✅ | 目标 ② |
| ~~P4b~~ | ~~分支条件（会客室 6 条 / 潮汐守望 / 互为半身 / 资深料理人）~~ ✅ | 目标 ② |
| ~~P5-a~~ | ~~`M09` per-target 加成（+0.45，6 条）~~ ✅ 见 §P5a | 目标 ②③ |
| **P5-b** | `M15a/M15b/M17` 事件类、加工站按次消耗、训练室 9 条技能 | 目标 ①② |
| ~~P5-c~~ | ~~`skill_taxonomy.md` 缺口清单更新~~ ✅ | 可维护性 |

### P4a —— 可数条件 + 两处语义/实现修正（已完成）

12 条「每有 N 个什么」的子句全部启用（`basis` 参数 + `variables.basis_count`，新增 5 个基准）。
过程中查出并修掉两个**真问题**：

1. **「同种效果取最高」的粒度错了**。上游写「（叠加后的**最终值**同种效果取最高）」
   → 同一技能的多个分句要先求和。旧实现按单个分句取 max：
   死前必做清单 Lv5 得 0.15（正确 0.25）、倾谈者/寻同路人/无瑕心/柔和微光/独处 同类偏低。
   修法：`MoodLedger.total()` 的 `SAME_KIND_MAX` 改为「按 (持有者, skill_id) 先聚合分句，再取最高」。
2. **β 替换链把「同一技能的分句」当成链上环节**，导致额外分句"替换"掉自己的基础分句——
   实测 **6 例**基础值全部丢失：响石 0.15、铎铃「万里传书」-0.1、刺玫 0.15、波卜 0.2、
   流明 0.1、隐德来希 0.1。修法：替换链单位改为 **skill_id**（`SkillEquip.replaces` 存不带 `#clause` 的 id，
   `_active_skill_ids` 按 `base_skill_id` 整条剔除）。
   > 这两条都是**已存在**的 bug，不是 P4a 引入的；是"把更多子句打开"之后才显形。

**效果**：不生效子句 **25 → 13**（累计 37 → 13）。

### P4b —— 分支条件（已完成）

回上游取全文后，13 条里 **10 条可建模**，全部启用（不生效子句 13 → 3）：

| 子句 | 条件（上游原文） | 实现 |
|---|---|---|
| 会客室 6 条 | 「如果会客室内**只有自身处于工作状态**时，…心情每小时消耗 +N」 | `_cond_alone_in_facility` |
| 潮汐守望 `#2` | 「**反之**则自身心情每小时恢复 +0.5」 | `_cond_no_abyssal_outside_dorm` |
| 潮汐守望 `#3` | 「宿舍内深海猎人干员为满心情，则**额外** +0.5」 | `_cond_dorm_abyssals_full_mood` |
| 互为半身 | 「当与丰川祥子一起进驻控制中枢时，消除**自身**心情消耗的影响」 | `_cond_with_cc_xiangzi` + `self_only=true` |
| 资深料理人 `#2` | 「如果目标是**莱欧斯小队**干员，则恢复效果额外 +0.15」 | `_cond_target_in_faction("莱欧斯小队")` |

**同时修掉 5 个结构/实现缺口**（都是"打开更多数据"之后才显形的）：

1. **3 处贡献循环从不求值 `skill.condition`**：`DORM_GROUP` / `DORM_SELF` / 消除类。
   后果：资深料理人会无条件对所有人 +0.15；互为半身会无条件消除**所有人**的自耗
   （连丰川祥子的「生活的重压」都被消掉）。
2. **`M07b`（自身回复·非宿舍）从未被求值**：`_work_ledger` 只处理中枢 `CC_RECOVER`，
   干员自己的 `DORM_SELF` 技能在非宿舍设施里完全没有入口 → 潮汐守望「反之」分支从未生效。
3. **消除类缺 `self_only` 区分**：槐琥/令是「同设施所有干员」，互为半身是「仅自身」。
4. **数据管道不幂等**：`classify_skills.py --agd` 会把手写参数（`basis=` / `var=`）与人工判定
   （`partial_mode=apply`）**全部重置**——重跑一次就会摧毁 P3/P4a 的全部回填。
   现改为「已有的手写参数与人工判定优先，分类器只给新行填初值」，实测重跑后参数变化 **0 行**。

### P5a —— `M09` 定向加成（已完成）

上游 6 条 `dorm_rec_single*` 的原文是「…恢复 **+0.55**（同种效果取最高），**如果目标是 X，
则恢复效果额外 +0.45**」。本地 CSV 当初把两句挤成一行（+0.45 写进 `condition` 列、未建 clause），
所以只有基础 0.55 生效。

| 技能（持有者） | 目标 | 条件 |
|---|---|---|
| 沏茶（黑） | 锡兰 | `_cond_target_is("锡兰")` |
| 烤肉大师（特米米） | 嘉维尔 | `_cond_target_is("嘉维尔")` |
| 毒剂师之友（深靛） | 蓝毒 | `_cond_target_is("蓝毒")` |
| 降生于冰寒（寒檀） | 萨米 | `_cond_target_in_faction("萨米")` |
| 圣城趣事通（新约能天使） | 拉特兰 | `_cond_target_in_faction("拉特兰")` |
| 狩猎好帮手（罗德岛隐秘队） | `cc.tag.mh` **和** `cc.tag.mh2` | `_cond_target_in_faction("怪物猎人小队", "泡影国狩猎小队")` |

三处工程改造（都不是"给这 6 条打补丁"，而是把机制做进通用层）：

1. **手工补 `clause#2`**（本项目第一次补分句）：上游一个 buff 描述里含多个效果、本地 CSV 未拆时，
   按上游原文补行即可——`classify_skills.py` 是**原地重写**而非重建，补的行会保留
   （重跑 `--agd` 参数变化 0 行已实测）。
2. **`MoodLedger.same_kind_winner(bucket, group)`**：`_single_recovery` 旧实现按**单条分句**取 max，
   会把 +0.45 吃掉（§P4a 那条教训的同一类错误，只是发生在 `rules.py` 自己的循环里）。
   现改为把每条分句记进 `MoodLedger`，再由这个新方法与 `total()` **同源**地取获胜**技能实例**。
   > 判据（写进 AGENTS.md §10 清单）：凡是"同种取最高"的取值点，都不许自己写 `max()`。
3. **点名守卫扩展**：`check_faction_refs()` 现在也扫 `CLAUSE_COND` 的字符串字面量——
   阵营名对 `factions.txt`、干员名对 `operators.txt` **全量**
   （不能用 `DEFAULT_OPERATORS`：锡兰/嘉维尔/蓝毒都是只有生产/训练技能的干员）。
   实测把「萨米」写成「萨米X」、把「蓝毒」写成「蓝毒X」都会被拦下。

**遗留简化**：`_single_recovery` 仍是"全局一名受益者"（AGENTS.md §8.5），
故「毒剂师之友」上游未写「除自身以外」、深靛本可自指，本模型仍把她排除在候选外。

### 剩余（3 条，明确超出心情模型范围）

| 子句 | 为什么不做 |
|---|---|
| 投资·α / 投资·β（龙舌兰） | 条件是「**下笔赤金订单交付数大于 3**」——取决于贸易站订单队列/策略，属贸易机制；心情模型不建模订单状态 |
| 患难之交（菲亚梅塔） | 「与当前宿舍**前一位进驻**的干员互换心情」——需要**进驻顺序**（快照/时间序），当前布局模型是集合而非有序队列 |

**每阶段的硬约束**：黑盒测试必须全绿；新增机制必须走"模板 + handler"，不得再往 `rules.py` 塞分支。
