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
> `resources/AGD_心情技能数据源分析.md`（该文档只是**数据源分析**，不参与计算）。
>
> **技能分类**：每条技能都挂在一个**六轴模板**（`M01`~`M17` 心情类 / `X01`~`X11` 非心情）上，
> 新增干员技能 = 认模板 + 填参数；上游全部 buff 都有覆盖台账，未归类会**硬报错**。
> 见 `resources/skill_taxonomy.md`（分类大纲 + 模板字典）与 `resources/skills_registry.txt`（台账）。

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
| 潮汐守望（歌蕾蒂娅） | 没有其他深海猎人在宿舍以外 | 自身回复 +0.5（反之 +0.5 消耗/个） |
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

### 开发约定

- **每次改动即时提交**：每完成一次修改就 `git commit` 一次，提交信息用 **1–15 个字**简要描述
  （如「修复替换链」「补变量账本」）。
- 改了代码 / 数据 / 文档，必须同步更新 `AGENTS.md` 与 `README.md`。

### 技能分类与阵营表

- **分类**：每个技能 clause 挂在六轴模板（`M01`~`M17` 心情类 / `X01`~`X11` 非心情）上，
  新增干员技能 = 认模板 + 填参数；上游全部 755 条 buff 都有覆盖台账，未归类**硬报错**。
  见 `resources/skill_taxonomy.md`、`resources/skills_registry.txt`。
- **阵营/标签**：`resources/factions.txt` 由 `scripts/generate_factions.py` 从上游
  `cc.g.*` / `cc.tag.*` 自动生成（28 组 231 条），**不手工维护**
  （人工补充只有上游不列名单的「异格者」）。生成器会校验技能引用的阵营名都存在，
  **包括写在条件表达式里的名字**（`_cond_target_in_faction` / `_cond_target_is`），
  写错阵营名或干员名都会直接报错。
- **架构**：运行时（派发/布局/记录）的可拓展性诊断与重构设计见 `resources/mood_engine_design.md`。

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
- 完整策略（含四个坑与查完后的动作）见 `AGENTS.md` §11。

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

> 阵营表 `OPERATOR_FACTIONS` 是手工维护的（resources 数据里没有干员↔阵营映射）。

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
├── main.py                命令行入口（--mode single|base / --demo / --scenario-file / --target / --period / --trace）
├── mood_soc/              核心包（库，可被 import）
│   ├── __init__.py        对外公共 API 汇总（导出下面各模块的公开符号）
│   ├── config.py          纯配置层：常量与数据表（无逻辑）
│   ├── battery.py         纯数学层：安时积分法 + to_decimal/INF（与游戏规则无关）
│   ├── models.py          数据模型层：Operator / Facility / BaseLayout / MoodResult / OperatorResult / BaseResult
│   ├── skills.py          规则数据层：Skill / SkillEquip / SkillKind 框架 + 条件函数（末尾 re-export 数据）
│   ├── skills_data.py     技能数据表（自动生成，勿手改）：SKILLS / DEFAULT_OPERATORS / SKILL_EQUIPS / TRAITS
│   ├── rules.py           业务逻辑层：把"布局 + 干员"折算成净速率
│   ├── simulator.py       时间步进模拟器：处理红脸等时变情况
│   ├── report.py          展示层：中文结果格式化（文本）
│   ├── output.py          输出层：结果 -> JSON dict / 写入文件
│   └── scenario.py        输入解析层：字典/JSON -> BaseLayout
├── tests/                 黑盒测试（只断言"输入 → 输出"，不测内部结构）
│   ├── __init__.py
│   ├── test_api_blackbox.py   公开 API 黑盒：场景 JSON + 目标/时段 → 结果 JSON
│   └── test_cli_blackbox.py   命令行黑盒：subprocess 调 main.py → stdout JSON / 退出码 / 结果文件
├── scripts/
│   ├── maa_to_scenario.py  把 MAA 排班 JSON 转成本工具的场景 JSON
│   └── generate_skills_data.py  把 resources 两份 CSV 生成为 mood_soc/skills_data.py
├── scenarios/             demo.json + maa_shift1/2/3.json（示例场景）
├── resources/             心情消耗回复和工休时间.docx + moods_skills.txt + operators.txt
│                          + skills_registry.txt（755 行 buff 覆盖台账）
│                          + skill_taxonomy.md（六轴 + 模板字典）
│                          + arknights-infra-schedule-maa.json + AGD_心情技能数据源分析.md
├── results/               运行生成的结果 JSON（已被 gitignore）
├── README.md              面向人类的完整说明
├── AGENTS.md              给 AI 的项目速读指南
├── requirements.txt       仅标准库，无第三方依赖（Python 3.9+）
└── .venv/                 Python 3.14 虚拟环境（uv 创建）
```

依赖关系单向向下：`main / tests → report/output/simulator/rules → skills/models → config/battery`，
无环、无横向耦合。

---

## 四、使用方法

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
| `--demo` | flag | 关 | 使用内置演示场景（内容同 `scenarios/demo.json`） |
| `--scenario-file` | 文件路径 | 无 | 从 JSON 文件读取场景 |
| `--target` | 干员名 | 无 | single 模式的目标干员；`--demo` 时缺省为「泡泡」 |
| `--period` | 小数（小时） | `0.0` | 目标时段；single 演示缺省 8、自定义场景缺省 0 |
| `--out-dir` | 目录 | `results` | JSON 文件输出目录（仅配合 `--json-file` 生效） |
| `--json-file` | flag | 关 | 是否额外把结果写入 JSON 文件（默认只打印到 stdout） |
| `--trace` | flag | 关 | 仅 single 模式：附加心情轨迹（时间步进模拟，输出到 stderr） |

**场景来源优先级**：`--demo` > `--scenario-file`；两者都不给则打印帮助并退出。

**single 模式**：必须提供 `--target`（`--demo` 时缺省为「泡泡」）。`--period` 缺省：`--demo` 为 8 小时，`--scenario-file` 为 0 小时（即只看当前状态）。

**base 模式**：忽略 `--target`；`--period` 缺省为 0；`--trace` 不生效。

**输出约定**：stdout **永远是纯 JSON**（便于程序解析）；错误提示、文件路径、`--trace` 轨迹都走 stderr。

**文件输出**：默认不写文件；加 `--json-file` 后，结果写入 `--out-dir`（默认 `results/`）下的
`single_<干员>_<时间戳>.json` 或 `base_<时间戳>.json`，并在 stderr 打印路径。

### 1) single 模式

```bash
python main.py --demo                                          # 演示场景，目标泡泡，8 小时
python main.py --demo --target 斥罪 --period 8                 # 指定目标与时长
python main.py --scenario-file scenarios/demo.json --target 泡泡 --period 8
python main.py --demo --target 泡泡 --trace                    # 附带心情轨迹（文本）
python main.py --demo --json-file                             # 额外把结果写入 results/ 下的 JSON 文件
```

返回 JSON（`--demo --target 泡泡 --period 8` 的真实输出）：

```json
{
  "mode": "single",
  "operator": "泡泡",
  "facility": "制造站",
  "period_hours": 8,
  "net_rate": 0.1,
  "state": "工作中",
  "mood": 23.2,
  "sustain_hours": 232
}
```

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
  "layout_sustain_hours": 25.263158,
  "bottleneck": "斥罪",
  "operators": [
    {"name": "玛恩纳",   "facility": "控制中枢", "mood": 24, "sustain_hours": 25.263158, "mood_at_end": 6.315789},
    {"name": "泡泡",     "facility": "制造站",   "mood": 24, "sustain_hours": 25.263158, "mood_at_end": 21.473684},
    {"name": "斥罪",     "facility": "办公室",   "mood": 24, "sustain_hours": 25.263158, "mood_at_end": 0},
    {"name": "菲亚梅塔", "facility": "宿舍",     "mood": 24, "sustain_hours": 0, "mood_at_end": 24}
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

- `type` 支持中文名或英文枚举值（`control_center` / `manufacturing` / ...）。
- 干员既可用名字字符串（自动套用内置技能，默认 `elite=2` 满练），也可用
  `{"name": "x", "mood": 20.5, "skill_ids": [...], "trait": "岁", "elite": 2, "level": 1}` 对象。
  `elite`（精英化等级 0/1/2）与 `level`（干员等级）控制技能解锁：某些技能只有精英化后才可用，
  或精英化后才"提升"到目标效果（如 火神 工匠精神 α→β）。

## 五、Python API

所有数值均为 `decimal.Decimal`；"无限"用 `mood_soc.INF`（= `Decimal('Infinity')`）表示，转 JSON 时为 `null`。
入参建议传字符串或 `Decimal`；传 float 也会被 `to_decimal` 经字符串安全转换（不丢精度）。

### 5.1 顶层公开 API（`from mood_soc import ...`）

| 函数 / 常量 | 签名 | 返回 | 说明 |
|---|---|---|---|
| `build_base_layout` | `(data)` | `BaseLayout` | 从场景 dict 构建布局（格式见 §4.3） |
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

- `Operator(name, mood=MOOD_MAX, skill_ids=[], trait=None, elite=2, level=1)` —— 干员（elite/level 控制技能解锁）
- `Facility(ftype, level=1, operators=[], atmosphere=None)` —— 设施房间
- `BaseLayout(facilities=[])` —— 基建布局；方法：`get_facility` / `control_center` / `facility_of` / `get_operator` / `all_operators`

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

测试为**黑盒测试**：只通过「命令行」和「公开 API」断言**输入 → 输出**是否正确，
不测试任何内部结构 / 内部函数。

```bash
# 运行全部测试
.venv/Scripts/python.exe -m unittest discover -s tests -v

# 只跑某个文件
.venv/Scripts/python.exe -m unittest tests.test_api_blackbox -v   # 公开 API 黑盒
.venv/Scripts/python.exe -m unittest tests.test_cli_blackbox -v   # 命令行黑盒
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

---