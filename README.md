# Rhodes-MoodSOC —— 干员"心情电池"建模工具

把《明日方舟》基建中的干员**心情**抽象为一块**电池**，基于 **BMS-SOC 安时积分法
（库仑计数 / Ampere-Hour Integration）** 完成数学建模，用于回答：

> 给定「当前干员信息 + 当前基建布局 + 目标时段」，
> 输出「目标时段后该干员的剩余心情」，以及「其余干员心情无限时，该干员还能工作多久」。

规则依据：
- 心情消耗/回复/工休的**计算规则**：`resources/心情消耗回复和工休时间.docx`；
- **真实技能库与干员↔技能映射**：`resources/moods_skills.txt` + `resources/operators.txt`
  （含精英化解锁等级、value 千分值、作用 family），由 `scripts/generate_skills_data.py`
  一键生成 `mood_soc/skills_data.py`。

> 技能数值以两份 txt 为**权威来源**（docx 中的技能示例值已过时）。
> 两份 txt 的**上游**是 `Kengxxiao/ArknightsGameData`（`zh_CN/gamedata/excel/building_data.json`）；
> 结构分析、逐条对照结果、以及「是否属心情类」的待判定清单见
> `documents/08-上游数据源分析.md`（该文档只是**数据源分析**，不参与计算）。
>
> **技能分类**：每条技能都挂在一个**六轴模板**（`M01`~`M17` 心情类 / `X01`~`X11` 非心情）上，
> 新增干员技能 = 认模板 + 填参数；上游全部 buff 都有覆盖台账，未归类会**硬报错**。
> 见 `documents/05-技能分类大纲.md`（分类大纲 + 模板字典）与 `resources/skills_registry.txt`（台账）。

### 心情流水账（可解释）

每条速率都能拆开看：谁 → 哪条技能 → 作用于谁 → 值多少 → 按哪条叠加规则合成。

```bash
python main.py --demo --target 泡泡 --explain      # 打印流水账
```
```python
from mood_soc import build_base_layout
from mood_soc.rules import mood_ledger
print(mood_ledger(world, "刺玫").explain())
# 回复  合计 4.2000
#      4.0000  [BASE] 宿舍基础回复　（1.5 + 0.1×5 + 0.0004×5000）
#        0.05  [M01] 焰影苇草「领袖」　（同种效果最取高）
#        0.15  [M08] 刺玫（自身）「芬芳疗养·β」　（同种效果取最高）
# 净速率 = 消耗 − 回复 = -4.2000 ……
```

### 分支条件

本地技能表里的条件文本曾被截断（只剩「如果」「反之」），现已回上游取全文并纳入模型：

| 技能 | 条件 | 心情效果 |
|---|---|---|
| 双面间谍 / 「职业操守」α·β / 我自己的愿望 / 专业经理·α·β | 会客室内**只有自身**在工作 | 自身消耗 +1~+2 |
| 潮汐守望（歌蕾蒂娅） | 宿舍以外每有 1 名深海猎人（**含她自己**） | 自身消耗 +0.5/名；「反之」的恢复分支随之不可达（见 `documents/04-特殊机制.md` 第 23 条） |
| 互为半身（若叶睦） | 与丰川祥子同中枢 | 消除**自身**心情消耗影响 |
| 资深料理人（森西） | 目标是**莱欧斯小队**干员 | 恢复效果额外 +0.15 |

### 定向加成（如果目标是 X，恢复效果额外 +0.45）

上游 6 条单体回复技能写的是「…恢复 **+0.55**（同种效果取最高），**如果目标是 X，
则恢复效果额外 +0.45**」——加成对象由**被锁定的那名干员是谁**决定：

| 技能（持有者） | 目标 | 命中时该宿舍的单体回复 |
|---|---|---|
| 沏茶（黑） | 锡兰 | **1.00**（未命中 0.55） |
| 烤肉大师（特米米） | 嘉维尔 | 1.00 |
| 毒剂师之友（深靛） | 蓝毒 | 1.00 |
| 降生于冰寒（寒檀） | **萨米** 干员 | 1.00 |
| 圣城趣事通（新约能天使） | **拉特兰** 干员 | 1.00 |
| 狩猎好帮手（罗德岛隐秘队） | **怪物猎人小队** 或 **泡影国狩猎小队** | 1.00 |

实现要点：基础分句与加成分句属**同一条技能**，必须先求和（0.55+0.45）再与临光「使徒」0.50
等其它单体回复取最高——见 `MoodLedger.same_kind_winner()`。目标可以是具体干员
（`_cond_target_is`）或阵营/标签（`_cond_target_in_faction`，可传多个 = 并集）。

### 元修正（强化别人的效果）

有一类技能**自己不回复**，而是把**别人的恢复效果**顶上去。上游原文
（摩根「头号陪练」）：

> 进驻宿舍时，**推进之王**对该宿舍中**格拉斯哥帮**干员恢复效果额外 **+0.3**

```
推进之王「狮心王」= 宿舍全体 +0.2（同种效果取最高）
  └─ 摩根在同一个宿舍时 · 目标属格拉斯哥帮 → +0.3
       → 格拉斯哥帮成员（推进之王 / 摩根 / 达格达 / 因陀罗）实际拿 0.5
       → 其他人仍是 0.2
```

实现要点：增量必须**并入被强化技能的小计**（`0.2 + 0.3 = 0.5` 后再与其他技能取最高），
**不能**记成一条独立技能——否则会被"同种取最高"当成竞争者，`max(0.2, 0.3) = 0.3` 反而算少。
数据形态：`family=dorm_meta` → `SkillKind.DORM_META`、`template_id=M17`、
`params` 里的 `boost_provider`（强化谁）+ `boost_group`（强化哪一组）。

### 进驻事件（M15a：患难之交）

有一类技能的效果不是"每小时 ±N 点"，而是**进驻那一刻的一次性跳变**。典型是
菲亚梅塔「患难之交」：

> 进驻宿舍时，**如果自身为满心情**，则与当前宿舍**前一位进驻**的干员互换心情

因为它是**布局初始化**语义而非速率语义，所以走**显式开关**（默认不结算）：

```bash
python main.py --scenario-file x.json --target 菲亚梅塔 --entry-events
# [进驻事件] [M15a] 菲亚梅塔「患难之交」　（与「前一位进驻」的 路人 互换：菲亚梅塔 24 → 6，路人 6 → 24）
```

#### 换不换、在哪换、换谁、换完怎么放、要不要等她——全都能配

**场景 JSON 顶层**加一个 `entry_events` 就能定这些事（不必每次敲命令行开关）：

```json
{
  "entry_events": {
    "enabled": true,
    "scope": "anywhere",
    "swap_with": "any",
    "restore_back": true,
    "force": true,
    "per_shift": [
      {"swap_with": "巫恋", "force": true},
      {"swap_with": "any"},
      {"enabled": false}
    ]
  },
  "facilities": [
    {"type": "宿舍", "level": 5, "operators": [{"name": "菲亚梅塔", "mood": 24}]},
    {"type": "制造站", "level": 3, "operators": [{"name": "路人", "mood": 2}]}
  ]
}
```

| 字段 | 取值 | 含义 |
|---|---|---|
| `enabled` | `true` / `false` / 省略 | `true` = 默认结算（CLI/界面不用再开）；`false` = 这个布局不换心情；省略 = 未配置 |
| `scope` | `"dorm"`（默认）/ `"anywhere"` | **在哪换**：`dorm` 只在同一宿舍找人；**`anywhere` = 基建任意位置**（任何设施上的干员都能换） |
| `swap_with` | 人名 / `"any"` / 省略 | **换谁**：人名 = 指定；`"any"`（或 `"任意"`/`"最累"`）= **自动挑全基建心情最低的那位**；省略 = 「前一位进驻」（`scope=anywhere` 时省略也走自动挑） |
| `restore_back` | `true`（默认）/ `false` | **换完怎么放**：`true` = 把被换满的那位**换回原位**（两人都留在自己的岗位上，只交换心情）；`false` = **位置也一起互换**（她接管对方岗位、对方进她的位置）。⚠️ 图形界面已把这一项收掉，**固定按 `true`** |
| **`when`** | `"immediate"`（引擎默认）/ `"wait"` / `"full"` | **她自己的心情门槛**：「对方心情是多少」**不构成限制**（照换）。`immediate` = 连她满不满都不看，**立刻换**；`wait` = 到点没满就**等她回满再换**；`full` = **只在她满心情时换**（游戏原文口径）。图形界面只写后两档（勾「强制切换」＝`wait`，不勾＝`full`） |
| `force` | 旧字段 | 兼容保留：写了 `force` 就按旧语义（`true`→`wait`、`false`→`full`） |
| **`per_shift`** | 数组 / 对象 | **按班次覆盖**上面各项 —— **每个班"用不用、换给谁、什么时候换"都可以不同** |

#### 按班次（3 班 12/6/6 就是典型）

```json
"per_shift": [
  {"swap_with": "巫恋", "when": "wait"},         // 第 1 班：换巫恋，等她满心情再换
  {"swap_with": "any"},                          // 第 2 班：自动挑当时最累的那位
  {"enabled": false}                             // 第 3 班：这一班不换
]
```

- 列表写法 = **按班次位置**（第 1/2/3 班）；也支持字典写法用**序号或班次名**定位：
  `"per_shift": {"2": {"swap_with": null}, "Shift 3 · 6h": {"enabled": false}}`。
- **逐字段继承**：没写的字段沿用全局；写 `null`（或 `""`）= **明确清空**该字段
  （`swap_with: null` = 这一班回到"不指定"口径）。
- 想让某班"明确用同宿舍的前一位进驻"而不受全局 `scope: "anywhere"` 影响：
  写 `{"swap_with": null, "scope": "dorm"}`。
- 每班的有效配置可以用 `Schedule.entry_config_for_shift(i)`（界面）或
  `models.resolve_entry_config(cfg, i, 班次名)`（引擎）算出来。

宽松写法：`"entry_events": true` / `false` / `"某人"`；`"anywhere": true` 等价于 `"scope": "anywhere"`。

```python
from mood_soc import apply_entry_events, entry_event_holders, find_entry_target, entry_target_kind
# ⚠️ apply_entry_events 就地修改 world（心情；restore_back=False 时还包括位置）
events = apply_entry_events(world)                                    # 默认：结算，用"前一位进驻"
events = apply_entry_events(world, swap_with="路人")                   # 指定与谁换
events = apply_entry_events(world, scope="anywhere", swap_with="any")  # 任意位置 + 自动挑最累的
events = apply_entry_events(world, restore_back=False)                 # 连位置一起换
events = apply_entry_events(world, when="full")                       # 只在她满心情时换（游戏原口径）
events = apply_entry_events(world, when="immediate")                   # 连她满不满都不看，立刻换
events = apply_entry_events(world, enabled=False)                     # 明确不换
events = entry_event_holders(world)                                   # [(触发者名, 所在房间)] 供界面提示
target, why = find_entry_target(world, holder, dorm, "any", "anywhere")  # 预览"会换谁"
entry_target_kind("any", "dorm")                                      # 'auto'（行为与文案同源）
```

> **强制交换**：只要开了并设了对象，就**执行互换**——「对方心情是多少」**不构成限制**，
> 而且**不再因为"双方心情相同"而跳过**（哪怕两边都是 24，事件照记、`restore_back=false` 时
> 位置照换，标记里会注明"数值不变"）。**她自己的心情门槛**由 `when` 决定：
> `immediate`（连她满不满都不看）/ `wait`（等她回满）/ `full`（没满就不换）。
> 同一份布局快照只结算一次（`Operator.entry_swapped` 标记），所以重复调用不会来回换。
>
> `when="wait"` 的"等待"是**带时间**的语义，只在排班模拟里生效：
> `ui.schedule.simulate_schedule(..., entry_when="wait", entry_per_shift=[...])`。
> 一次性 API 不会等待（配了 `wait` 但她当前没满时会记一条说明，而不是静默）。
> 实测示例：她红脸时「自律」失效 → 按宿舍基础 4/h 回满 → **恰好走到 24 的那一刻**触发换心情。

**优先级**（两边都能配，规则简单）：

| | 取值 | 谁说了算 |
|---|---|---|
| 换不换 | `enabled` 参数 / JSON `enabled` / 都没有 | **显式参数 > JSON > 默认结算**（"调用这个函数"本身就是"要结算"） |
| 换谁 / 在哪换 | 同名参数 / JSON 同名字段 / 都没有 | **显式参数 > JSON > 默认**（默认＝前一位进驻、仅同宿舍） |
| 换完怎么放 / 什么时候换 | `restore_back` / `when` | 同上；**图形界面固定 `restore_back=true`**，`when` 只写 `wait`/`full` |
| **按班次** | `per_shift`（JSON）或界面里的「高级：按班次单独设置」 | **界面传的 > JSON 的**；每班内再"逐字段覆盖全局" |

命令行 `--entry-events` 与界面上的勾选都是"显式参数"，因此它们**优先于** JSON 里的 `enabled: false`。

「前一位进驻」= `Facility.operators` 里排在触发者之前的那一位（该列表本来就是进驻顺序）。
幂等：换完她就不再是满心情，重复调用不会再换回来。
指定的对象不在允许范围内时**不换**，并记一条 `Bucket.EVENT`（group=`entry_swap_skipped`）说明原因。

### 可数条件（每有 N 个什么）

「每有 1 间发电站」「当前宿舍每级」「每个招募位」「每有 1 名其他干员」这类条件**可以从布局直接数出来**，
现已全部纳入模型（12 条子句）：

```bash
# 死前必做清单（响石）= 0.15 + 0.02×宿舍等级，再与其它宿舍群体回复取最高
# 寻同路人（斥罪）= 0.15 + 0.05×办公室等级（招募位）
# 柔和微光（流明）= 0.15（β）+ 0.05×发电站数
```
上游原文明确写「…额外 +N 恢复效果（**叠加后的最终值**同种效果取最高）」——
即同一技能的多个分句要**先求和**，再与其他技能取最高。

### 变量（技能间的中间货币）

官方术语表定义了 26 种变量，其中 **人间烟火 / 热情值 / 无声共鸣** 会影响心情，例如：

```
重岳「知我为我」→ 人间烟火 +5/每个（宿舍/活动室以外的）岁干员
              → 重岳「孤光共照」每 20 点人间烟火，room2 心情回复额外 +0.05
若叶睦「演技的怪物」+20 / 祐天寺若麦「勤学苦练」+10 / 八幡海铃「可靠伙伴」+10
              → 丰川祥子「生活的重压」在热情值 ≥ 40 时自身消耗 +0.05
塑心「无声共鸣」+1/每名宿舍干员 → 塑心「无词颂歌」每 5 点，宿舍回复额外 +0.01
```

产出端逐条注明上游出处：`resources/variable_producers.txt`；实现见 `mood_soc/variables.py`。
流水账 `--explain` 会打印变量快照与"由谁产出"。

### 基建布局

多房间（4 制造站 / 4 宿舍 / 3 发电站）、容量、副手、活动室都是一等公民：

```json
{"facilities": [
  {"type": "制造站", "level": 3, "name": "制造站#1", "operators": ["泡泡"], "deputies": ["火神"]},
  {"type": "活动室", "level": 1, "operators": ["某人"]}
]}
```
- 容量与房间数上限取自上游 `rooms[].maxCount` / `phases[lv].maxStationedNum`；`build_base_layout(data, validate=True)` 可自检。
- `get_facility()` 只取第一个同类型设施（兼容旧用法），多房间用 `of_type()` / `count_of_type()`。

### 设施与心情消耗

**常规生产设施**的基础消耗是 **1 点/时**（需求文档第 4 段），中枢满员再全局 -0.25，
制造/贸易按进驻人数 -0.05/-0.1：

| 设施 | 每小时基础消耗 | 说明 |
|---|---|---|
| 制造站 / 贸易站 / 发电站 / 办公室 / 会客室 / 控制中枢 | 1.0 | 中枢另给全局减免 |
| **加工站 / 训练室** | **0** | **挂件位**——不计算心情消耗（见下） |
| 宿舍 | 0 | 只回复 |
| 活动室 | 0 | 不视作入住在基建内，不纳入心情模型 |

#### 挂件位：加工站 / 训练室

这两个位置的**实际用法都是放挂件**：

> 加工站、训练室的教练位的作用只是用来放**挂件干员**——挂件本身并没有什么技能，
> 但**只要在基建内（不包含副手及活动室使用者）**，就可以为其他干员提供效果服务。

也就是说，挂件被放进来的唯一目的是「**人在基建内**」，好让**别人**的计数类技能数到它
（如「基建内每有 1 名萨米/深海猎人/骑士干员…」）。**挂件不需要休息**，
所以给它们算心情消耗没有意义：

```
训练室/加工站：心情消耗 = 0/h  → 挂件永不红脸 → 它提供的"在场"效果持续生效
（对照）制造站 路人 = 1.0/h、制造站 泡泡 = 0.4/h
```

推论：那 9 条写「进驻训练室协助位时，心情每小时消耗 **+1**」的训练室技能**不生效**
（工作狂 / 过量训练 / 索然无味 / 何须解脱 / 变异 / 斗争渴望 / 与人乐 / 兴之所至·β / “手段应当有效”）
——已从技能库撤出，`resources/skills_registry.txt` 保留登记与原因。

> 与需求文档第 4 段「干员工作时…每小时基础消耗速率 1 点」**不冲突**：
> 那说的是常规生产设施「上岗生产」的稳态消耗，挂件位不属于上岗生产。

加工站另有原因：上游 35 条加工站心情 buff **全部**说的是「**配方**心情消耗」
（「心情消耗为 4 的配方全部 -1」「相应配方的心情消耗恒定为 2」「全部除以 4」…），
即心情是**按次**扣的；而**配方自身的心情消耗在上游数据 dump 里没有字段**，
所以"一次加工扣多少心情"也不建模（棘刺「爆炸艺术」同理）。

### 开发约定

- **每次改动即时提交**：每完成一次修改就 `git commit` 一次，提交信息用 **1–15 个字**简要描述
  （如「修复替换链」「补变量账本」）。一次提交只做一件事。
- **改完同步文档**：至少更新本文件与 `AGENTS.md`，以及 `documents/` 下受影响的那一篇。
- **目录分工**：`resources/` 只放**数据**，`documents/` 放**文档**（按门类分文件，
  索引见 `documents/README.md`）；根 `AGENTS.md` 是给 AI 的**精简入口**，保持精简
  （64KB 指令预算，超了会被截断），细节一律写进 `documents/`。

### 技能分类与阵营表

- **分类**：每个技能 clause 挂在六轴模板（`M01`~`M17` 心情类 / `X01`~`X11` 非心情）上，
  新增干员技能 = 认模板 + 填参数；上游全部 755 条 buff 都有覆盖台账，未归类**硬报错**。
  见 `documents/05-技能分类大纲.md`、`resources/skills_registry.txt`。
- **阵营/标签**：`resources/factions.txt` 由 `scripts/generate_factions.py` 从上游
  `cc.g.*` / `cc.tag.*` 自动生成（28 组 231 条），**不手工维护**
  （人工补充只有上游不列名单的「异格者」）。生成器会校验技能引用的阵营名都存在，
  **包括写在条件表达式里的名字**（`_cond_target_in_faction` / `_cond_target_is`），
  写错阵营名或干员名都会直接报错。
- **架构**：运行时（派发/布局/记录）的可拓展性诊断与重构设计见 `documents/07-设计史.md`。
- **数据查找**：完整的上游查证策略见 `documents/06-数据来源.md`
  （拉取命令、四个坑、查完之后的三件事，以及**哪些数据上游根本没有**的清单——
  例如加工站「配方心情消耗」在数据 dump 里没有字段，别去反推）。

### 🔎 数据查找策略（强制）

遇到任何**不知道的数据**——技能原文 / 数值 / 解锁精英化与等级 / 阵营成员名单 / 设施集合定义 /
全局常量 / 机制术语——**先去上游仓库查证**：
[**Kengxxiao/ArknightsGameData**](https://github.com/Kengxxiao/ArknightsGameData)（`zh_CN/gamedata/excel/`）。
**不要凭印象写、不要猜、不要从二手资料誊抄。** 本项目的 `resources/*.txt` 与
`mood_soc/skills_data.py` 都只是**上游的派生物**；两者不一致时**以上游为准**。

- 技能/数值/解锁 → `building_data.json`（`buffs` + `chars[].buffChar[].buffData[]`）
- 干员名 → `character_table.json`；术语/阵营/设施集合/常量 → `gamedata_const.json`
- 拉取方式（不要整仓 clone）：
  `git clone --filter=blob:none --depth 1 --no-checkout <仓库> <目录>` 后用
  `git sparse-checkout set zh_CN/gamedata/excel` 只取表。
- 注意：仓库是**纯数据 dump**，没有公式实现；"怎么算"仍以需求 docx 为准。
- 完整策略（含四个坑、查完后的动作、**上游缺失清单**）见 `documents/06-数据来源.md`。

> **精度策略**：全部数值计算使用 Python 标准库 `decimal.Decimal`（十进制精确），
> 避免 float 无法精确表示 `0.1 / 0.3 / 0.05 / 0.0004` 等十进制小数带来的累积误差。
> 内部"无限"用 `Decimal('Infinity')` 表示；仅在 JSON 输出边界做一次受控的
> **6 位小数舍入**（`mood_soc/output.py`），并把无限输出为 `null`。

---

## 一、数学建模：心情 = 电池

| 电池概念 | 心情概念 |
|---|---|
| 容量 `C` | 心情上限 `24` |
| 荷电状态 SOC | 当前心情（0 ~ 24，可含小数） |
| 放电电流 | 净心情消耗速率（点 / 小时） |
| 充电电流 | 净心情回复速率（点 / 小时） |

**核心公式（安时积分法）：**

```
                    t
SOC(t) = SOC(t0) - ∫ I(τ) dτ        I(τ) = 消耗速率(τ) - 回复速率(τ)
                    t0
```

- `I > 0`：心情下降（放电）
- `I < 0`：心情上升（充电，即"休息中 / 空闲中"）
- `I = 0`：心情不变

**离散化**（用于时间步进模拟）：

```
SOC[k+1] = clamp( SOC[k] - I[k] * Δt,  0, 24 )
```

其中 `clamp` 把心情钳位在 `[0, 24]`（下限为"红脸/空电池"，上限为满心情）。

**解析结果**（假设速率恒定、其余干员心情无限）：

```
剩余心情  = clamp( SOC0 - I * T, 0, 24 )        （工作 T 小时后）

sustain_hours（还能维持/恢复多久，evaluate 输出）：
  I > 0（工作）→ 剩余心情 / I                    到红脸
  I < 0（宿舍）→ (24 - 剩余心情) / (-I)          恢复到满心情
  I = 0（不变）→ null
```

> 注意：底层函数 `remaining_work_hours(world, name)` 仍表示"从当前心情起算、还能工作多久"；
> 而 `evaluate(world, name, T)` 输出的 `sustain_hours` 是先推进 T 小时、再按净速率符号
> 给出"维持/恢复时长"。CLI 走的是后者。

---

## 二、净速率 I 的构成

### 心情消耗

```
消耗 = 1（基础）
     - X      设施基础减免（制造/贸易按进驻人数：1人0 / 2人0.05 / 3人0.1；其它设施0）
     - 0.25   控制中枢满员全局减免（按人数线性折算）
     ± 自身技能        （泡泡 -0.25、火神 α-0.15/β-0.25、阿罗玛 +0.25、斥罪 +0.5 等）
     ± 同设施设施级技能  （黍 -0.1、夕"不以物喜"中枢 -0.05，作用于全体含自身）
     ± 同设施其他干员技能（巫恋"低语" +0.25，作用于其他干员、不含自身）
     - 中枢减免技能  （预留：维什戴尔/重岳等真实数据已归为"回复"，见下）
```

特殊规则：
- **消除类**（槐琥 / 令）：移除目标干员"自身技能"的正负影响，但**不**影响中枢减免、
  **不**影响设施级技能；令额外限定 `trait="岁"`。
- **红脸**（心情 ≤ 0）：该干员所有技能失效（仍可继续工作，只是效率下降）。
- **精英化判断**：技能是否生效 = 干员 `elite >= 技能解锁 elite` 且 `level >= 解锁 level`；
  "精英化提升"版技能（如 火神 β）解锁后**替换**低版本（α），不叠加。

### 心情回复

```
宿舍回复 = 白字(1.5 + 0.1×等级) + 绿字(0.0004×实际氛围 + 技能加成)
        + 中枢干员对宿舍的回复（领袖/战纹/巡心/羁绊相生等）
        + 自身回复 + 群体回复 + 单体回复 + 定向回复
工作设施回复 = 中枢技能提供的回复（玛恩纳中枢+0.05/发电办公会客+0.1、维什戴尔工作设施+0.1、
              重岳+0.05、冰酿中枢+0.05）
```

- 不同类型（自身/群体/单体/定向）**可叠加**；同种类型**取最高**。
  ⚠️「同种」的比较单位是**技能**（含它的各个分句），不是单个分句：基础分句与
  「如果目标是 X，额外 +0.45」这类加成分句要先求和，再去和其它技能比
  （详见上文「定向加成」）。
- 特殊干员：**菲亚梅塔**（自身 +2、不接受其它来源）；**冰酿**（0.8 总额平分给未满成员）。

### 阵营联动（因其他阵营/干员存在而改变心情）

三类联动技能，均已生效（阵营表 `skills.OPERATOR_FACTIONS` **由上游自动生成**）：

1. **阵营计数类（per-count）**：value 是"每个该阵营干员"的回复量，总回复 = `value × 目标设施内
   该阵营干员数`。例：陈"德才兼备"（中枢内每个龙门近卫局干员 +0.05）、电弧"无言的慈爱"
   （宿舍内每个精英干员 +0.1）、异格者/彩虹小队/谢拉格/乌萨斯/鲤氏等。
2. **与具体干员共事**：同中枢（`_cond_with_cc_operator`）/ 同设施（`_cond_with_facility_operator`）。
   例：德克萨斯"恩怨"与拉普兰德同贸易站 +0.3、老鲤"浮生得闲"与阿同中枢、魔王"魔王传承"与阿米娅同中枢。
3. **与阵营共事**：同中枢且排除自身（`_cond_with_cc_faction`）。
   例：摆渡人"英雄的骄傲"与萨尔贡干员同中枢 +0.02。

> 阵营表 `OPERATOR_FACTIONS` **不是手工维护的**——它与 `FACTION_MEMBERS` 一起由
> `scripts/generate_factions.py` 从上游 `cc.g.*` / `cc.tag.*` 生成（28 组 / 208 名干员 / 231 条记录），
> 人工补充只有上游不列名单的「异格者」（`resources/factions_supplement.txt`）。
> 要改干员↔阵营，改上游或补充表后重跑生成器，**不要**改代码。

### 工休比

```
可连续工作 = 24 / x          从零回满 = 24 / y
最大工休比 = y / x           最大工作时长占比 = y / (x + y)
24h 内最长可工作时间 = 24y / (x + y)      （x = 工作消耗，y = 休息回复）
```

---

## 三、项目结构（高内聚、低耦合）

```
Rhodes-MoodSOC/
├── main.py                命令行入口（--mode single|base / --demo / --scenario-file / --target / --period
│                            / --out-dir / --json-file / --trace / --explain / --entry-events）
├── mood_soc/              核心包（库，可被 import）
│   ├── __init__.py        对外公共 API 汇总（导出下面各模块的公开符号）
│   ├── config.py          纯配置层：常量 + 数据表 + 解析函数（无逻辑）
│   ├── battery.py         纯数学层：安时积分法 + MoodBattery + to_decimal/INF（与游戏规则无关）
│   ├── models.py          数据模型层：Operator / Facility（多房间·容量·副手·活动室）
│   │                      / BaseLayout（按类型聚合）/ MoodResult（含 ledger）/ OperatorResult / BaseResult
│   ├── skills.py          规则数据层：Skill / SkillEquip / SkillKind 框架 + 条件函数（末尾 re-export 数据）
│   ├── skill_templates.py 分类字典：六轴枚举（ModelTier/Domain/Target/Effect/ValueShape/Stacking）+ 模板注册表
│   ├── ledger.py          ★记录层：Contribution / MoodLedger（逐条贡献流水账）+ 轴 F 统一合成
│   ├── variables.py       ★变量账本：26 种"中间货币"（人间烟火/热情值/无声共鸣…）+ 产出者收集
│   ├── skills_data.py     技能数据表（自动生成，勿手改）：SKILLS / DEFAULT_OPERATORS / SKILL_EQUIPS / TRAITS
│   ├── maa.py             输入解析层：MAA 排班 JSON -> facilities（脚本与图形界面共用这一份映射）
│   ├── rules.py           业务逻辑层：**流水账驱动**——把"布局 + 干员"折算成消耗/回复/净速率 + 各项查询
│   ├── simulator.py       时间步进模拟器：每步重算速率，处理"红脸 → 技能失效"等时变情况
│   ├── report.py          展示层：中文结果格式化（文本）
│   ├── output.py          输出层：结果 -> JSON dict / 写入文件（inf -> null）
│   └── scenario.py        输入解析层：字典/JSON -> BaseLayout
├── ui/                    ★ **图形界面**（tkinter，纯标准库；见 documents/10-图形界面.md）
│   ├── schedule.py        多班排班模型 + 整周期心情轨迹（事件驱动精确积分）——不依赖 GUI
│   ├── theme.py           配色/字体/间距令牌 + 颜色混合 + 显示格式化（唯一的显示舍入处）
│   ├── widgets.py         心情芯片（21px 紧凑呈现单元：色条 + 位置标记 + 名字 + 心情值）
│   ├── board.py           基建看板：控制中枢整行 + 工作区/休息区两列（一屏放下全部房间）
│   ├── roster.py          「全员一览」条：整个周期的全部干员，一屏摆开、不滚动
│   ├── chart.py           心情曲线（Canvas 手绘：坐标轴 / 网格 / 班次分界 / 悬停读数）
│   ├── dialogs.py         选人 / 设心情 / 班次设置 / 进驻事件设置对话框 + 心情输入校验
│   ├── batch.py           「批量设置」对话框：当前布局的干员 + 心情一张表改完
│   ├── app.py             主窗口（工具栏 + 看板 + 全员一览 + 曲线 + 时间滑块 + 状态栏）
│   └── __main__.py        `python -m ui` 入口
├── tests/                 黑盒测试（只断言"输入 → 输出"，不测内部结构）
│   ├── __init__.py
│   ├── test_api_blackbox.py        公开 API 黑盒：场景 JSON + 目标/时段 → 结果 JSON
│   ├── test_cli_blackbox.py        命令行黑盒：subprocess 调 main.py → stdout JSON / 退出码 / 结果文件
│   ├── test_ui_schedule_blackbox.py 图形界面的计算核心黑盒（含"不拉起 tkinter"的结构断言）
│   ├── test_ui_app_smoke.py        界面端到端冒烟（真建窗口；无图形环境自动跳过）
│   └── test_ui_batch_blackbox.py   「批量设置」对话框黑盒（真建窗口；无图形环境自动跳过）
├── scripts/
│   ├── maa_to_scenario.py      把 MAA 排班 JSON 转成本工具的场景 JSON（解析在 mood_soc/maa.py）
│   ├── classify_skills.py      给每个 clause 挂六轴模板 + 生成 755 行覆盖台账 + 零遗漏校验
│   ├── generate_factions.py    从上游 termDescriptionDict 生成干员↔阵营/标签表
│   └── generate_skills_data.py 把 resources 两份 txt 生成为 mood_soc/skills_data.py（技能数据管道）
├── scenarios/             demo.json + maa_shift1/2/3.json（示例场景）
├── resources/             ★ **数据**（不放文档）
│   ├── 心情消耗回复和工休时间.docx        需求文档（心情消耗/回复/工休的**计算规则**）
│   ├── moods_skills.txt / operators.txt   技能库 + 干员↔技能映射（含 template_id/params）
│   ├── skills_registry.txt                上游 755 条 buff 的覆盖台账
│   ├── factions.txt / factions_supplement.txt  阵营/标签表（上游生成）
│   ├── variable_producers.txt             变量产出者表（人间烟火/热情值/无声共鸣）
│   └── arknights-infra-schedule-maa.json  MAA 排班样例（转换脚本的输入）
├── documents/             ★ **文档**（按门类分文件，索引见 documents/README.md）
│   ├── README.md            文档索引 + §编号约定 + 维护约定
│   └── 01-架构.md … 10-图形界面.md
├── results/               运行生成的结果 JSON（已被 gitignore）
├── README.md              面向人类的完整说明（人类入口）
├── AGENTS.md              给 AI 的精简入口（DSH 只从项目根自动加载它）
├── requirements.txt       仅标准库，无第三方依赖（Python 3.9+）
└── .venv/                 Python 3.14 虚拟环境（uv 创建）
```

依赖关系单向向下：`main / ui / tests → report/output/simulator/rules → skills/models → config/battery`，
无环、无横向耦合。`ui/` 是**与 `main.py` 并列的另一个入口层**（`mood_soc` 不知道 `ui` 存在；
`ui/schedule.py` 不得 import tkinter，有测试盯着）。

---

## 四、使用方法

> 两种用法：**命令行**（本节的 `--mode single|base`，算一次）与
> **图形界面**（`python -m ui`，看整周期 —— 见本节最后的「4) 图形界面」）。

工具分两种模式（`--mode`）：

- **single（默认）**：只算一个干员。输入「干员 + 布局 + 目标时段」→ 输出该干员时段后的
  剩余心情，以及"其余干员心情无限"时从剩余心情起算还能工作多久。
- **base**：算整个基建。基于当前布局 → 输出每个干员的心情，以及"没有一个干员红脸"
  的条件下该布局还能维持工作多久（= 所有干员到红脸时长的最小值）。

两种模式都：把结果以 **JSON** 打印到标准输出；加 `--json-file` 才额外**生成文件**到 `results/` 目录（默认不写）。

### 0) 命令行参数总览

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--mode` | `single` / `base` | `single` | 测算模式：`single` 只算一个干员；`base` 算整个基建 |
| `--demo` | flag | 关 | 使用 `main.py` 内置的 `DEMO_SCENARIO`（与 `scenarios/demo.json` **只差办公室干员**：内置用「遥」、demo.json 用「斥罪」） |
| `--scenario-file` | 文件路径 | 无 | 从 JSON 文件读取场景 |
| `--target` | 干员名 | 无 | single 模式的目标干员；**必须显式提供**（`--demo` 也不会替你补「泡泡」） |
| `--period` | 小数（小时） | `0.0` | 目标时段；**两种场景来源都缺省 0**（即只看当前状态，不推进时间） |
| `--out-dir` | 目录 | `results` | JSON 文件输出目录（仅配合 `--json-file` 生效） |
| `--json-file` | flag | 关 | 是否额外把结果写入 JSON 文件（默认只打印到 stdout） |
| `--trace` | flag | 关 | 仅 single 模式：附加心情轨迹（时间步进模拟，输出到 stderr） |
| `--entry-events` | flag | 关 | 先结算**进驻事件**（M15a 患难之交：菲亚梅塔进驻宿舍时与同宿舍某人互换心情）再测算；场景 JSON 顶层写 `"entry_events": {"enabled": true, "swap_with": "某人"}` 也能开启并指定与谁互换 |

**场景来源优先级**：`--demo` > `--scenario-file`；两者都不给则打印帮助并退出。

**single 模式**：必须提供 `--target`（不带则打印错误并退出码 1）。`--period` 缺省 `0`（只看当前状态）。

**base 模式**：忽略 `--target`；`--period` 缺省为 0；`--trace` 不生效。

**输出约定**：stdout **永远是纯 JSON**（便于程序解析）；错误提示、文件路径、`--trace` 轨迹都走 stderr。

**文件输出**：默认不写文件；加 `--json-file` 后，结果写入 `--out-dir`（默认 `results/`）下的
`single_<干员>_<时间戳>.json` 或 `base_<时间戳>.json`，并在 stderr 打印路径。

### 1) single 模式

```bash
python main.py --demo --target 泡泡                              # 演示场景，目标泡泡，period 缺省 0
python main.py --demo --target 泡泡 --period 8                  # 推进 8 小时
python main.py --demo --target 遥 --period 8                    # 指定其它目标（内置演示的办公室干员是「遥」）
python main.py --scenario-file scenarios/demo.json --target 斥罪 --period 8   # 自定义场景里的斥罪
python main.py --demo --target 泡泡 --trace                    # 附带心情轨迹（文本）
python main.py --demo --target 泡泡 --json-file                # 额外把结果写入 results/ 下的 JSON 文件
```

返回 JSON（`--demo --target 泡泡 --period 8` 的真实输出）：

```json
{
  "mode": "single",
  "operator": "泡泡",
  "facility": "制造站",
  "period_hours": 8,
  "net_rate": 0.05,
  "state": "工作中",
  "mood": 23.6,
  "sustain_hours": 472
}
```

> 泡泡当前净速率 0.05/h（基础 1 − 设施减免 0.1 − 中枢满员 0.25 − 自身「囤积者」0.25 − 黍「春雷响，万物长」0.1
> + 中枢回复 0.25，见 `--explain`）：8 小时后 23.6，之后还能工作 472h。

### 2) base 模式

```bash
python main.py --mode base --scenario-file scenarios/demo.json          # 当前布局可持续多久
python main.py --mode base --demo --period 12                           # 先推进 12h 再评估
```

返回 JSON（`--mode base --scenario-file scenarios/demo.json` 的真实输出，`operators` 共 16 人，此处节选）：

```json
{
  "mode": "base",
  "period_hours": 0,
  "layout_sustain_hours": 24,
  "bottleneck": "斥罪",
  "operators": [
    {"name": "玛恩纳",   "facility": "控制中枢", "mood": 24, "sustain_hours": 24, "mood_at_end": 7.2},
    {"name": "泡泡",     "facility": "制造站",   "mood": 24, "sustain_hours": 24, "mood_at_end": 22.8},
    {"name": "斥罪",     "facility": "办公室",   "mood": 24, "sustain_hours": 24, "mood_at_end": 0},
    {"name": "菲亚梅塔", "facility": "宿舍",     "mood": 24, "sustain_hours": 0,  "mood_at_end": 24}
  ]
}
```

> 说明：`layout_sustain_hours` 是整个布局还能维持的最长时长，`bottleneck` 是最先红脸的干员；
> **工作干员**的 `sustain_hours` 彼此一致、都等于 `layout_sustain_hours`；
> **宿舍干员**的 `sustain_hours`：若能在临界时间前恢复满心情则 = 恢复满所需时间，否则 = `layout_sustain_hours`
> （示例中菲亚梅塔心情已满，恢复满需 0 小时，故为 0）；
> `mood_at_end` 是到达 `layout_sustain_hours` 时该干员的心情（瓶颈约 0、宿舍已恢复满 24、其余为中间值）。

### 3) 场景 JSON 格式

```json
{
  "facilities": [
    {"type": "控制中枢", "level": 1, "operators": ["玛恩纳", "维什戴尔", "魔王", "令", "路人"]},
    {"type": "制造站",   "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
    {"type": "办公室",   "level": 3, "operators": ["斥罪"]}
  ]
}
```

顶层还可以可选地写 **`entry_events`**（进驻事件 = 进驻那一刻的换心情）：

```json
{"entry_events": {"enabled": true, "swap_with": "路人"}, "facilities": [ ... ]}
```

- `type` 支持中文名或英文枚举值（`control_center` / `manufacturing` / ...）。
- 干员既可用名字字符串（自动套用内置技能，默认 `elite=2` 满练），也可用
  `{"name": "x", "mood": 20.5, "skill_ids": [...], "trait": "岁", "elite": 2, "level": 1}` 对象。
  `elite`（精英化等级 0/1/2）与 `level`（干员等级）控制技能解锁：某些技能只有精英化后才可用，
  或精英化后才"提升"到目标效果（如 火神 工匠精神 α→β）。

### 4) 图形界面（`python -m ui`）

命令行是"算一次"，图形界面是"**看整周期**"——导入 MAA 排班，看板上每个位置显示干员名与
**实时心情**，拖时间滑块看一天里心情怎么走，指定干员看整周期曲线：

```bash
.venv/Scripts/python.exe -m ui          # 推荐（在仓库根目录执行）
```

也支持**直接运行文件**（IDE 里右键 Run 常用这种）：`python ui/__main__.py`、`python ui/app.py`
——两者都会先把仓库根放进 `sys.path`，因此不依赖当前工作目录。

| 能做什么 | 怎么操作 |
|---|---|
| 导入多班（12h / 6h / 6h 三个文件，或一个含多班的文件） | 「导入排班…」可多选；一个文件里的每个 plan 都算一个班次 |
| 自己设置周期 / 班数 / 每班时长 | 「班次设置…」（各班长之和必须等于周期，界面实时校验） |
| **一眼看到全部房间** | 看板用 21px 紧凑芯片：控制中枢横排一行，工作区（制造/贸易/发电）与辅助休息区（会客/办公/训练/加工/宿舍）分两列，**一屏放下，不用滚动** |
| **一眼看到全部干员** | 底部「全员一览」把整个周期出现过的干员（含只出现在别的班次的）全摆出来，带位置标记（`制1`/`宿3`/`中`），按颜色看谁危险 |
| 逐个位置设干员与心情 | 看板**左键**位置 → 选人/更换/清空；**右键**位置 → 设该干员心情（周期起点） |
| **进驻事件（换心情）** | 工具栏是一颗「**换心情设置**」按钮 + 右侧**当前状态**（`未开启` / `已开启 · 换塞雷娅 · 等她满` / `已开启 · 按班次`）；**开关只有设置框里这一处**（旧版工具栏那个复选框已删掉，避免同一个开关出现两次）。框里是**① 总开关 + ② 一张"每班一行"的表**：`班次 | 用 | 换谁 | 强制切换`（下方「全选 / 全不选」）——一行说尽这一班的行为，不再分"全局值 + 例外"两层；换谁自带范围（前一位进驻＝同宿舍、最累的 / 具体干员＝基建任意位置），强制切换勾＝她没满就**等她回满再换**、不勾＝判定时没满**就不换**。「对方心情是多少」不设开关（照换）；「位置也一起互换」收成固定口径（只换心情）。状态栏另有一句完整口径（如 `换心情：未开启（…）`），应用后整句复述当前配置。场景 JSON 顶层的 `entry_events` 会在导入时自动逐行同步到这张表 |
| **闲置入宿** | 工具栏「**闲置入宿设置**」+ 右侧状态（`未开启` / `已开启 · 6 人（自动）`）：把**没在上班、也不在宿舍、心情还没满**的干员安排进宿舍恢复——宿舍有空位就直接放进去（氛围高的优先），**没空位就与宿舍里心情已满的那位互换**（那位换出来闲置；他已是满心情，闲置不掉心情）。设置框＝**一个总开关 + 一张按时间排的逐次表**（第 1 周期第 1 班 → … → 第 N 周期最后一班，每组组头写时刻、自带全选/全不选；「去哪／与谁换」= **空位**（宿舍01、宿舍02…＝放进那间宿舍的空位，不动任何人）或 **满心情的人**（与他互换，他换出来闲置），只列**那一刻真的可选**的那些）。设置粒度是 **(周期, 班次, 干员)**，因为心情跨班跨周期连续、每次的候选都不一样；**改动实时生效**（防抖 250ms，表和主界面一起刷新，取消回滚）。默认**关闭**；曲线图上用绿色「宿」标记标出每次入宿 |
| **批量设置** | 工具栏「**批量设置…**」：把**当前布局的所有干员 + 心情**摊成一张可滚动表（房间 · 位次 · 干员 · 心情）一次改完。心情区：全部 24 / 全部 0 / 统一设为 X / **按当前时刻回填** / 恢复导入值；干员区：**批量粘贴名单**（按房间顺序填入）/ 清空本班次 / 显示空位。点干员名可搜索更换，切班次下拉可逐班改 |
| 时间滑动 → 各位置心情实时变化 | 底部滑块；两侧 `◀`/`▶` 与 `←/→` 键 = 15 分钟一档、`Home/End` 跳首尾、`空格` **只**播放/暂停（不会"按下"聚焦的工具栏按钮；工具栏按钮也不参与 Tab 焦点） |
| **播放**（看一天怎么走） | ▶ 播放 + **速度 1x / 60x / 600x / 3600x / 14400x**——单位是 **模拟秒/真实秒（s/s）**：`1x` ＝实时、`60x`＝1 分/秒、`3600x`＝1 小时/秒（24 秒放完一天）。工具栏显示当前档的等价说法（＝实时 / ＝1 小时/秒…），状态栏给出完整解释 |
| 对点：输入干员名 → 整周期心情曲线 | 右侧「对点查询」选人，或直接点「全员一览」里的芯片 → 曲线 + 关键数值（**此刻速率 / 本班平均速率**、最低/最高及时刻、红脸段数与时长、各班最低） |

技术底座是 tkinter（标准库），**没有引入任何第三方依赖**；界面规矩、计算口径与已知简化见
**`documents/10-图形界面.md`**。

## 五、Python API

所有数值均为 `decimal.Decimal`；"无限"用 `mood_soc.INF`（= `Decimal('Infinity')`）表示，转 JSON 时为 `null`。
入参建议传字符串或 `Decimal`；传 float 也会被 `to_decimal` 经字符串安全转换（不丢精度）。

### 5.1 顶层公开 API（`from mood_soc import ...`）

| 函数 / 常量 | 签名 | 返回 | 说明 |
|---|---|---|---|
| `build_base_layout` | `(data, validate=False)` | `BaseLayout` | 从场景 dict 构建布局（格式见上文「3) 场景 JSON 格式」）；`validate=True` 时做容量/房间数自检并抛 `ValueError` |
| `apply_idle_to_dorm` | `(world, enabled=None, idle=None, only=None, swap_with=None, scope=None)` | `list[Contribution]` | **闲置入宿**：把「没在上班、也不在宿舍、心情还没满」的干员安排进宿舍（空位优先，没空位就与宿舍里心情已满的那位互换）；`idle`＝本班未排班者的心情、`only`＝只处理这些人、`swap_with`＝指定交换对象、`scope`＝`(周期, 班次)`（逐次设置）；⚠️ **就地改布局**。见 `04-特殊机制.md` 第 30 条 |
| `apply_entry_events` | `(world, swap_with=None, enabled=None, scope=None, restore_back=None, when=None)` | `list[Contribution]` | **进驻事件**（M15a 换心情）：`swap_with` 换谁（人名 / `"any"` 自动挑最累的）、`scope` 范围（`dorm`/`anywhere`）、`restore_back` 换完是否把对方换回原位、`when` **她自己的心情门槛**（`immediate`/`wait`/`full`；「对方心情是多少」不构成限制）、`enabled` 强制开关；⚠️ **就地改** `world`。优先规则见上文 |
| `entry_event_holders` | `(world)` | `list[(干员名, 房间名)]` | 列出可能触发进驻事件的干员（如菲亚梅塔），供界面提示 |
| `find_entry_target` | `(world, holder, facility, swap_with=None, scope="dorm")` | `(Operator, 说明)` | 预览"会换谁"（界面用它做候选与提示） |
| `entry_target_kind` | `(swap_with, scope="dorm")` | `"named"/"auto"/"default"` | 口径判定（**行为与文案同源**，避免"实际自动挑、文案写前一位"） |
| `resolve_entry_config` | `(cfg, index, label="", overrides=None)` | `EntryEventConfig` | 合并"全局配置 + 某班次覆盖" → 该班的有效配置（按班次生效的关键） |
| `evaluate` | `(world, name, period_hours=0)` | `MoodResult` | single 模式：目标干员时段后的状态 |
| `evaluate_base` | `(world, period_hours=0)` | `BaseResult` | base 模式：全体干员 + 布局可维持时长 |
| `compute_net_rate` | `(world, name)` | `Decimal` | 某干员净速率（消耗 − 回复，>0 下降） |
| `remaining_mood_after` | `(world, name, hours)` | `Decimal` | 时段后剩余心情（钳位 [0,24]） |
| `remaining_work_hours` | `(world, name)` | `Decimal` | 从当前心情起算还能工作多久（net≤0 为 Infinity） |
| `time_to_mood` | `(world, name, target_mood)` | `Decimal` | 达到指定心情所需时间（工作=下降、宿舍=上升） |
| `work_rest_ratio` | `(consumption, recovery)` | `WorkRestRatio` | 工休比指标 |
| `simulate` | `(world, name, duration, step=0.05)` | `list[(时间,心情)]` | 时间步进模拟轨迹（深拷贝 world，处理红脸联动） |
| `ampere_hour_integration` | `(soc0, net_rate, dt, capacity=24)` | `Decimal` | 安时积分一步 |
| `MoodBattery` | `(capacity=24, soc=None)` | 类 | 心情电池封装（`integrate` / `time_to_empty` / `time_to_full` / `percent`） |
| `to_decimal` | `(value)` | `Decimal` | int/float/str/Decimal 安全转 Decimal（float 走字符串） |
| `parse_facility_type` | `(name)` | `FacilityType` | 中文名或枚举值 → `FacilityType`（失败返回 None） |
| `FacilityType` | — | 枚举 | 设施类型（见 §5.4） |
| `MOOD_MAX` | — | 常量 | 心情上限 `Decimal("24")` |
| `INF` | — | 常量 | 无限 `Decimal("Infinity")` |

### 5.2 结果数据模型（dataclass 字段）

**MoodResult**（single 模式结果）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `operator_name` | str | 目标干员名 |
| `facility_label` | str | 所在设施中文名 |
| `initial_mood` | Decimal | 初始心情 |
| `net_rate` | Decimal | 净速率（>0 下降 / <0 上升） |
| `remaining_mood` | Decimal | 目标时段结束后的心情 |
| `sustain_hours` | Decimal | 还能维持/恢复多久（工作=到红脸、宿舍=恢复满；不变为 Infinity） |
| `state` | str | 中文状态描述 |

**BaseResult**（base 模式结果）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `operators` | list[OperatorResult] | 每个干员的结果 |
| `layout_sustain_hours` | Decimal | 布局可维持时长（无人红脸；无人会红脸为 Infinity） |
| `bottleneck` | str / None | 最先红脸的干员名 |

**OperatorResult**（base 中单个干员）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | str | 干员名 |
| `facility_label` | str | 所在设施中文名 |
| `mood` | Decimal | 心情值（当前或时段推进后） |
| `sustain_hours` | Decimal | 工作=布局可维持时长；宿舍=min(恢复满时间, 布局可维持时长) |
| `mood_at_end` | Decimal / None | 到达 `layout_sustain_hours` 时的心情 |

**WorkRestRatio**：

| 字段 | 说明 |
|---|---|
| `consumption` / `recovery` | 工作消耗 x / 休息回复 y |
| `work_time_full` | 满心情可连续工作 24/x |
| `recover_time_full` | 从零回满 24/y |
| `ratio` | 最大工休比 y/x |
| `work_share` | 最大工作时长占比 y/(x+y) |
| `max_work_per_day` | 24 小时内最长可工作 24y/(x+y) |

**布局 / 干员 / 设施**：

- `Operator(name, mood=MOOD_MAX, skill_ids=[], trait=None, factions=None, elite=2, level=1)` —— 干员（elite/level 控制技能解锁）
- `Facility(ftype, level=1, operators=[], atmosphere=None, name="", deputies=[], slots=None, enabled=True)` —— 设施房间
- `BaseLayout(facilities=[])` —— 基建布局；方法：`get_facility`（只取第一个，deprecated）/ `control_center` /
  `facility_of` / `get_operator` / `all_operators` / `all_deputies` / `of_type` / `count_of_type` / `count_in` /
  `all_dormitories` / `facilities_in` / `operators_in` / `working_operators` / `base_operators` / `validate`

### 5.3 输出层（`from mood_soc.output import ...`）

| 函数 | 签名 | 说明 |
|---|---|---|
| `mood_result_to_dict` | `(r, period_hours)` | MoodResult → JSON dict（数字按 6 位小数舍入，Infinity→null） |
| `base_result_to_dict` | `(r, period_hours=None)` | BaseResult → JSON dict |
| `to_json_string` | `(data)` | dict → 可读 JSON 字符串（中文不转义） |
| `dump_json` | `(data, path)` | dict → 写 JSON 文件（自动建目录），返回绝对路径 |

### 5.4 设施类型（`FacilityType`）

| 枚举值 | 中文名 | | 枚举值 | 中文名 |
|---|---|---|---|---|
| `control_center` | 控制中枢 | | `office` | 办公室 |
| `manufacturing` | 制造站 | | `training` | 训练室 |
| `trading` | 贸易站 | | `workshop` | 加工站 |
| `power` | 发电站 | | `dormitory` | 宿舍 |
| `reception` | 会客室 | | | |

### 5.5 完整示例

```python
from decimal import Decimal
from mood_soc import (build_base_layout, evaluate, evaluate_base, simulate,
                      work_rest_ratio, compute_net_rate)
from mood_soc.output import mood_result_to_dict, base_result_to_dict, dump_json

# 1) 构建布局
world = build_base_layout({
    "facilities": [
        {"type": "控制中枢", "level": 1, "operators": ["路人1","路人2","路人3","路人4","路人5"]},
        {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
        {"type": "宿舍", "level": 5, "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
    ]
})

# 2) single 模式
r = evaluate(world, "泡泡", period_hours=Decimal("12"))
print(r.net_rate, r.remaining_mood, r.sustain_hours, r.state)
print(mood_result_to_dict(r, Decimal("12")))          # -> dict

# 3) base 模式
b = evaluate_base(world)
print(b.layout_sustain_hours, b.bottleneck)
for o in b.operators:
    print(o.name, o.mood, o.sustain_hours, o.mood_at_end)
print(base_result_to_dict(b))                          # -> dict

# 4) 底层查询
print(compute_net_rate(world, "泡泡"))
print(work_rest_ratio(Decimal("0.65"), Decimal("4")))

# 5) 时间步进模拟
traj = simulate(world, "泡泡", Decimal("12"), step=Decimal("0.1"))
print(traj[0], traj[-1])

# 6) 写文件
print(dump_json(base_result_to_dict(b), "results/out.json"))
```

---

## 六、测试（黑盒）

测试为**黑盒测试**：只通过「命令行」「公开 API」与「图形界面的计算核心」断言
**输入 → 输出**是否正确，不测试任何内部结构 / 内部函数。当前共 **202 个用例全绿**。

```bash
# 运行全部测试
.venv/Scripts/python.exe -m unittest discover -s tests -v

# 只跑某个文件
.venv/Scripts/python.exe -m unittest tests.test_api_blackbox -v          # 公开 API 黑盒
.venv/Scripts/python.exe -m unittest tests.test_cli_blackbox -v          # 命令行黑盒
.venv/Scripts/python.exe -m unittest tests.test_ui_schedule_blackbox -v  # 图形界面的计算核心
.venv/Scripts/python.exe -m unittest tests.test_ui_app_smoke -v          # 界面冒烟（无图形环境自动跳过）
```

覆盖的代表性输入 → 输出：

| 输入 | 预期输出 |
|---|---|
| 办公室单人、时段 0 | 心情 24，还能工作 24h |
| 泡泡（满中枢 + L3 制造）、时段 8 | 心情 20.8，还能工作 52h |
| 灵知 时段 16 / 20 | 心情 12→16h / 9→12h（基于剩余心情重算） |
| 给定干员 + 目标心情 | 返回所需时间（工作下降 / 宿舍上升方向正确） |
| base：满中枢+制造+宿舍 | 布局可维持 32h，瓶颈=中枢干员 |
| base：已有红脸干员 | 布局可维持 0h，瓶颈=该干员 |
| base：全员宿舍 | 布局可维持 null（无限） |
| 宿舍 深靛 + 蓝毒 | 蓝毒 单体回复 1.00；换成路人则 0.55 |
| 宿舍 寒檀 + 提丰（均为萨米） | 提丰 单体回复 1.00 |
| 宿舍 深靛 + 临光 + 蓝毒 | 1.00（0.55+0.45 先求和，再胜过使徒 0.50） |
| 命令行目标不存在 | 非零退出码 + stderr 错误提示 |
| 图形界面：导入 3 班排班 | 3 个班次 / 周期 24h；看板 50 个位置；轨迹节点 130 |
| 图形界面：时间滑到 12h | 看板心情随之变化（如锡人 24 → 15） |
| 图形界面：对点选人 | 曲线与该干员的关键数值同步切换 |

---
