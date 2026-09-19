# Rhodes-MoodSOC —— 干员"心情电池"建模工具

把《明日方舟》基建中的干员**心情**抽象为一块**电池**，基于 **BMS-SOC 安时积分法
（库仑计数 / Ampere-Hour Integration）** 完成数学建模，用于回答：

> 给定「当前干员信息 + 当前基建布局 + 目标时段」，
> 输出「目标时段后该干员的剩余心情」，以及「其余干员心情无限时，该干员还能工作多久」。

规则依据：
- 心情消耗/回复/工休的**计算规则**：`resources/心情消耗回复和工休时间.docx`；
- **真实技能库与干员↔技能映射**：`data/moods_skills.txt` + `data/operators.txt`
  （含精英化解锁等级、value 千分值、作用 family），由 `scripts/generate_skills_data.py`
  一键生成 `data/skills_data.py`。

> 技能数值以两份 txt 为**权威来源**（docx 中的技能示例值已过时）。
> 两份 txt 的**上游**是 `Kengxxiao/ArknightsGameData`（`zh_CN/gamedata/excel/building_data.json`）；
> 结构分析、逐条对照结果、以及「是否属心情类」的待判定清单见
> `documents/08-上游数据源分析.md`（该文档只是**数据源分析**，不参与计算）。
>
> **技能分类**：每条技能都挂在一个**六轴模板**（`M01`~`M17` 心情类 / `X01`~`X11` 非心情）上，
> 新增干员技能 = 认模板 + 填参数；上游全部 buff 都有覆盖台账，未归类会**硬报错**。
> 见 `documents/05-技能分类大纲.md`（分类大纲 + 模板字典）与 `data/skills_registry.txt`（台账）。

### 本项目为谁服务（定位）

**ArknightsInfraCalc-v3（Rust）负责算出"高效率的基建布局"；本项目负责"匹配那套布局"，
生成它的完整心情变化周期** —— 这套布局下每一刻谁在哪、心情多少、能撑多久、会不会红脸。

图形界面只负责让人**直观地看**；真正的交付面是**程序接口**：v3 把求解结果**内联**喂进来
（`load_json`），配好「换干员 / 闲置入宿 / 周期」三组口径，然后只要问两句：

- 「**某人 + 时间节点** → 她的心情」 —— `operator_detail`；
- 「**时间节点** → 整座基地的布局 + 全员心情」 —— `layout_at`。

默认口径（已定）：**换干员默认关**（必须手动配置）、**闲置入宿默认按本项目四级优先级**、
**周期默认取文件里的各班时长**。给 v3 侧照抄的一页纸见 `documents/12-v3接入.md`。

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

产出端逐条注明上游出处：`data/variable_producers.txt`；实现见 `mood_soc/variables.py`。
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
——已从技能库撤出，`data/skills_registry.txt` 保留登记与原因。

> 与需求文档第 4 段「干员工作时…每小时基础消耗速率 1 点」**不冲突**：
> 那说的是常规生产设施「上岗生产」的稳态消耗，挂件位不属于上岗生产。

加工站另有原因：上游 35 条加工站心情 buff **全部**说的是「**配方**心情消耗」
（「心情消耗为 4 的配方全部 -1」「相应配方的心情消耗恒定为 2」「全部除以 4」…），
即心情是**按次**扣的；而**配方自身的心情消耗在上游数据 dump 里没有字段**，
所以"一次加工扣多少心情"也不建模（棘刺「爆炸艺术」同理）。

### 「不在基建」的干员（既不在工作设施、也不在宿舍）

多班排班里总有人**这一刻没上班、也不在宿舍**（换下来歇着、或压根没排班）。他们要能设心情、
要能在曲线里看到，但**不能**因此被算成"在岗"：

| | 口径 |
|---|---|
| 数值 | **这一刻**净速率 0（不消耗也不回复）⇒ 心情不变；轨迹里是**一条平线**（等于起点心情） |
| 她在别的班有活？ | **那些班照常算**（"本班未排班" ≠ "整份排班都不在基建"） |
| 技能计数 | **不参与** —— 她不在任何 `facilities` 里，「基建内每有 1 名 XX 干员」数不到她 |
| 名单 ↔ 位置 | **不变式**：写进名单的人会被**自动从所有班次的位置上摘掉**（不会一边在名单、一边占位） |
| 哪来的人 | ① 面板自动列出「本班未排班」的人；② 点「**＋ 添加干员…**」手动加（可从全量名册/干员池挑） |
| 存哪 | 排班 JSON 顶层 `"detached": ["某人", ...]`（与 `entry_events` / `idle_to_dorm` 同层；老文件没这个键 = 空） |

```jsonc
// 场景 JSON：顶层加一个 detached 就够
{"detached": ["板凳甲", "板凳乙"],
 "facilities": [ {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]} ]}
```

程序接口：`set_detached`（改名单）/ `bench_names`（问名单）；`moods` 与 `trajectory` 里
他们照样出现（一条平线）。详见 `documents/11-程序接口.md` §4。

### 开发约定

- **每次改动即时提交**：每完成一次修改就 `git commit` 一次，提交信息用 **1–15 个字**简要描述
  （如「修复替换链」「补变量账本」）。一次提交只做一件事。
- **改完同步文档**：至少更新本文件与 `AGENTS.md`，以及 `documents/` 下受影响的那一篇。
- **目录分工**：**数据分两条线** —— `data/` 放**要 import 的表**（`*.txt` 人工表 + `*.py` 生成物），
  `resources/` 放**要读文件的项目级数据**（样例 JSON / 数据字典 / 核对报告 / 需求文档 docx）；
  `documents/` 放**文档**（按门类分文件，
  索引见 `documents/README.md`）；根 `AGENTS.md` 是给 AI 的**精简入口**，保持精简
  （64KB 指令预算，超了会被截断），细节一律写进 `documents/`。

### 技能分类与阵营表

- **分类**：每个技能 clause 挂在六轴模板（`M01`~`M17` 心情类 / `X01`~`X11` 非心情）上，
  新增干员技能 = 认模板 + 填参数；上游全部 755 条 buff 都有覆盖台账，未归类**硬报错**。
  见 `documents/05-技能分类大纲.md`、`data/skills_registry.txt`。
- **阵营/标签**：`data/factions.txt` 由 `scripts/generate_factions.py` 从上游
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
**不要凭印象写、不要猜、不要从二手资料誊抄。** 本项目的 `data/*.txt` 与
`data/skills_data.py` 都只是**上游的派生物**；两者不一致时**以上游为准**。

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
> **6 位小数舍入**（`store/serialize.py`），并把无限输出为 `null`。

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
     ± 同设施设施级技能  （黍 -0.1、巫恋"低语"+0.25，作用于同设施全体含自身）
     ± 同设施其他干员技能（当前无使用者：低语已改判为"含自身"）
     - 中枢减免技能  （预留：维什戴尔/重岳等真实数据已归为"回复"，见下）
```

特殊规则：
- **消除类**（槐琥 / 令）：移除目标干员"自身技能"的正负影响，但**不**影响中枢减免、
  **不**影响设施级技能；令额外限定 `trait="岁"`。
- **红脸**（心情 ≤ 0）：该干员所有技能失效（仍可继续工作，只是效率下降）。
- **精英化判断**：技能是否生效 = 干员 `elite >= 技能解锁 elite` 且 `level >= 解锁 level`；
  "精英化提升"版技能（如 火神 β）解锁后**替换**低版本（α），不叠加。
  **缺省口径是满练**（`elite=2`、`level=30`）；要算没练满的干员，场景里写
  `{"name": "卡夫卡", "elite": 1}`，用 `mood_skill_summary(op)` 可以问出"他少算了哪几条"。详见
  `documents/03-技能系统.md` §5.3。

### 心情回复

```
宿舍回复 = 白字(1.5 + 0.1×等级) + 绿字(0.0004×实际氛围 + 技能加成)
        + 中枢干员对宿舍的回复（领袖/战纹/巡心/羁绊相生等）
        + 自身回复 + 群体回复 + 单体回复 + 定向回复
工作设施回复 = 中枢技能提供的回复（玛恩纳中枢+0.05/发电办公会客+0.1、维什戴尔工作设施+0.1、
              重岳+0.05、冰酿中枢+0.05）
中枢内回复 = 中枢干员对中枢内全体（左膀右臂/S.W.E.E.P./笑靥如春… 各 +0.05、
              德才兼备等按阵营人数 ×0.05、夕"不以物喜"+0.05）
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
> 人工补充只有上游不列名单的「异格者」（`data/factions_supplement.txt`）。
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
├── data/                  ★ **数据（要 import 的表）**：干员与技能数据
│   ├── paths.py           全部数据路径的**唯一出口**（别处不许手拼路径）
│   ├── domain.py          领域基元：FacilityType / 容量表 / 心情上下限 / 默认练度
│   ├── skill_model.py     技能数据模型：Skill / SkillEquip / SkillKind
│   ├── conditions.py      技能条件函数（`_cond_*`）与阵营查询（`_factions_of`）
│   ├── skills_data.py     **生成物**：SKILLS / DEFAULT_OPERATORS / SKILL_EQUIPS / 阵营 / 变量产出者
│   ├── operator_names.py  **生成物**：别名 → 中文名（英文名 / char_id）
│   └── *.txt              人工维护或上游生成的 CSV（技能库 / 台账 / 阵营 / 产出者）
├── mood_soc/              **纯计算**包（库，可被 import）
│   ├── __init__.py        对外公共 API 汇总（导出下面各模块的公开符号）
│   ├── config.py          计算规则常量与公式（基础消耗 / 减免 / 宿舍回复 / 设施集合 / 建造位）
│   ├── battery.py         纯数学层：安时积分法 + MoodBattery + to_decimal/INF（与游戏规则无关）
│   ├── models.py          数据模型层：Operator / Facility（多房间·容量·副手·活动室）
│   │                      / BaseLayout（按类型聚合）/ MoodResult（含 ledger）/ OperatorResult / BaseResult
│   ├── skills.py          **兼容门面**：re-export data 的技能模型 / 条件函数 / 数据表
│   ├── skill_templates.py 分类字典：六轴枚举（ModelTier/Domain/Target/Effect/ValueShape/Stacking）+ 模板注册表
│   ├── ledger.py          ★记录层：Contribution / MoodLedger（逐条贡献流水账）+ 轴 F 统一合成
│   ├── variables.py       ★变量账本：26 种"中间货币"（人间烟火/热情值/无声共鸣…）+ 产出者收集
│   ├── rules.py           业务逻辑层：**流水账驱动**——把"布局 + 干员"折算成消耗/回复/净速率 + 各项查询
│   ├── simulator.py       时间步进模拟器：每步重算速率，处理"红脸 → 技能失效"等时变情况
│   ├── report.py          展示层：中文结果格式化（文本）
│   └── scenario.py / importer.py / maa.py / output.py   **兼容转发壳** → store/ 对应模块
├── store/                 ★ **数据管理层**（状态与 IO）
│   ├── session.py         ★ **会话状态**：全部可调项 + 唯一的重算入口（界面与程序接口共用）
│   ├── schedule.py        多班排班模型 + 整周期心情轨迹（事件驱动精确积分；原 ui/schedule.py）
│   ├── sources.py         4 种排班/蓝图 JSON 自动识别与转换（+ 干员池 + 变量初始值 + 导入报告）
│   ├── layout.py          字典/JSON -> BaseLayout
│   ├── maa.py             MAA 排班 JSON -> facilities（唯一的房间映射表）
│   └── serialize.py       结果 -> JSON dict / 写入文件（inf -> null）
├── api/                   ★ **程序接口**（JSON 进 / JSON 出；见 documents/11-程序接口.md）
│   ├── ops.py             能力表：36 个 op（图形界面能做的一切 + 给 v3 的适配面）
│   ├── protocol.py        线协议：请求/响应的解析与组装（NDJSON）
│   ├── server.py          常驻服务 `python -m api.server`（推荐给 Rust）
│   └── cli.py             一次性调用 `python -m api.cli --op …`
├── ui/                    ★ **图形界面**（tkinter，纯标准库；见 documents/10-图形界面.md）
│   ├── app.py             主窗口（工具栏 + 看板 + 全员一览 + 曲线 + 时间滑块 + 状态栏）；
│   │                      业务状态全在 store.session.Session 上，这里只有别名与绘制
│   ├── schedule.py        **兼容转发** → store/schedule.py（引擎已搬走）
│   ├── theme.py           配色/字体/间距令牌 + 颜色混合 + 显示格式化（唯一的显示舍入处）
│   ├── widgets.py         心情芯片（21px 紧凑呈现单元：色条 + 位置标记 + 名字 + 心情值）
│   ├── board.py           基建看板：控制中枢整行 + 工作区/休息区两列（一屏放下全部房间）
│   ├── roster.py          「全员一览」条：整个周期的全部干员，一屏摆开、不滚动
│   ├── chart.py           心情曲线（Canvas 手绘：坐标轴 / 网格 / 班次分界 / 悬停读数）
│   ├── dialogs.py         选人 / 设心情 / 房间等级对话框 + 设置内容本体（时间轴 / 换心情 / 闲置入宿）+ 心情输入校验
│   ├── batch.py           「干员与心情」内容本体：房间等级 + 干员 + 练度 + 按时刻指定心情（锚点）+ 共享 grid 等宽表
│   ├── settings.py        「设置」中心：左侧导航 + 固定内容区 + 页面缓存（切页不重建、窗口不跳）
│   ├── scroll.py          可滚动表的统一做法（Canvas + 滚动条 + 滚轮 + 键盘；尺寸缓存、滚动中状态）
│   └── __main__.py        `python -m ui` 入口
├── tests/                 黑盒测试（只断言"输入 → 输出"，不测内部结构）
│   ├── __init__.py
│   ├── test_api_blackbox.py        公开 API 黑盒：场景 JSON + 目标/时段 → 结果 JSON
│   ├── test_cli_blackbox.py        命令行黑盒：subprocess 调 main.py → stdout JSON / 退出码 / 结果文件
│   ├── test_api_ops.py             ★程序接口黑盒：握手 / 全流程 / **与引擎逐位同源** / 协议形状 / 时刻写法 / **求解器适配**
│   ├── test_layers.py              ★分层回归：依赖方向 / 兼容转发壳不漏名字 / 数据路径只有一处
│   ├── test_ui_schedule_blackbox.py 图形界面的计算核心黑盒（含"不拉起 tkinter"的结构断言）
│   ├── test_ui_app_smoke.py        界面端到端冒烟（真建窗口；无图形环境自动跳过）
│   ├── test_ui_batch_blackbox.py   「干员与心情」面板黑盒（真建窗口；无图形环境自动跳过）
│   ├── test_ui_settings_blackbox.py 「设置」中心黑盒（统一大小 / 切页不重建 / 失效重建 / 干员池）
│   ├── test_import_blackbox.py     导入黑盒（4 种 JSON 格式识别 + 转换 + 兜底推断 + 报告）
│   └── test_skill_coverage.py      技能全量核对三层断言（模板级 / 250 条 clause / 上游描述对照）
├── scripts/
│   ├── maa_to_scenario.py      把 MAA 排班 JSON 转成本工具的场景 JSON（解析在 store/maa.py）
│   ├── classify_skills.py      给每个 clause 挂六轴模板 + 生成 755 行覆盖台账 + 零遗漏校验
│   ├── verify_skills.py        **技能全量核对**（三层）+ 生成核对报告（只读，不改数据）
│   ├── generate_factions.py    从上游 termDescriptionDict 生成干员↔阵营/标签表
│   ├── generate_operator_names.py 从上游 character_table 生成 data/operator_names.py（别名表）
│   └── generate_skills_data.py 把 data/ 两份 txt 生成为 data/skills_data.py（技能数据管道）
├── scenarios/             demo.json + maa_shift1/2/3.json（示例场景）
├── resources/             ★ **数据（要读文件的）**：仓库根的项目级数据
│   ├── 心情消耗回复和工休时间.docx              需求文档（规则来源）
│   ├── arknights-infra-schedule-maa.json        MAA 排班样例（界面冷启动自载）
│   ├── import_v3_out_*.json / import_v4_input.json   4 种导入格式的样例
│   ├── 输出JSON结构说明.md / plan_compute_example_v4_annotated.md   数据字典与契约
│   └── skill_verify_report.md                   技能核对报告（生成物，勿手改）
├── documents/             ★ **文档**（按门类分文件，索引见 documents/README.md）
│   ├── README.md            文档索引 + §编号约定 + 维护约定
│   └── 01-架构.md … 12-v3接入.md
├── results/               运行生成的结果 JSON（已被 gitignore）
├── README.md              面向人类的完整说明（人类入口）
├── AGENTS.md              给 AI 的精简入口（DSH 只从项目根自动加载它）
├── requirements.txt       仅标准库，无第三方依赖（Python 3.9+）
└── .venv/                 Python 3.14 虚拟环境（uv 创建）
```

依赖关系**严格单向向下**，无环、无横向耦合（`tests/test_layers.py` 会静态扫描源码盯着）：

```
ui/（tkinter 视图）      api/（程序接口，JSON 进 / JSON 出）     ← 两者互不 import
        └──────────────────┬──────────────────┘
                    store/（状态与 IO：排班 / 轨迹 / 解析 / 序列化 / 会话）
                           │
                    mood_soc/（纯计算：给定布局，谁的心情怎么变）
                           │
                       data/（数据 + 领域基元 + 路径出口；只依赖标准库）
```

三条硬规矩：① `data`/`mood_soc` 不许 import `store`/`ui`/`api`；② `ui` 与 `api` 互不 import；
③ `api`/`store`/`data` 的代码里不许出现 `tkinter`（程序接口要能在无图形环境跑）。
`store/schedule.py`（计算核心）同样不得 import tkinter。
`mood_soc/__init__.py` 里 `build_base_layout` 从 `store.layout` 取，是唯一一处向上引用
（为了 `from mood_soc import build_base_layout` 这个历史公开 API 不破）。

> **为什么数据单独成包**：改数据的人不该翻计算代码。`data/paths.py` 是资源路径的唯一出口
> （老代码里 7 处手拼 `resources/…` 已全部收敛），`data/domain.py` 放"游戏给的事实"
> （设施枚举 / 容量表 / 心情上下限），顺便断开了数据包与计算包之间的循环导入。

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
- 干员既可用名字字符串（自动套用内置技能，**默认 `elite=2` 满练、`level=30`**），也可用
  `{"name": "x", "mood": 20.5, "skill_ids": [...], "trait": "岁", "elite": 2, "level": 30}` 对象。
  `elite`（精英化等级 0/1/2）与 `level`（干员等级）控制技能解锁：某些技能只有精英化后才可用，
  或精英化后才"提升"到目标效果（如 火神 工匠精神 α→β）；`mood_skill_summary(op)` 会告诉你
  "按这个练度，他少算了哪几条心情技能"。

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
| **导入排班（4 种 JSON 自动识别）** | 「导入排班…」可多选；每个文件**自己认格式**：本工具场景 / MAA 排班 / **v3 求解输出**（`result.rotation.shifts[]` → 每班时长 + "谁在哪间房" + 练度）/ **v4 蓝图+干员池**（房间 + 干员池，**房间里没有人**）。一个文件含多班就全导进来；导入后状态栏给一句摘要（读到什么、忽略了什么、**哪些是推断的**）。识别与转换只有一处：`store/sources.py`；字段对照见 `documents/10-图形界面.md` §2.1 |
| **干员池（导入 v4 蓝图才有）** | 那类文件只有"蓝图 + 我有谁"，导入后池会显示在「干员与心情」里：池里的人**能搜到**、**练度按池里的值**（E1 就写 E1），并能一键**「从池中依次填入」**按顺序铺满当前班次 |
| **所有设置都在一个窗口里** | 工具栏＝**3 组**（组间有分隔线）：`导入排班… 设置… 周期数▾ │ 状态摘要 │ ▶播放 速度 回到起点`。「**设置…**」是唯一设置入口，左边导航选分区：**时间轴**（周期 / 班数 / 每班时长 / 周期数 / **初始时间点**）、**干员与心情**、**换心情**、**闲置入宿**。**改完立即生效**，关掉窗口即接受（不再有"点开→改→应用"三步）；中间那行状态摘要（点它也能开设置）随时告诉你两个开关的状态 |
| 自己设置周期 / 班数 / 每班时长 / 周期数 | 「设置…」→ 时间轴：各班长之和必须等于周期，界面实时校验（不合法就不落地）；周期数 1~3 用来判断这套排班能不能永动。**周期数在工具栏上也有一个下拉**——两处共用同一个变量，哪边改另一边立刻跟着变 |
| **周期从几点开始（初始时间点）** | 「设置…」→ 时间轴 → **初始时间点**：填 `01:00` 就是"1 点到第二天 1 点为一个周期"。**纯显示口径**——滑块读数、班次按钮的时段、曲线横轴、逐次表组头全按它渲染，**引擎数值一字不变** |
| **两层"世界"：界面显示以引擎为准** | 引擎跑模拟时给**每个班次深拷贝一份布局**，进驻事件的"位置也互换"与**闲置入宿的换人只改这份副本**（排班快照＝"你导入的排班"，导出时原样写回，**不改**）。所以同一个干员会有两份"她在哪"，换过人之后必然不一致：**看板 / 全员一览的位置标记 / 对点查询的所在设施 / 「干员与心情」的位置列**一律读**引擎那份**（`Trajectory.world_at(t)`）；「干员与心情」里"两份不一致"的行会打 `⇄` 并在「说明」列写明`本班被闲置入宿换出（快照写 宿舍#1）`。⚠️ 落在班次边界上取**右侧**（那一刻开始生效的班次），与 `mood_at`/`rate_at` 同口径 |
| **指定周期内任意时刻的心情（锚点）** | 「设置…」→ 干员与心情：顶上选**周期 + 时刻**（`◀/▶` 15 分钟、可「跟随滑块」），**表格里的心情列就是那一刻的实际心情**（换时刻即按轨迹重算）；手改某一格＝在那一刻给她指定这个值（**心情指定事件**，只对指定周期生效，之后照常演化）。**干员列也跟着时刻走**（时刻落在哪一班就显示那一班的人）。锚点一览写在心情区下方，可「清空本时刻」或用「恢复导入值」全清 |
| **底部班次条只用来切班次** | 按钮写「序号 + 时段」（`1. 00:00 – 12:00`）；班次名与时长（`Shift 1 · 12h`）只在**看板头部**出现一次，不在上下两处重复显示 |
| **一眼看到全部房间** | 看板用 21px 紧凑芯片：控制中枢横排一行，工作区（制造/贸易/发电）与辅助休息区（会客/办公/训练/加工/宿舍）分两列，**一屏放下，不用滚动** |
| **一眼看到全部干员** | 底部「全员一览」把整个周期出现过的干员（含只出现在别的班次的）全摆出来，带位置标记（`制1`/`宿3`/`中`），按颜色看谁危险 |
| 逐个位置设干员与心情 | 看板**左键**位置 → 选人/更换/清空；**右键**位置 → 设该干员心情（周期起点） |
| **改房间等级（容量随之变）** | 点看板**房间卡头** → 选 Lv（括号里写「可放 N 人」）；不足时卡头 `人数/容量` 变红。设置 → 干员与心情 里也有「房间等级」区 |
| **布局规模约束** | 制造/贸易/发电**共用 9 个建造位**（上游 `layouts.v0.slots` 的 OUTPUT 槽位），单类型上限 制造/贸易 5、发电 3、宿舍 4；导入 MAA 后**按实际人数自动推断最低等级**（中枢 5 人 → Lv5），自检问题写在状态栏 |
| **进驻事件（换心情）** | 「设置…」→ **换心情**：**① 总开关 + ② 一张"每班一行"的表**：`班次 | 用 | 换谁 | 强制切换`（下方「全选 / 全不选」）——一行说尽这一班的行为，不再分"全局值 + 例外"两层；换谁自带范围（前一位进驻＝同宿舍、最累的 / 具体干员＝基建任意位置），强制切换勾＝她没满就**等她回满再换**、不勾＝判定时没满**就不换**。「对方心情是多少」不设开关（照换）；「位置也一起互换」收成固定口径（只换心情）。**改动立即生效**，状态栏那句始终复述当前口径。场景 JSON 顶层的 `entry_events` 会在导入时自动逐行同步到这张表 |
| **闲置入宿** | 「设置…」→ **闲置入宿**：把**没在上班、也不在宿舍、心情还没满**的干员安排进宿舍恢复。**四级优先级**：**① 任一间宿舍还有未占满的位次 → 直接住进去（有空位就不换人）**；② 宿舍全满 → 换「宿舍 **#4→#3→#2** 的**第 2~5 位**」里实时满心情的那位；③ 还找不到 → 只在**白板**（不属于任何已记录阵营的干员）里挑实时满心情的，**满 24 的菲亚梅塔也在此列**（她「患难之交」只要满 24、不靠待在宿舍）；④ 自动都挑不到 → **你点名了就与那位互换**（**主动换**：不限心情、不受"只换白板 / 自回型不换出"限制），没点名才不动。**同优先级内「优先 4 最后 1」**（宿舍 `#4→#3→#2→#1`，同宿舍内 第 2→5 位→第 1 位）。**除菲亚梅塔外**，有阵营的人与"宿舍里能自回"的人都不会被自动换出去；被换出的那位**既不工作也不在宿舍**（心情平线），进来的人**接替他那个位次**。**先换心情、再判闲置入宿**（判定用换完那一刻的实时心情）。一个总开关 + **一张按时间排的逐次表**（第 1 周期第 1 班 → … → 第 N 周期最后一班，每组组头写时刻、自带全选/全不选）；表的主要职责是设置**第④级的兜底**——「换谁」= 点名与谁互换（候选是**那一刻宿舍里的所有人**，标签带心情，如 `巫恋 23.4`），「去哪」= **空位**（宿舍01、宿舍02…＝放进那间宿舍的空位，不动任何人）。设置粒度是 **(周期, 班次, 干员)**，因为心情跨班跨周期连续、每次的候选都不一样；**改动立即生效**（防抖 250ms，表和主界面一起刷新）。默认**关闭**；曲线图上用绿色「宿」标记标出每次入宿（⚠️ 谁被换出只有这个标记与对点查询的 `⚠ 不在基建` 能看出来） |
| **干员与心情（原「批量设置」）** | 「设置…」→ **干员与心情**：把**当前布局的所有干员 + 练度 + 该时刻的心情**摊成一张可滚动表（房间 · 位次 · 干员 · **练度** · **心情**）一次改完。**时刻区**：周期 1~3 + `HH:MM`（`◀/▶` 15 分钟、**「跟随滑块」**、「清空本时刻」）——**心情列就是这一刻的实际心情**，换时刻即按轨迹重算；手改某一格＝在那一刻给她**指定这个值**（锚点，只对该周期生效，之后照常演化）。**勾上「跟随滑块」就一直跟着主界面滑块**（切页/重建都不丢；跟随时时刻控件只读，取消勾选后才能手动指定）。心情区：全部 24 / 全部 0 / 统一设为 X / **按这一刻回填** / **恢复导入值**（＝原来的工具栏「重置心情」，会连锚点一起清）；干员区（跟着时刻所在班次走）：**批量粘贴名单**（按房间顺序填入）/ **从池中依次填入** / 清空本班次 / 显示空位；**练度区：逐人下拉（E0/E1/E2）＋「全部设为 E2」**。点干员名可搜索更换。**改动立即生效**（心情输入防抖 **500ms**：一次落地＝一整轮重算，实测 0.2s（周期数 1）~0.7s（周期数 3）；**程序自己的刷新不算改动、相同结果不重复通知**，所以面板不会把自己算起来）。**表格**：固定表头（房间·位次·干员·练度·心情·说明）+ 每间房一张**分组卡片** + **所有单元格共享一个 grid**（所以**列宽只有一个来源**、跨行严格对齐：房间 96 / 位次 40 / 练度 56 / 心情 72 像素固定，干员与说明列拉伸并带 `wraplength`）+ 悬停高亮（**滚动期间关掉**，滚完 120ms 恢复）+ 「不在基建」那一段淡绿底；上方**搜索框**输入即过滤（匹配名字/房间，一键动作只作用于看得见的行）。**滚动**：滚轮 3 行/次、`Shift+滚轮` 整页、`Ctrl+滚轮` 10 行、`PgUp/PgDn/Home/End` 翻页与首尾；尺寸没变就不重设滚动区间（实测 0.002ms/次） |
| **设置窗口本身** | 左边导航四个分区，右边**内容区尺寸固定**（切页时窗口不跳）、分区**只建一次**（切页只是 `tkraise`，实测 <1ms + 约 16ms 重绘，不再每次重建 ~150ms）；**房间等级区是网格**（每行 5 间，不再被裁）；两张表都能**用滚轮**滚（悬停在表格、表里任意文字、或滚动条上都行）；表格高度**自动吃剩余空间并自校正**（房间极多时宁可表格矮一点，也不让整页超出内容区）；改动即生效、底部只有「关闭」 |
| **练度（精英化）看得见** | 缺省＝满练；没练满的干员在**看板 / 全员一览的芯片名字后带 `E1` 角标**（只有非 E2 才有），右侧对点查询多一行 `练度 E1（等级 30）· 已解锁 N 条心情技能；⚠ 因未满练少 M 条：「手工艺品·β」（E2）`。写回场景时只有非 E2 才记成 `{"name": …, "elite": …}` |
| 时间滑动 → 各位置心情实时变化 | 底部滑块；两侧 `◀`/`▶` 与 `←/→` 键 = 15 分钟一档、`Home/End` 跳首尾、`空格` **只**播放/暂停（不会"按下"聚焦的工具栏按钮；工具栏按钮也不参与 Tab 焦点） |
| **播放**（看一天怎么走） | ▶ 播放 + **速度 1x / 60x / 600x / 3600x / 14400x**——单位是 **模拟秒/真实秒（s/s）**：`1x` ＝实时、`60x`＝1 分/秒、`3600x`＝1 小时/秒（24 秒放完一天）。工具栏显示当前档的等价说法（＝实时 / ＝1 小时/秒…），状态栏给出完整解释 |
| 对点：输入干员名 → 整周期心情曲线 | 右侧「对点查询」选人，或直接点「全员一览」里的芯片 → 曲线 + 关键数值（**此刻速率 / 本班平均速率**（随滑块/播放**实时更新**）、最低/最高及时刻、红脸段数与时长、各班最低）；鼠标悬停的读数里也带该点速率 |

技术底座是 tkinter（标准库），**没有引入任何第三方依赖**；界面规矩、计算口径与已知简化见
**`documents/10-图形界面.md`**。

### 5) 程序接口（给别的程序 / Rust 调用）

图形界面能做的事，也能用 **JSON 进 / JSON 出**做完——同一个 `store.session.Session`，
所以两条路算出来的数值**逐位一致**（有 `tests/test_api_ops.py::Test数值同源` 盯着）。

```bash
# 常驻（推荐：省掉每次 0.3~0.5s 的数据表 import）
.venv/Scripts/python.exe -m api.server

# 一次性（脚本 / 调试）
.venv/Scripts/python.exe -m api.cli --op capabilities
.venv/Scripts/python.exe -m api.cli \
  --op load_schedule --args '{"facilities":[{"type":"制造站","level":3,"operators":["泡泡","黍","路人甲"]}]}' \
  --then '{"op":"moods","args":{"at":[0,8,"24:00"],"include_trajectory":true}}'
```

```jsonc
// 常驻模式下：一行一个请求，一行一个响应（NDJSON over stdio）
{"id":1,"op":"capabilities"}
{"id":2,"op":"load_json","args":{"data": /* v3 求解结果，原样塞进来（不落临时文件） */ }}
{"id":3,"op":"set_entry_events","args":{"enabled":false}}          // 换干员默认就是关
{"id":4,"op":"layout_at","args":{"at":8}}                          // 整座基地的布局 + 全员心情
{"id":5,"op":"operator_detail","args":{"name":"但书","at":8}}       // 某人此刻的心情
{"id":6,"op":"closure","args":{"cycles":3}}                        // 这套布局能不能长期跑
{"id":7,"op":"quit"}
```

共 **36 个 op**：载入（`load_schedule` / **`load_json`** / `load_file` / `load_files`）、改设置（时间轴 / 槽位 /
房间等级 / 心情 / 锚点 / 练度 / 干员池 / 换心情 / 闲置入宿 / **不在基建名单**）、出结果（`moods` / `trajectory` /
**`layout_at`** / **`closure`** / `mood_ledger` / `time_to_mood` / `bottleneck` / `export_schedule`）、
只读（`describe` / `list_shifts` / `validate` / `get_settings` / `operator_detail` …）。
**先调一次 `capabilities` 握手**（拿协议版本与 op 表，当前 `protocol = 3`），版本不一致就报错、别继续。

**给 v3（Rust 求解器）的适配面**：`load_json` 把求解结果**内联**喂进来（自动识别格式，
含 v3 的 `{"result": …}` 信封），`layout_at` 回答"某时刻整座基地的布局 + 心情"，
`closure` 回答"这套高效布局能不能长期跑"。默认口径：**换干员关**（不继承文件里的 `Fiammetta.enable`）、
**闲置入宿开**（本项目默认四级优先级）、**周期取文件里的各班时长**。

时刻支持两种写法：`8.5`（**绝对**小时，跨周期递增）或 `"08:30"`（**周期内**时刻，`"24:00"` = 周期末）。
完整字段表、错误形状、Rust 接入样例、加 op 的步骤见 **`documents/11-程序接口.md`**；
v3 侧照抄即可的一页纸见 **`documents/12-v3接入.md`**。

## 五、Python API

所有数值均为 `decimal.Decimal`；"无限"用 `mood_soc.INF`（= `Decimal('Infinity')`）表示，转 JSON 时为 `null`。
入参建议传字符串或 `Decimal`；传 float 也会被 `to_decimal` 经字符串安全转换（不丢精度）。

### 5.1 顶层公开 API（`from mood_soc import ...`）

| 函数 / 常量 | 签名 | 返回 | 说明 |
|---|---|---|---|
| `build_base_layout` | `(data, validate=False)` | `BaseLayout` | 从场景 dict 构建布局（格式见上文「3) 场景 JSON 格式」）；`validate=True` 时做容量/房间数自检并抛 `ValueError` |
| `apply_idle_to_dorm` | `(world, enabled=None, idle=None, only=None, swap_with=None, scope=None)` | `list[Contribution]` | **闲置入宿**：把「没在上班、也不在宿舍、心情还没满」的干员安排进宿舍（**四级优先级**：有空位直接住 → 换「宿舍#4→#3→#2 的第 2~5 位」的实时满心情者 → **白板兜底（满 24 的菲亚梅塔也放行）** → 自动挑不到时用**点名**的那位（**主动换，不限心情**））；`idle`＝本班未排班者的心情、`only`＝只处理这些人、`swap_with`＝点名交换对象（**只在④生效**）、`scope`＝`(周期, 班次)`（逐次设置）；⚠️ **就地改布局**。见 `04-特殊机制.md` 第 30 条 |
| `apply_entry_events` | `(world, swap_with=None, enabled=None, scope=None, restore_back=None, when=None)` | `list[Contribution]` | **进驻事件**（M15a 换心情）：`swap_with` 换谁（人名 / `"any"` 自动挑最累的）、`scope` 范围（`dorm`/`anywhere`）、`restore_back` 换完是否把对方换回原位、`when` **她自己的心情门槛**（`immediate`/`wait`/`full`；「对方心情是多少」不构成限制）、`enabled` 强制开关；⚠️ **就地改** `world`。优先规则见上文 |
| `entry_event_holders` | `(world)` | `list[(干员名, 房间名)]` | 列出可能触发进驻事件的干员（如菲亚梅塔），供界面提示 |
| `find_entry_target` | `(world, holder, facility, swap_with=None, scope="dorm")` | `(Operator, 说明)` | 预览"会换谁"（界面用它做候选与提示） |
| `entry_target_kind` | `(swap_with, scope="dorm")` | `"named"/"auto"/"default"` | 口径判定（**行为与文案同源**，避免"实际自动挑、文案写前一位"） |
| `mood_skill_summary` | `(op)` | `(已解锁数, [(技能名, 需要精英, 需要等级)])` | 这个练度下"他有哪些心情技能生效、少算了哪几条"——只读解释接口，不参与速率计算（界面用它写练度那行字） |
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

### 5.3 输出层（`from store.serialize import ...`）

| 函数 | 签名 | 说明 |
|---|---|---|
| `mood_result_to_dict` | `(r, period_hours)` | MoodResult → JSON dict（数字按 6 位小数舍入，Infinity→null） |
| `base_result_to_dict` | `(r, period_hours=None)` | BaseResult → JSON dict |
| `to_json_string` | `(data)` | dict → 可读 JSON 字符串（中文不转义） |
| `dump_json` | `(data, path)` | dict → 写 JSON 文件（自动建目录），返回绝对路径 |

### 5.4 数据管理层（`from store... import ...`）

| 入口 | 说明 |
|---|---|
| `store.session.Session` | ★**会话状态**：界面与程序接口共用的一份"当前在算的东西"。`load_paths([...])` / `load_layout({...})` / **`load_data({...})`** / `recompute()` / `moods_at(t)` / `mood_at(name,t)` / `rate_at(name,t)` / **`layout_at(t)`** / **`closure(cycles)`** / `red_face_spans(name)` / `set_initial_mood` / `set_mood_at` / `set_training` / `set_timeline` / `set_slots` / `entry_candidates()` / `idle_groups()` / `validate()` / `describe()` / `settings_dict()` |
| `store.schedule` | 多班排班与整周期轨迹：`Schedule` / `Shift` / `Trajectory` / `simulate_schedule` / `load_schedule(_ex)` / **`load_schedule_from_imports`** / `MoodSetEvent` / `default_initial_moods` / `all_operator_names` |
| `store.sources` | 排班/蓝图 JSON 的格式自动识别与转换：`detect_format` / `import_data` / `import_file` / `resolve_name` |
| `store.layout` | `dict/JSON → BaseLayout`：`build_base_layout` / `build_operator` |
| `store.maa` | MAA 排班解析：`read_maa` / `plans_from_data`（**唯一的房间映射表** `ROOM_MAP`） |
| `store.serialize` | 结果 → JSON：`mood_result_to_dict` / `base_result_to_dict` / `to_json_string` / `dump_json` |

> 老路径 `mood_soc.importer` / `mood_soc.output` / `mood_soc.scenario` / `mood_soc.maa` /
> `ui.schedule` 都是**兼容转发壳**，import 照旧能用（`tests/test_layers.py` 逐名盯着）。

### 5.5 数据层（`from data... import ...`）

| 入口 | 说明 |
|---|---|
| `data.paths` | **全部数据路径的唯一出口**（`MAA_SAMPLE` / `OPERATORS_TXT` / `SKILLS_TXT` / `SKILLS_DATA` / `SKILL_VERIFY_REPORT` …）；其它模块不许自己拼 `resources/…` |
| `data.domain` | 领域基元：`FacilityType` / `MOOD_MAX` / `MOOD_MIN` / `facility_slots` / `facility_max_level` / `DEFAULT_ELITE` / `DEFAULT_OPERATOR_LEVEL` |
| `data.skills_data` | **生成物**：`SKILLS` / `SKILL_EQUIPS` / `DEFAULT_OPERATORS` / `TRAITS` / `OPERATOR_FACTIONS` / `FACTION_MEMBERS` / `SPREAD_SKILL_IDS` / `VARIABLE_PRODUCERS` |
| `data.skill_model` | `Skill` / `SkillEquip` / `SkillKind` |
| `data.conditions` | 技能条件函数（`_cond_*`）与 `_factions_of` |

### 5.6 设施类型（`FacilityType`）

| 枚举值 | 中文名 | | 枚举值 | 中文名 |
|---|---|---|---|---|
| `control_center` | 控制中枢 | | `office` | 办公室 |
| `manufacturing` | 制造站 | | `training` | 训练室 |
| `trading` | 贸易站 | | `workshop` | 加工站 |
| `power` | 发电站 | | `dormitory` | 宿舍 |
| `reception` | 会客室 | | | |

### 5.7 完整示例

```python
from decimal import Decimal
from mood_soc import (build_base_layout, evaluate, evaluate_base, simulate,
                      work_rest_ratio, compute_net_rate)
from store.serialize import mood_result_to_dict, base_result_to_dict, dump_json

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
**输入 → 输出**是否正确，不测试任何内部结构 / 内部函数。当前共 **449 个用例全绿**。

```bash
# 运行全部测试
.venv/Scripts/python.exe -m unittest discover -s tests -v

# 只跑某个文件
.venv/Scripts/python.exe -m unittest tests.test_api_blackbox -v          # 公开 API 黑盒
.venv/Scripts/python.exe -m unittest tests.test_cli_blackbox -v          # 命令行黑盒
.venv/Scripts/python.exe -m unittest tests.test_skill_coverage -v        # 技能全量核对（三层）
.venv/Scripts/python.exe -m unittest tests.test_import_blackbox -v       # 导入：4 种格式识别 + 转换
.venv/Scripts/python.exe -m unittest tests.test_api_ops -v               # 程序接口：握手 / 全流程 / 与引擎同源
.venv/Scripts/python.exe -m unittest tests.test_layers -v                # 分层方向 / 兼容转发壳 / 数据路径只有一处
.venv/Scripts/python.exe -m unittest tests.test_ui_schedule_blackbox -v  # 图形界面的计算核心
.venv/Scripts/python.exe -m unittest tests.test_ui_app_smoke -v          # 界面冒烟（无图形环境自动跳过）
.venv/Scripts/python.exe -m unittest tests.test_ui_batch_blackbox -v     # 「干员与心情」面板黑盒
.venv/Scripts/python.exe -m unittest tests.test_ui_settings_blackbox -v  # 「设置」中心（同上）
```

当前 **449 个测试全绿**（其中 `test_layers.py` 是**结构回归网**：依赖方向、
兼容转发壳不漏名字、源码里不许手拼资源路径）。

技能侧另有一道"体检"（与测试同源，可独立跑、可出报告）：

```bash
.venv/Scripts/python.exe scripts/verify_skills.py --check    # 通过=退出码 0
.venv/Scripts/python.exe scripts/verify_skills.py --report   # 重写 resources/skill_verify_report.md
```

它做**四层**核对，把上游 **755** 条 buff / 本仓库 **250** 条心情 clause / **178 名有心情技能的干员**
全部过一遍：

| 层 | 对象 | 现状 |
|---|---|---|
| L1 模板级 | 32 个模板 | ✅ 模板 ↔ 数据自洽 + 机制用例 |
| L2 clause 级 | 250 条 clause | ✅ 逐条造场景核对（数值 / 桶 / **作用范围**）：242 条生效 + 8 条按性质归类 |
| L3 描述对照 | 上游描述原文 | ✅ 246 条一致 + 4 条无数字可对，**0 处差异** |
| **L4-a 真名挂载** | 178 名干员 | ✅ 178/178：真名建出来的技能槽与技能库**逐条一致** |
| **L4-b 真名结算** | 178 名干员 | ✅ 178/178：用真名 + 她真实的技能槽造场景，技能**真的进流水账** |
| **L4-c 组合技能双向对照** | 48 条组合型 clause | ✅ 48/48：**撤掉搭档 / 摘掉阵营 / 断掉变量**之后，数值必须真的变回去 |

L4 的两句话价值：L2 用的是**合成干员 + 手工注入 `skill_id`**（绕过"她身上到底挂了什么"、
也绕过"名字认不认得出来"）；而它只验"条件满足时数值对不对"，**没验**"条件不满足时不该生效"
—— 组合技能（共事 / 定向 / 阵营 / 变量 / 元修正）最容易错的就是后一半。
口径与踩过的坑见 `documents/05-技能分类大纲.md` §5.11。

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
| 图形界面：导入 3 班排班 | 3 个班次 / 周期 24h；看板 50 个位置；轨迹节点 131 |
| 图形界面：时间滑到 12h | 看板心情随之变化（如锡人 24 → 15） |
| 图形界面：对点选人 | 曲线与该干员的关键数值同步切换 |
| 图形界面：把卡夫卡标成 E1 | 芯片出现 `E1` 角标，练度摘要写明"少 1 条：「手工艺品·β」（E2）" |

---
