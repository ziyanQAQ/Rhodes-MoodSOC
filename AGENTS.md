# AGENTS.md —— 给 AI 的项目速读指南

> **维护约定（必读）**
>
> 1. **每次修改项目（代码 / 数据 / 文档 / 结构 / 数值规则）后，必须同步更新本文件与
>    `README.md`**，保持两者与实际代码一致，方便后续接手的 AI / 人类快速理解。
> 2. **提交约定**：每完成一次修改就**立即 `git commit` 一次**，不要攒着；
>    提交信息用 **1–15 个字**简要描述改动（如「修复替换链」「补变量账本」「忽略 IDE 目录」），
>    不写多段说明、不加 `feat:`/`fix:` 前缀。一次提交只做一件事（改了代码就别把无关文档混进来，
>    除非该文档是这次改动的同步更新）。
>
> 面向后续接手本项目的 AI / 编码智能体。目标：读完本文件即可理解"这个项目在做什么、
> 怎么算的、代码长什么样、如何改如何测"，无需逐行重读源码。
> 更偏"人"的完整说明见同目录 `README.md`；两者规则一致，本文件侧重"快速上手 + 精确规则 + 易错点"。

---

## 0. 一句话概括

这是一个 **纯 Python 标准库** 的建模工具，把《明日方舟》基建中的干员 **心情** 抽象为一块
**电池**，用 **BMS-SOC 安时积分法（库仑计数 / Ampere-Hour Integration）** 做数学建模，回答两个问题：

> - **single**：输入「干员 + 基建布局 + 目标时段」→ 该干员剩余心情 + 其余干员心情无限时还能工作多久；
> - **base**：输入「基建布局」→ 每个干员的心情 + 该布局"没有一个干员红脸"可维持的最长时长。

规则来源：
- `resources/心情消耗回复和工休时间.docx`（需求文档，心情消耗/回复/工休的**计算规则**）；
- `resources/moods_skills.txt` + `resources/operators.txt`（**真实技能库与干员↔技能映射**，
  含精英化解锁等级、value 千分值、作用 family **与分类模板 `template_id`/`params`**，由
  `scripts/generate_skills_data.py` 一键生成 `mood_soc/skills_data.py` 数据表）；
- `resources/skill_taxonomy.md`（**技能分类大纲：六轴 + 模板字典**，新增技能先查这里）；
- `resources/skills_registry.txt`（上游 755 条 buff 的**覆盖台账**，防漏用）；
- `resources/factions.txt` + `factions_supplement.txt`（**干员↔阵营/标签表**，由
  `scripts/generate_factions.py` 从上游 `cc.g.*` / `cc.tag.*` 生成，28 组；人工补充仅异格者）；
- `resources/variable_producers.txt`（**变量产出者表**：人间烟火/热情值/无声共鸣 的产出端，
  逐条注明上游 buff 出处；见 `mood_soc/variables.py`）；
- `resources/mood_engine_design.md`（**架构诊断与重构设计**：运行时派发/布局模型/记录层的改造蓝图）。

> 技能数值以两份 txt 为**权威来源**（docx 中的技能示例值已过时，不要作为技能数值依据）。

> ### 🔎 数据查找策略（强制，见 §11）
> 遇到任何**不知道的数据**——技能原文 / 数值 / 解锁精英化与等级 / 阵营成员名单 /
> 设施集合定义 / 全局常量 / 机制术语——**先去上游仓库查证**：
> [**Kengxxiao/ArknightsGameData**](https://github.com/Kengxxiao/ArknightsGameData)
> （`zh_CN/gamedata/excel/`）。**不要凭印象写、不要猜、不要从二手资料誊抄。**
> 本项目 `resources/*.txt` 与 `skills_data.py` 都只是**上游的派生物**；
> 两者不一致时**以上游为准**，并修正派生物。

---

## 1. 目录结构与分层（高内聚、低耦合）

```
Rhodes-MoodSOC/
├── main.py                命令行入口（--mode single|base / --demo / --scenario-file / --target / --period / --json-file / --trace / --explain）
├── mood_soc/              核心包（库，可被 import）
│   ├── __init__.py        对外公共 API 汇总（导出下面这些符号）
│   ├── config.py          纯配置层：常量 + 数据表 + 解析函数（无业务逻辑）
│   ├── battery.py         纯数学层：安时积分法 + MoodBattery + clamp（与游戏规则无关）
│   ├── models.py          数据模型层：Operator / Facility（多房间/容量/副手/活动室）/ BaseLayout（按类型聚合）
│   │                      / MoodResult（含 ledger）/ OperatorResult / BaseResult
│   ├── skills.py          规则数据层：Skill / SkillEquip / SkillKind 框架 + 条件函数；末尾 re-export 数据
│   ├── skill_templates.py 分类字典：六轴枚举（ModelTier/Domain/Target/Effect/ValueShape/Stacking）+ 模板注册表
│   ├── ledger.py          ★记录层：Contribution / MoodLedger（逐条贡献流水账）+ 轴 F 统一合成
│   ├── variables.py       ★变量账本：26 种"中间货币"（人间烟火/热情值/无声共鸣…）+ 产出者收集
│   ├── skills_data.py     技能数据表（自动生成，勿手改）：SKILLS / DEFAULT_OPERATORS / SKILL_EQUIPS / TRAITS
│   │                      + SPREAD_SKILL_IDS（玛恩纳扩散白名单）/ ROOM2_MAX_GROUP
│   ├── rules.py           业务逻辑层：**流水账驱动**——把「布局 + 干员」折算成消耗/回复/净速率 + 各项查询
│   │                      （唯一计算路径：先记 Contribution，再由 MoodLedger 合成，见 ledger.py）
│   ├── simulator.py       时间步进模拟器：逐步重算速率，处理"红脸→技能失效"等时变情况
│   ├── report.py          展示层：把 MoodResult / 轨迹 格式化为中文文本
│   ├── output.py          输出层：结果 → JSON dict / 写入文件（inf → null）
│   └── scenario.py        输入解析层：dict / JSON → BaseLayout（隔离输入格式与内部模型）
├── tests/                 黑盒测试（unittest，只断言"输入→输出"，不测内部结构）
│   ├── test_api_blackbox.py  公开 API 黑盒：场景 JSON + 目标/时段 → 结果 JSON（dict）
│   └── test_cli_blackbox.py  命令行黑盒：subprocess 调 main.py → stdout JSON / 退出码 / 结果文件
├── scripts/
│   ├── maa_to_scenario.py          把 MAA 排班 JSON 转成本工具的场景 JSON
│   ├── classify_skills.py          分类器：给每个 clause 挂模板 + 生成 755 行覆盖台账 + 零遗漏校验
│   ├── generate_factions.py        从上游 termDescriptionDict 生成干员↔阵营/标签表
│   └── generate_skills_data.py     把 resources 两份 CSV 生成为 mood_soc/skills_data.py（技能数据管道）
├── scenarios/             demo.json + maa_shift1/2/3.json（示例场景）
├── results/               运行生成的结果 JSON（已被 gitignore）
├── resources/             心情消耗回复和工休时间.docx（需求文档）+ arknights-infra-schedule-maa.json
│                          + moods_skills.txt / operators.txt（技能数据源，含 template_id/params）
│                          + skills_registry.txt（上游 755 条 buff 覆盖台账）
│                          + factions.txt / factions_supplement.txt（阵营/标签表，上游生成）
│                          + variable_producers.txt（变量产出者表，人间烟火/热情值/无声共鸣）
│                          + mood_engine_design.md（架构诊断与重构设计）
│                          + skill_taxonomy.md（技能分类大纲：六轴 + 模板字典）
│                          + AGD_心情技能数据源分析.md（上游仓库结构分析 + 待判定清单，非代码依赖）
├── README.md              面向人类的完整说明
├── requirements.txt       仅标准库，无第三方依赖（Python 3.9+）
└── .venv/                 Python 3.14 虚拟环境（uv 创建）
```

**依赖方向严格单向向下，无环、无横向耦合：**

```
main.py / tests
   ↓ 调用
report / output / simulator / scenario / rules
   ↓ 依赖
skills / models
   ↓ 依赖
config / battery   ← 最底层（config 只 import decimal+enum；battery 只 import decimal）
```

改代码时的经验法则：
- **改数值/阈值** → 只动 `config.py`；**新增技能** → 只动 `skills.py`（加一条数据）；
- **改计算逻辑** → 只动 `rules.py`；**改数学积分** → 只动 `battery.py`；
- **改输入格式** → 只动 `scenario.py`；**改输出文案** → 只动 `report.py`。

---

## 2. 数学建模：心情 ⇔ 电池

| 电池概念 | 心情概念 | 本项目的常量/变量 |
|---|---|---|
| 容量 C | 心情上限 | `MOOD_MAX = Decimal("24")` |
| 空电量 | 红脸（技能失效） | `MOOD_MIN = Decimal("0")` |
| 荷电状态 SOC | 当前心情 | `Operator.mood`（0~24，可含小数） |
| 放电电流 | 净心情消耗速率 | `net_rate = I`（点/小时，>0 下降） |
| 充电电流 | 净心情回复速率 | `-I`（I<0 上升） |

**核心公式（安时积分 / 库仑计数）：**

```
                t
SOC(t) = SOC(t0) - ∫ I(τ) dτ        其中  I(τ) = 消耗速率(τ) - 回复速率(τ)
                t0
```

**离散化**（`simulator.py` 时间步进用）：

```
SOC[k+1] = clamp( SOC[k] - I[k] * Δt , 0 , 24 )
```

**解析解**（假设时段内速率恒定、其余干员心情无限，`rules.py` 用）：

```
剩余心情    = clamp( SOC0 - I * T , 0 , 24 )        # remaining_mood_after(world, name, T)
sustain_hours（还能维持/恢复多久，evaluate 输出）：
  I>0（工作）→ 剩余心情 / I           到红脸
  I<0（宿舍）→ (24 - 剩余心情) / (-I)  恢复满
  I=0 → Infinity（JSON null）
```

> ⚠️「还能维持/恢复多久」有两个入口，别混淆（这是最容易写错的点）：
> - `remaining_work_hours(world, name)`：底层函数，从**当前（初始）心情**起算的"工作时长" → `SOC0 / I`（net≤0 为 Infinity）；
> - `evaluate(world, name, T)` 的 `sustain_hours` 字段：先推进 T 小时算出剩余心情，再按净速率符号给时长：
>   工作（net>0）→ `剩余心情 / net`（到红脸）；宿舍（net<0）→ `(24-剩余心情)/(-net)`（恢复满）。CLI 输出的是这一种。

关键约定（务必记住，很多 bug 源于此）：
- **`I = 消耗 - 回复`**；`I>0` 心情下降（工作中），`I<0` 心情上升（休息中/空闲中），`I=0` 不变。
- 心情**始终钳位在 [0, 24]**，不会为负、不会超过 24。
- "还能维持/恢复多久"（`sustain_hours`）返回 `Decimal('Infinity')`（即 `mood_soc.INF`）表示永续（净速率 ≤ 0）。

**精度策略（全程 Decimal）**：所有数值都是 `decimal.Decimal`（十进制精确），不要用 float 参与运算。
- 常量在 `config.py` 里用 `Decimal("0.1")` 等字符串字面量定义；`config.py` 导入时统一设置
  全局 Decimal 上下文（28 位有效数字 + ROUND_HALF_UP）。
- 任何外部输入（JSON 的 mood / atmosphere、CLI 的 period、simulate 的 duration/step、API 的
  consumption/recovery）都要经 `battery.to_decimal()` 安全转换——**必须走字符串，禁止 `Decimal(float)`**。
- "无限"统一用 `INF = Decimal('Infinity')`（在 `battery.py` 定义，`mood_soc` 顶层导出），
  `==`/`<=`/`<` 等判断对 Decimal 是**精确**的，无需容差。
- 唯一一次受控舍入在输出边界：`output.py` 把 Decimal 按 6 位小数舍入成 JSON 数字（整数退化为 int），
  `Infinity` 输出为 `null`。

---

## 3. 净速率 I 的完整构成（= 文档规则全集）

### 3.1 心情消耗 `compute_consumption(world, op, facility)` → 结果 ≥ 0

```
消耗 = base_consumption(设施类型)   ← 见 config.BASE_CONSUMPTION_BY_FACILITY
       工作设施 1.0；**加工站 0**（心情是按次消耗，不搓材料就是 0）；
       训练室 = TRAINING_BASE_CONSUMPTION = 1.0（docx 第 4 段「工作时基础消耗 1 点/时」）；宿舍/活动室 0
     - X          设施基础减免（仅 制造站/贸易站：按进驻人数 (n-1)×0.05，上限 0.1）
     - 中枢减免    控制中枢全局减免：按进驻人数线性折算 (cc人数/5)×0.25（满员=0.25）
     ± 自身技能    self_consume（泡泡 -0.25、火神 α-0.15/β-0.25、阿罗玛 +0.25、斥罪 +0.5、夕"不以己悲"+0.5 等）
                   若技能带 `var_*`/`basis` 参数，值再按变量/计数折算（见 §4.16）
     ± 设施级技能  facility_consume（黍 -0.1、夕"不以物喜"中枢 -0.05、**巫恋"低语"+0.25**；对同设施全体含自身生效）
     ± 同设施其他  room_others_consume（**当前无使用者**；低语已按官方原文"全体"改判为 facility_consume，见 §4.11）
     - 中枢减免技能 cc_reduce（预留；真实数据中维什戴尔/重岳等已是 cc_recover，见 3.2）
     （若存在"消除类"干员，则"自身技能"这一项被清零，见 §4）
最后： max(0, 上面结果)   —— 消耗不为负，负值一律走"回复"侧表达
```

### 3.2 心情回复 `compute_recovery(world, op, facility)`

> 消耗与回复都由同一套**流水账**产出（`rules.consume_ledger` / `recovery_ledger`），
> 再交给 `ledger.MoodLedger` 按轴 F 合成——没有"带解释"与"不带解释"两份逻辑。
> 想看某个干员的速率为什么是这个值：`rules.mood_ledger(world, "泡泡").explain()`。

**工作设施内**（非宿舍）：来自中枢内干员的 `cc_recover` 技能，按**三条叠加规则**合成（轴 F）：
- **F1 求和**：不同来源默认相加。
- **F3 跨干员取最高**（`max_group=room2_recover`）：官方术语 `cc.c.sui2_1` 规定
  公事公办 / 孤光共照 / 巴别塔之帜 的 room2 恢复值**取最高**——实现为**同干员各 clause 先求和，再跨干员取 max**。
- **M02c 扩散**：玛恩纳在中枢时，官方术语 `cc.c.skill` 的 **15 条**白名单中枢回复技能
  额外作用到 room2（其他设施）内工作状态的干员；扩散提供者红脸则失效。

| 技能 | 效果 |
|---|---|
| 玛恩纳「公事公办」 | room1（发电/办公/会客）+0.1，**并把 15 条白名单中枢回复技能扩散到 room2** |
| 维什戴尔「巴别塔之帜」 | room2 +0.1（精英2）；与魔王同在中枢再 +0.1（同干员内相加后参与取最高） |
| 重岳「孤光共照」 | room2 +0.05（精英2） |
| 玛恩纳「独善其身」/ 冰酿「笑靥如春」等 15 条 | 中枢内 +0.05（被扩散时也作用于 room2） |

**宿舍内**（`_dorm_recovery`）：

```
宿舍回复 = 基础回复（白字 + 绿字氛围）
         + 中枢干员对宿舍的回复（cc_recover 且 target=宿舍，如"领袖/战纹/巡心/羁绊相生"等）
         + 自身回复（dorm_self，同种取最高）
         + 群体回复（dorm_group，同种取最高；冰酿单独分配）
         + 单体回复（dorm_single，同种取最高，锁定"心情最低且未满且非提供者"的干员）
         + 定向回复（dorm_targeted，满足条件者叠加求和）
         ± 元修正（M17，dorm_meta）：**他人**把上面某条效果顶高（摩根→推进之王 +0.3，见 §4.28）
         + 冰酿分配（0.8 总额 / 心情未满成员数）
```

- 基础回复 = `1.5 + 0.1×等级 + 0.0004×实际氛围`（`atmosphere=None` 时按该等级满氛围）。
- 宿舍等级表（`DORM_LEVEL_TABLE`）：

| 等级 | 氛围上限 | 满氛围基础回复 | 校验式 |
|---|---|---|---|
| 1 | 1000 | 2.0 | 1.5+0.1+0.4 |
| 2 | 2000 | 2.5 | 1.5+0.2+0.8 |
| 3 | 3000 | 3.0 | 1.5+0.3+1.2 |
| 4 | 4000 | 3.5 | 1.5+0.4+1.6 |
| 5 | 5000 | 4.0 | 1.5+0.5+2.0 |

- 叠加规则：**不同种类（自身/群体/单体/定向）之间相加；同一种类内部取最高**。
  例外：定向回复（dorm_targeted）内部是**求和**而非取最高（`_targeted_recovery` 直接累加）。

### 3.3 顶层查询（`rules.py`）

| 函数 | 作用 |
|---|---|
| `compute_net_rate(world, name)` | 净速率 = 消耗 - 回复（>0 下降 / <0 上升） |
| `remaining_mood_after(world, name, hours)` | 时段后剩余心情（钳位 [0,24]） |
| `remaining_work_hours(world, name)` | 还能工作多久（net≤0 → inf） |
| `time_to_mood(world, name, target_mood)` | 达到指定心情所需时间（工作=下降、宿舍=上升） |
| `evaluate(world, name, hours)` | single 模式汇总 → `MoodResult`（含中文状态） |
| `evaluate_base(world, hours)` | base 模式汇总 → `BaseResult`（所有干员 + 布局可维持时长） |
| `work_rest_ratio(x, y)` | 工休比指标 → `WorkRestRatio` |

`evaluate` 的中文状态判定顺序：
```
不在任何设施 → "未进驻"
宿舍         → "宿舍休息"
mood ≤ 0     → "红脸（技能失效）"
net < 0      → "休息中 / 空闲中（回复大于消耗）"
net == 0     → "心情不变"
否则         → "工作中"
```

### 3.4 工休比 `work_rest_ratio(consumption=x, recovery=y)`

```
可连续工作    = 24 / x           （x>0，否则 Infinity）
从零回满      = 24 / y           （y>0，否则 Infinity）
最大工休比    = y / x
最大工作时长占比 = y / (x + y)
24h 内最长可工作时间 = 24·y / (x + y)
```

### 3.5 两种测算模式：single 与 base（核心入口）

| | single（`evaluate`） | base（`evaluate_base`） |
|---|---|---|
| 对象 | 一个目标干员 | 基建内所有干员 |
| 心情值 | 目标时段结束后的剩余心情 | 当前心情（或 `period>0` 时推进后） |
| sustain_hours | 其余干员心情无限：工作→到红脸、宿舍→恢复满 | 工作=布局可维持时长；宿舍=min(恢复满时间, 布局可维持时长) |

**base 模式口径**（`rules.evaluate_base`）：
- 布局可维持时长 `layout_sustain_hours` = 所有干员"到红脸（mood≤0）剩余时长"的**最小值**
  （mood/net，net>0；宿舍/回复 net≤0 为 Infinity；已红脸 mood≤0 为 0），并标出 `bottleneck`（最先红脸者）。
- 每个干员的 `sustain_hours`：
  - 工作（net>0）/ 不变（net=0）→ 统一 = layout_sustain_hours；
  - 宿舍（net<0）→ `min(恢复满所需时间, layout_sustain_hours)`：能提前恢复满则记恢复满时间，否则维持布局可维持时长。
  每个干员 `mood_at_end` = 到达 layout_sustain_hours 时的心情 = clamp(mood - net×layout_sustain_hours, 0, 24)。
  layout_sustain_hours 为 Infinity（无人会红脸）时，二者均为 None（JSON null）。
这是解析解（速率恒定）；要"红脸后技能失效联动"用 `simulate`。

**JSON 输出**（`mood_soc/output.py`）：`mood_result_to_dict` / `base_result_to_dict` 转 dict，
`dump_json` 写文件；`inf` 一律序列化为 `null`。CLI（`main.py --mode ...`）默认只把 JSON 打印到 stdout，
加 `--json-file` 才写入 `results/`。

---

## 4. 特殊情况处理（需求文档第 3 条，务必遵守）

1. **红脸（心情 ≤ 0）**：该干员**所有心情类技能失效**（`_active(op)` 返回 `mood > 0`），
   但仍可继续工作，只是不再影响速率。
2. **心情钳位**：任何时刻心情 ∈ [0, 24]，积分/查询/模拟均通过 `clamp` 保证。
3. **菲亚梅塔独占**：宿舍中只要她"持有技能且未红脸"，回复**恒等于 2.0**，
   **不接受**宿舍基础回复和其它任何来源（`_dorm_recovery` 顶部短路 return）。
4. **冰酿分配**：0.8 总额**平均分给"心情未满（<24）"的宿舍成员**；满心情者不分；
   宿舍无人未满时该项为 0（不会除以零）。
5. **消除类（槐琥 / 令）**：只消除目标干员 **"自身技能"（self_consume）的正负影响**；
   中枢减免、设施级技能**不受影响**；令额外限定 `trait="岁"`。
6. **中枢减免"取最高" vs 设施技能"求和"**：`cc_reduce` 按"不同干员取最高"（每名干员自身技能先求和，再跨干员取 max）；`facility_consume` 是"全体求和"。两者别搞混。
7. **消耗不为负**：`compute_consumption` 末尾 `max(0, total)`；"净回复"统一由 `compute_recovery` 表达。
8. **目标干员不在任何设施**：净速率视为 0（不消耗也不回复）。
9. **未知干员**：`compute_net_rate` / `remaining_*` / `evaluate` / `simulate` 抛 `KeyError`。
10. **显示取整**：`report._fmt_mood` 同时给出游戏内**向下取整**值 + 真实小数（如 `12（实际 12.700）`）。
11. **巫恋「低语」= 全体含自身**（用户拍板）：官方原文「…同时**全体**心情每小时消耗+0.25」，
    故归 `FACILITY_CONSUME`（`M05`，`include_self=true`），不再是 `ROOM_OTHERS_CONSUME`。
    `M06`/`ROOM_OTHERS_CONSUME` 保留但**当前无使用者**。
12. **玛恩纳扩散**：官方术语 `cc.c.skill` 的 15 条白名单中枢回复技能，在玛恩纳进驻中枢时
    额外作用于 room2；提供者红脸则扩散失效（`rules._spread_active` / `_reaches`）。
13. **room2 三条取最高**：官方术语 `cc.c.sui2_1`。`max_group=room2_recover` 的技能
    **同干员内求和、跨干员取最高**（由 `MoodLedger.total()` 完成）。
14. **各设施基础消耗不同**（P1 修正）：旧实现只排除宿舍、其余一律 1.0，把**加工站算成 0.75/h**。
    现在按 `config.base_consumption(ftype)`：工作设施 1.0、**加工站 0**（按配方/按次消耗，不搓材料就是 0）、
    活动室 0、宿舍 0、训练室 1.0（见下条）；加工站的证据见 §4.31。
15. **训练室已建模，基础消耗 1.0/h 有两条独立佐证**（P5c 完成）：
    - 需求文档 `docx` **第 4 段**：「干员工作时，在无额外心情加减的情况下，每小时的**基础消耗速率为 1 点心情**」
      ——不区分设施，训练室同样适用（这就是 `TRAINING_BASE_CONSUMPTION = 1` 的依据，
      **不再是"按增量语义猜的"**）；
    - 上游 `building_data.json → buffs`：**9 条**训练室 buff 原文均为「…时，心情每小时消耗 **+1**」：
      `train_cost&profession[140]` 工作狂 / `[320]` 过量训练 / `[340]` 索然无味 / `[350]` 何须解脱 /
      `[360]` 变异 / `[380]` 斗争渴望、`train_spd_bd[000]` 与人乐、`train_spd_doubleProf3[100]` 兴之所至·β、
      `train_spd_power_down[000]` “手段应当有效”。
    → 这 9 条已作为 `SELF_CONSUME +1`（`M07a`，family=`self`）进 `moods_skills.txt`，
      `FACILITY_BY_PREFIX["train"] = "TRAINING"`；持有人净消耗 **2.0/h**，同设施其他人仍 1.0/h。
    ⚠️ 上游 `trainingData` 只有训练**速度**常量（`basicSpeedBuff=0.05`），没有心情常量——
      所以训练室的 1.0/h 来自 docx 而非上游，别去 upstream 找。
16. **变量（中间货币）已纳入模型**（P3）：官方术语表 `cc.bd*` 共 26 种，其中
    **人间烟火 / 热情值 / 无声共鸣** 会影响心情，另有派生变量 **心情落差**（= 24 − 当前心情）。
    `mood_soc/variables.py` 的 `VariableLedger` + `resources/variable_producers.txt` 负责产出，
    技能用 `var_name`/`var_per`（每有 N 点 → 值 × floor(变量/N)）或 `var_min`（≥ N 才生效）消费。
    口径：变量是**基建级**值；产出者要①在布局里②已解锁③未红脸④条件成立（如夕「不以物喜」要求自身心情 < 12）。
    ⚠️ 变量与心情存在**单向快照依赖**（先用当前心情算变量，再用变量算速率）；
    `simulate` 每步重算故能跟上变化。
17. **变量产出者的持有者从 `operators.txt` 全量取**，不能从 `DEFAULT_OPERATORS`（仅心情技能）取——
    产出者里有不少是纯变量技能（祐天寺若麦「勤学苦练」、塑心「无声共鸣」），
    用后者会一个持有者都找不到（实测踩过这个坑）。
18. **「同种效果取最高」的粒度是「技能」不是「分句」**（P4a 修正）：
    上游原文写「…额外 +N 恢复效果（**叠加后的最终值**同种效果取最高）」
    → 同一技能的「基础 + 每有 N 额外」两个分句要**先求和**，再与其他技能取最高。
    `ledger.MoodLedger.total()` 的 `SAME_KIND_MAX` 现在按「**持有者 + skill_id**」先聚合分句、再取最高。
    旧实现按单个分句取 max，会丢掉基础分句（死前必做清单 Lv5 → 0.15 而非 0.25）。
19. **可数条件（`basis`）已纳入模型**（P4a）：12 条「每有 N 个什么」的子句从 `hold` 转 `apply`
    （柔和微光 每有 1 间发电站 / 死前必做清单 宿舍每级 / 倾谈者 每有 1 名未满干员 /
    独处·赋格融汇 每有 1 名其他干员 / 寻同路人·无瑕心 每个招募位 / 潮汐守望 每个深海猎人 /
    救援队·保证体力 每个招募位；挑大梁的「每有黑钢国际」修饰的是**生产力**，心情 -0.15 无条件）。
    基准实现见 `variables.basis_count(world, basis, facility, op)`。
20. **β 替换链的单位是 `skill_id`，不是 `skill_id#clause`**（P4a 修 bug）：
    旧实现把每个分句当成链上一环，导致「基础 + 每有 N 额外」结构里额外分句把自己的基础分句
    "替换"掉（实测 6 例：响石 0.15 / 铎铃 万里传书 -0.1 / 刺玫 0.15 / 波卜 0.2 / 流明 0.1 / 隐德来希 0.1 全丢）。
    `SkillEquip.replaces` 现在存 **skill_id**，`rules._active_skill_ids` 按 `base_skill_id` 整条剔除。
    ⚠️ 改动 `replaces` 语义后务必跑全量测试。
21. **分支条件已纳入模型**（P4b）：本地 CSV 的 `condition` 列被截断（`如果` / `反之` / `多心情子句`），
    按 §11 回上游取全文后才看出真实语义。现已生效：
    - **会客室 6 条**（双面间谍 / 「职业操守」α·β / 我自己的愿望 / 专业经理·α·β）：
      「如果会客室内**只有自身**处于工作状态时，…心情每小时消耗 +N」→ `_cond_alone_in_facility`
    - **潮汐守望 `#2`/`#3`**：「反之则自身心情每小时恢复 +0.5」「宿舍内深海猎人为满心情则额外 +0.5」
    - **互为半身**（若叶睦）：与丰川祥子同中枢时消除**自身**心情消耗影响
    - **资深料理人**（森西）：基础 0.15 + 「如果目标是**莱欧斯小队**」额外 0.15
    挂载方式：`generate_skills_data.py` 的 `CLAUSE_COND`（按 `(skill_id, clause)` 精确指定，附上游原文注释）。
22. **消除类（M13）有两种语义，靠 `self_only` 区分**：
    槐琥「团队精神」/ 令「杯莫停」= 消除**同设施所有干员**（含他人，也含持有者自己）；
    若叶睦「互为半身」= 只消除**自身**（`self_only=true`）。
23. **潮汐守望「每有 1 个深海猎人」**口径：**排除技能持有者自身**。
    理由：歌蕾蒂娅自己就进驻控制中枢（宿舍以外），若把她算进去，则「每有…」恒 ≥1、
    上游明写的「**反之**则自身心情每小时恢复 +0.5」分支永不可达——游戏不会写死分支。
    ⚠️ 这是一处**解释性口径**，若你判断应含自身，改 `variables.basis_count` 的 `abyssal_non_dorm` 分支即可。
24. **条件必须被求值**（P4b 修 3 处循环缺口）：`DORM_GROUP` / `DORM_SELF` / 消除类三个循环
    此前**从不调用 `skill.condition`**，导致按目标筛选的条件形同虚设
    （资深料理人会无条件对所有人 +0.15；互为半身会无条件消除所有人）。
    新增条件分支时，务必确认所在循环有求值（见 §10 清单）。
25. **`M07b`（自身回复·非宿舍）曾被完全忽略**：`_work_ledger` 只处理中枢的 `CC_RECOVER`，
    从不处理干员自己的 `DORM_SELF` 技能，所以潮汐守望的「反之」分支从未生效。
    现按 `template_id == "M07b"` 在 `_work_ledger` 内求值（用模板区分，避免 M10 宿舍自身回复外溢）。
26. **布局是一等公民**（多房间）：`Facility.operators` 是进驻者（占位/耗心情/算"处于工作状态"），
    `Facility.deputies` 是副手（不占位、不耗心情、被「不包含副手」类技能排除，29 条 buff 用到）；
    活动室 `FacilityType.PRIVATE` 的使用者默认被 `base_operators()` 排除（23 条 buff 用到）。
    `get_facility()` 只返回第一个同类型设施，多房间请用 `of_type()` / `count_of_type()`。
27. **`M09` 定向加成「如果目标是 X，则恢复效果额外 +0.45」已实现**（P5）：
    上游 6 条 `dorm_rec_single*` 写的是「…恢复 +0.55（同种效果取最高），**如果目标是 X，
    则恢复效果额外 +0.45**」。本地 CSV 当初挤成一行、加成丢失；现按上游原文**手工补 `clause#2`**
    （value=450，与 `#1` 同 `skill_id`），条件见 `generate_skills_data.CLAUSE_COND`：
    沏茶→锡兰、烤肉大师→嘉维尔、毒剂师之友→蓝毒（用 **`_cond_target_is`**，点名具体干员）；
    降生于冰寒→萨米、圣城趣事通→拉特兰、狩猎好帮手→怪物猎人小队/泡影国狩猎小队
    （用 **`_cond_target_in_faction`**，可传多个 = 并集）。
    两条配套铁律：
    - **「同种效果取最高」的单位是技能（含各分句），不是分句**（同 §4.18）。`_single_recovery`
      旧实现按单条分句取 `max` 会吃掉 +0.45；现改为记进 `MoodLedger` 后调用
      **`MoodLedger.same_kind_winner(bucket, group)`** 取获胜**技能实例**（0.55+0.45=1.00）。
      该方法与 `total()` 的 `SAME_KIND_MAX` 同源，规则只有一份。
    - **点名要过守卫**：`check_faction_refs()` 现在会扫 `CLAUSE_COND` 里的字符串字面量，
      阵营名与干员名对不上直接报错（干员名册取 `resources/operators.txt` **全量**，
      不能用 `DEFAULT_OPERATORS`——锡兰/嘉维尔/蓝毒都是只有生产/训练技能的干员）。
    ⚠️ 遗留简化：`_single_recovery` 的**目标锁定**是全局一名受益者（§8.5），
    所以「毒剂师之友」上游没写「除自身以外」、深靛本可自指，本模型仍把她排除在候选外。
28. **元修正（M17）：一名干员强化**另一名干员**的效果已实现**（P5b）：
    上游原文（`buffs["dorm_rec_toone[000]"]`）：「进驻宿舍时，**推进之王**对该宿舍中
    **格拉斯哥帮**干员恢复效果额外 **+0.3**」——摩根自己不回复，而是把**别人已经算出的那条贡献**顶上去。
    - 数据：`moods_skills.txt` 新增 `dorm_rec_toone_000`（family=`dorm_meta` → `SkillKind.DORM_META`，
      template=`M17`），params 为 `boost_provider=推进之王;boost_group=dorm_group`；
      被强化的**目标**由 `CLAUSE_COND` 的 `_cond_target_in_faction("格拉斯哥帮")` 筛选。
    - 实现（`rules._dorm_ledger` 的元修正段）：找出被点名提供者（`boost_provider`）已记入流水账、
      且 `group == boost_group` 的贡献，逐条补一条**同组、同 skill_id、同 owner** 的增量贡献。
      于是 `SAME_KIND_MAX` 会把它**先并入该技能的合计**、再与其他技能取最高——
      这正是「恢复效果额外 +0.3」的语义（推进之王 0.2 → 0.5）。
      ⚠️ 若把增量记成**独立技能**，会被"同种取最高"当成竞争者而整个丢掉，这是本机制最容易写错的地方。
    - 守卫：`boost_provider` 是点名引用，已被 `check_faction_refs()` 纳入干员名校验。
29. **进驻事件（M15a）已实现**：**患难之交**（菲亚梅塔）。上游原文（`buffs["dorm_exchangeAp[000]"]`）：
    「进驻宿舍时，**如果自身为满心情**，则与当前宿舍**前一位进驻**的干员互换心情」。
    它不是"每小时 ±N 点"而是**进驻那一刻的状态跳变**，因此：
    - **不进速率流水账、不进时间积分**，而是走显式 API **`rules.apply_entry_events(world)`**
      （CLI：`main.py --entry-events`），事件本身记为 `Bucket.EVENT` 展示用。
    - ⚠️ **会就地修改** `world` 的干员心情；`evaluate` / `evaluate_base` **不**自动调用
      （它是布局初始化语义，不是速率语义）。重复调用幂等（换完她就不再满心情）。
    - 「前一位进驻」= `Facility.operators` 里排在触发者之前的那一位——
      布局的 `operators` **本来就是有序列表**（进驻顺序），无需额外的队列结构。
      条件挂 `CLAUSE_COND` 的 `_cond_self_full_mood`；调度按 `template_id == "M15a"`
      （`rules._template_skills`，与 §4.25 的 M07b 同一约定）。
30. **流水账 `explain()` 的「同种取最高」判负按**技能小计**比，不按单条分句**（P5b 修）：
    一条获胜技能若由多个分句组成（「基础 + 每有 N 额外」、或被 M17 强化），
    旧实现拿每条分句的 `value` 去和"技能小计"比，会把**获胜技能的每条分句都标注成
    "被更高者覆盖"**（实测：摩根强化后 0.2 与 0.3 都被标）。现改为实例小计 vs 组内最高小计。
31. **加工站不建每小时模型，且这次是查出来的**（P5c）：上游 `building_data.json` 里
    **加工站相关且提到「心情」的 buff 共 35 条**，原文**全部**是「**配方**心情消耗」
    （`workshop_formula_cost*` / `cost2` / `cost3` / `cost4` / `cost5` / `lolxh` / `rub` / `proc_cost`），
    例如「心情消耗为 4 的配方全部 -1 心情消耗」「相应配方的心情消耗**恒定**为 2」「全部除以 4」。
    → 印证 `base_consumption(WORKSHOP) = 0`：加工站**没有**每小时消耗，心情是按**次**扣的。
    ⚠️ 但**配方本身的心情消耗在上游数据 dump 里没有字段**（`workshopFormulas` 68 条只有
      `apCost/goldCost/costs/…`，全库递归搜 `mood` 无命中），故"每次加工扣多少心情"**无法从上游落库**，
      35 条 X08 buff 与棘刺「爆炸艺术」一并保持登记不建模。别用 `apCost/180000` 之类的巧合反推。

---

## 5. 技能系统（skills.py + skills_data.py）—— 技能数据自动生成

> ⚠️ **技能数值不要手写进 `skills.py`**。真实技能数据在
> `resources/moods_skills.txt` + `resources/operators.txt`，运行
> `.venv/Scripts/python.exe scripts/generate_skills_data.py` 一键生成
> `mood_soc/skills_data.py`（含 `SKILLS` / `DEFAULT_OPERATORS` / `SKILL_EQUIPS` / `TRAITS`）。
> `skills.py` 只保留框架（`Skill` / `SkillEquip` / `SkillKind` / 条件函数），末尾 re-export 数据。

### 5.1 数据模型（三个关键对象）

- **`Skill`（技能效果，一个 skill_id 的一个 clause）**：字段
  `id / name / kind / value / facility_types / target_trait / condition / note /
  exclusive / pool / untranslated / count_faction / template_id / max_group /
  spread_whitelist / partial / partial_mode`。
  - `value` 已是真实 Decimal（生成时由千分值 ÷1000 得到），符号语义见下表；
  - `exclusive=True` 表示独占回复（菲亚梅塔）；`pool=True` 表示池分配（冰酿）；
  - `count_faction` 非空表示 **per-count 阵营计数**：value 是"每个该阵营干员"的回复量，
    规则引擎按"目标设施内该阵营干员数"倍增（如陈"德才兼备"、电弧"无言的慈爱"）。
  - `var_name` / `var_per` / `var_min` = **变量**折算（见 §4.16）；
  - `self_only` = 消除类只作用于**自身**（若叶睦「互为半身」）；False = 同设施所有干员（槐琥/令）；
  - `basis` = **计数基准**折算（见 §4.19），如 `power_count`（每有 1 间发电站）、
    `dorm_level`（当前宿舍每级）、`dorm_others`（每有 1 名其他干员）、`recruit_slot`（每个招募位）。
  - `boost_provider` / `boost_group` = **元修正**（M17）的点名槽：强化谁（持有者名）、
    强化它哪一组贡献（如 `dorm_group`）；目标筛选仍走 `condition`（见 §4.28）。
  - `template_id` = 分类模板（`M01`~`M17` / `X01`~`X11`，见 §5.5）；
  - `max_group` 非空 = **同组取最高**（轴 F3，官方术语 `cc.c.sui2_1`）；
  - `spread_whitelist=True` = 该技能是**扩散提供者**（玛恩纳公事公办）；
  - `partial` / `partial_mode` = 模板确定但条件槽空（`apply` 按骨架生效 / `hold` 保留骨架不生效）；
  - `untranslated=True` = **暂不生效**，等价于 `partial_mode="hold"`（note 保留原文待人工翻译）。
- **`SkillEquip`（干员↔技能的"解锁/提升"绑定）**：字段
  `unlock_elite / unlock_level / enhanced / replaces`。
  同一 skill_id 在不同干员身上解锁等级可能不同，故解锁信息**不属于 Skill 本身**。
- **`Operator.elite / level`**：精英化等级（0/1/2）与干员等级（1/30）。默认 elite=2（满练）。
  技能是否生效 = `op.elite >= equip.unlock_elite 且 op.level >= equip.unlock_level`。

### 5.2 阵营联动（"因其他阵营/干员存在而改变心情"）

三类联动技能，均已在数据中生效（阵营表 `skills.OPERATOR_FACTIONS` **由上游自动生成**）：

1. **阵营计数类（per-count，`count_faction`）**：`value` 是"每个该阵营干员"的回复量，
   总回复 = `value × 目标设施内该阵营干员数`。例：陈"德才兼备"（中枢内每个龙门近卫局干员 +0.05）、
   电弧"无言的慈爱"（宿舍内每个精英干员 +0.1）、异格者/彩虹小队/谢拉格/乌萨斯/鲤氏等。
2. **与具体干员共事（`condition`）**：`_cond_with_cc_operator(name)`（同中枢）、
   `_cond_with_facility_operator(name)`（同设施）。例：德克萨斯"恩怨"与拉普兰德同贸易站 +0.3、
   老鲤"浮生得闲"与阿同中枢、魔王"魔王传承"与阿米娅同中枢。
3. **与阵营共事（`condition`）**：`_cond_with_cc_faction(faction)`（同中枢、排除自身）。
   例：摆渡人"英雄的骄傲"与萨尔贡干员同中枢 +0.02。

> 阵营表 `OPERATOR_FACTIONS` / `FACTION_MEMBERS` **自动生成**，来源是上游
> `gamedata_const.json → termDescriptionDict` 的 `cc.g.*`（阵营）/ `cc.tag.*`（标签），
> 共 28 组 231 条；上游不列名单的（`cc.g.sp` 异格）放在 `resources/factions_supplement.txt`。
> 生成脚本：`python scripts/generate_factions.py --agd <ArknightsGameData>`。
> **禁止手工维护阵营表**（历史上那张手工表把米诺斯误标为萨尔贡、岁只收 3/7）。
> `generate_skills_data.py` 的 `check_faction_refs()` 会校验技能引用的阵营名都存在，对不上直接报错。

### 5.3 精英化判断（本项目的核心扩展）

1. **解锁（解锁）**：`enhanced=False` 的技能是独立技能，达到 `unlock_elite/unlock_level` 才生效
   （如巫恋"低语"精英2解锁）。
2. **β 替换 α（提升）**：`enhanced=True` 的技能是"精英化提升"版，其 `replaces` 指向被替换的
   低版本技能 id。同一干员同一 family 内，提升技能解锁后**替换**低版本、**不叠加**
   （如火神"工匠精神" α-0.15 → 精英2 β-0.25）。
3. **规则引擎**：`rules._active_skill_ids(op)` 一次算好"已解锁 + 未被替换 + 非待译"的技能集合，
   后续 `_skills_of(op, kind)` 全部基于它。要改判断逻辑只动 `rules.py`。

### 5.4 `SkillKind`（11 种）与 value 符号语义（极易错，请牢记）

| SkillKind | value 语义 | 叠加方式 | 现有哪些技能（节选） |
|---|---|---|---|
| `SELF_CONSUME` | 自身消耗增减：负=减耗，正=加耗 | 该干员自身求和 | 泡泡-0.25 / 火神α-0.15·β-0.25 / 阿罗玛+0.25 / 斥罪+0.5 / 夕"不以己悲"+0.5 |
| `FACILITY_CONSUME` | 同设施全体消耗增减（含自身） | 全体求和 | 黍-0.1 / 夕"不以物喜"中枢-0.05 / **巫恋"低语"+0.25**（含自身） |
| `ROOM_OTHERS_CONSUME` | 同设施**其他**干员消耗增减（不含自身） | 全体求和（跳过自身） | **当前无使用者**（低语已改判，见 §4.11） |
| `CC_REDUCE` | 中枢全局消耗减免（正=减免量） | 跨干员取最高 | 预留（真实数据中相关技能已归为 CC_RECOVER） |
| `CC_RECOVER` | 中枢提供的回复速率（恒正） | **F1 求和 + F3 同组取最高（`max_group`）+ 扩散** | 玛恩纳公事公办（room1+扩散15条）/ 维什戴尔巴别塔之帜 / 重岳孤光共照 / 冰酿笑靥如春 / 左膀右臂等 |
| `DORM_SELF` | 宿舍自身回复（恒正） | 同种取最高 | 菲亚梅塔+2.0（独占） |
| `DORM_GROUP` | 宿舍群体回复（恒正） | 同种取最高 | 刺玫+0.15 / 冰酿0.8（池分配） |
| `DORM_SINGLE` | 宿舍单体回复（恒正，锁定目标） | 同种取最高 | 使徒/慈悲/疗养等（大量真实技能） |
| `DORM_TARGETED` | 宿舍定向回复（恒正，满足条件） | 求和 | 刺玫低心情+0.1（mood<18）/ 净化呼吸（mood<20） |
| `ELIMINATE_SELF` | 消除他人自身消耗（value 恒 0） | — | 槐琥 / 令（限岁） |
| `DORM_META` | **元修正**：强化**他人**在宿舍的恢复效果（value = 增量，正） | 并入**被强化技能**的小计（轴 F2） | 摩根「头号陪练」→ 推进之王 +0.3（见 §4.28） |

`condition` 是 `Callable(SkillContext)->bool`，`SkillContext` 含 `world / owner / target / facility`；
条件函数用鸭子类型读 ctx，因此 `skills.py` 不 import `rules.py`（避免循环依赖）。

**新增/修正一条技能 = 改 `resources/*.txt` → 重跑生成脚本**；特殊机制无法用数据表达的，
才在 `rules.py` 里加解释逻辑（保持解释器通用、数据可生成）。

### 5.5 分类模板（六轴）—— 新增技能的"填表"入口

`mood_soc/skill_templates.py` 定义**六轴**：A 建模地位（`A1` 速率 / `A2` 事件 / `A3` 心情当条件 /
`A4` 非心情）、B 作用域、C 作用对象、D 效果、E 数值形态、F 叠加规则。
**模板 = 真实数据里用到的 (B,C,D,E,F) 组合**，ID 为 `M01`~`M17`（心情类）与 `X01`~`X11`（非心情，登记不建模）。

- **铁律**：模板挂在 **clause** 上（一个 buff 的多个子句可属不同族），故
  `moods_skills.txt` 是 clause 级、每行带 `template_id` + `params`；`skills_registry.txt` 是
  buff 级覆盖台账（上游全部 755 条，一行一条）。
- **「规则修饰」并入 F 轴**（同种取最高 / 特殊比较规则 / 归零 / 恒定 / 除以 / 优先生效）：
  它们只改"怎么合"、不改"改什么"，因此不是独立技能族。
- **零遗漏保证**：`scripts/classify_skills.py --check` 断言「模板全部命中 + 台账行数 == buff 总数 +
  无 `tier=??`」，任一不满足即退出码 1。新 buff 没归宿会**硬报错**而不是被静默忽略。
- 详细模板字典、判定关键词、待办缺口见 `resources/skill_taxonomy.md`。
- 引导死锁提醒：`scripts/*.py` **不能** `import mood_soc.skill_templates`（包 `__init__` 会连锁到
  `skills_data`，而它是生成物），故用 `importlib` 按文件加载（见脚本内 `_load_templates_module`）。

---

## 6. 数据流（一次完整测算怎么走）

```
用户输入（dict/JSON）
  └─ scenario.build_base_layout(data)          # 解析层：字符串/对象 → Operator/Facility/BaseLayout
       └─ 可选 main.py 内置 DEMO_SCENARIO 或 load_scenario(文件)
            ├─ single：rules.evaluate(world, name, hours)   → MoodResult
            └─ base：  rules.evaluate_base(world, hours)    → BaseResult
                 └─ battery.ampere_hour_integration         # 数学层：钳位积分
                      └─ output.mood/base_result_to_dict()  # 输出层：JSON dict
                           └─ output.dump_json() 写文件到 results/（CLI 同时打印到 stdout）
            （--trace，single 时）simulator.simulate(world, name, hours, step)
                          └─ 每步 rules.compute_net_rate 重算 → ampere_hour_integration 推进
```

技能数据管道（与测算主流程分离）：
```
Kengxxiao/ArknightsGameData  building_data.json（上游，非本仓库）
  └─ scripts/classify_skills.py --agd <仓库>     # 挂模板 + 生成覆盖台账 + 零遗漏校验
       ├─ resources/moods_skills.txt      （clause 级，+template_id/params）
       └─ resources/skills_registry.txt   （buff 级 755 行台账）
            └─ scripts/generate_skills_data.py
                 └─ mood_soc/skills_data.py（SKILLS / DEFAULT_OPERATORS / SKILL_EQUIPS /
                          TRAITS / SPREAD_SKILL_IDS / ROOM2_MAX_GROUP）
                      └─ skills.py re-export → rules.py 使用
```

**解析解 vs 数值解的区别（重要）**：
- `remaining_mood_after` / `remaining_work_hours` / `evaluate` / `evaluate_base` 是**解析解**：
  假设速率恒定（single 的"其余干员心情无限"、base 的"到第一个红脸前无人红脸"）。
  速度快、结果确定。
- `simulator.simulate` 是**数值解**：逐步推进，每步**重新计算所有干员**速率，
  因此能正确表现"有人中途红脸 → 他/她的技能失效 → 速率随之改变"的联动效果。
  它**深拷贝** world，不修改调用方对象；返回 `[(时间, 心情), ...]` 轨迹。

---

## 7. 如何运行与测试

```bash
# 内置演示（目标泡泡，8 小时）
python main.py --demo
python main.py --demo --target 斥罪 --period 8
python main.py --demo --target 泡泡 --trace          # 附心情轨迹
python main.py --demo --target 泡泡 --explain        # 打印心情流水账（为什么是这个速率）
python main.py --demo --target 菲亚梅塔 --entry-events  # 先结算**进驻事件**（M15a 患难之交心情互换）再测算
python main.py --demo --json-file                   # 额外把结果写入 results/ 下的 JSON 文件（默认只打印到 stdout）

# 自定义场景（single）
python main.py --scenario-file scenarios/demo.json --target 泡泡 --period 8
# base 模式：整个布局还能维持多久（无人红脸）
python main.py --mode base --scenario-file scenarios/demo.json
python main.py --mode base --demo --period 12    # 先推进 12h 再评估

# 全部测试（黑盒）
.venv/Scripts/python.exe -m unittest discover -s tests -v
# 只跑某个文件
.venv/Scripts/python.exe -m unittest tests.test_api_blackbox -v   # 公开 API 黑盒
.venv/Scripts/python.exe -m unittest tests.test_cli_blackbox -v   # 命令行黑盒
```

场景 JSON 格式（`scenario.py` 支持中文名或英文枚举作 `type`；干员可用字符串或对象）：

```json
{
  "facilities": [
    {"type": "控制中枢", "level": 1, "operators": ["玛恩纳", "维什戴尔", "魔王", "令", "路人"]},
    {"type": "制造站",   "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
    {"type": "办公室",   "level": 3, "operators": ["斥罪"]}
  ]
}
```

干员对象完整形态：`{"name": "x", "mood": 20.5, "skill_ids": ["..."], "trait": "岁", "elite": 2, "level": 1}`。
其中 `elite`（精英化等级，0/1/2，默认 2）与 `level`（干员等级，默认 1）控制技能解锁。

技能数据生成：`python scripts/generate_skills_data.py`（读 resources 两份 txt → 写 mood_soc/skills_data.py）。
MAA 排班转换：`python scripts/maa_to_scenario.py [源] [输出目录]`（默认读 `resources/arknights-infra-schedule-maa.json`，输出到 `scenarios/`）。

---

## 8. 关键"建模假设 / 近似"（不要当 bug 去"修复"）

文档未给出精确数值或机制时，代码做了显式简化并在注释中声明。改动前先确认是否要推翻这些假设：

1. **中枢全局减免线性折算**：文档只给"放满 5 人 = 0.25"一个锚点，代码按 `(人数/5)×0.25` 线性折算（`config.cc_reduction`）。若游戏真实规则是阶梯式，需改这里。
2. **条件槽空 → `partial`（保留效果骨架）**：模板化后不再"整条丢弃"。约 74 个子句的条件是
   MAA 占位符 / per-count 倍率 / 资源计数 / 截断文本，一律标 `partial=true`，并按
   `partial_mode` 处置：`apply`（子句 value 无条件成立 → 按骨架生效）/ `hold`（value 本身以缺失条件
   为前提 → 保留骨架但不生效，仍计入 `untranslated`）。判定：条件文本为空或含「额外」⇒ `apply`。
   实收：不生效子句 **37 → 31**（6 条本就正确的单体回复重新生效）；P3/P4a/P4b 又逐步降到 **3 条**
   （变量 + 可数基准 + 分支条件全部落地）。
   要彻底启用剩余子句，需实现 per-count 变量（人间烟火等）与 `COND_MARKERS` 扩充
   ——**这两项 P3/P4a 已完成，P5b 又落地了 M15a（患难之交，见 §4.29），当前仅剩 2 条 `hold`**
   （投资·α/β 需贸易站订单类型这类"环境事实"）。
3. **布局容量与房间数取自上游**：`config.FACILITY_MAX_COUNT`（`rooms[].maxCount`）与
   `FACILITY_SLOTS_BY_LEVEL`（`rooms[].phases[lv].maxStationedNum`）。`BaseLayout.validate()`
   只**报告**问题、不抛异常（历史场景可能刻意超容量）；`build_base_layout(validate=True)` 才抛。
4. **work_area 的 room1/room2 折算**：room2（"其他设施"）→ 全部 5 个工作设施；room1（"部分设施"，
   如玛恩纳"公事公办"）→ 发电/办公/会客。
5. **单体回复锁定规则简化**：文档的单体回复存在"进驻顺序/快照锁定"等复杂机制，代码简化为
   "锁定心情最低、未满、且非提供者的一名干员"（`rules._single_recovery`）。
6. **冰酿 0.8 分配语义**：按"心情未满成员平均分配"实现（`rules._icebrew_recovery`）。
7. **β 替换 α 的分组与粒度**：提升链按"同一干员 + 同一 skill_id 的 family"分组，
   组内按 (elite, level) 升序，`enhanced` 技能替换**前一个 skill_id**（整条，含全部分句）。
   family 取该技能**首个分句**的 family（同一技能的分句可能跨 family，如「挣脱」的 dorm_self / dorm_group）。
   ⚠️ 上游 `chars[].buffChar[]` 的**槽位结构**才是权威（同槽位多阶段 = 提升链），
   `audit_enhanced()` 会对照它校验 `enhanced` 列。
8. **数据管道幂等**（P4b 修）：`classify_skills.py --agd` 会重写 `moods_skills.txt` 的
   `template_id` / `params`。它现在**保留手写参数**（`basis=` / `var=` / `var_per=` / `var_min=`）
   与**已存在的人工判定**（`partial` / `partial_mode`），只给新行填初值。
   实测：重跑一次 `--agd`，259 行参数变化 **0** 行。
   ⚠️ 若没有这道保护，重跑分类器会把 P3/P4 手工回填的折算规则**全部抹掉**（已踩过）。
9. **阵营表已改为上游自动生成**（2026-09 重构）：`OPERATOR_FACTIONS` / `FACTION_MEMBERS` 由
   `scripts/generate_factions.py` 读上游 `cc.g.*` / `cc.tag.*` 生成（28 组 231 条），
   `TRAITS` 由阵营反推。**不要再手工加干员↔阵营**——改上游或 `factions_supplement.txt`。
   实测修正：旧手工表把「米诺斯」6 人（埃拉托/帕拉斯/铸铁/断罪者/火神/摆渡人）**误标为「萨尔贡」**，
   导致摆渡人「英雄的骄傲」在判断错的人；「岁」只收 3/7（漏 年/黍/余/望），
   令「杯莫停」因此少消除 4 名岁干员。两项均已修，`test_coop_with_faction_cc` 相应重写。
   ⚠️ 阵营名要用上游原名（早露的技能用「乌萨斯学生自治团」，不是「乌萨斯」）。

---

## 9. 对外公共 API 速查（`from mood_soc import ...`）

```python
INF, MoodBattery, ampere_hour_integration, to_decimal   # battery
FacilityType, MOOD_MAX, parse_facility_type             # config
BaseLayout, BaseResult, Facility, MoodResult,
Operator, OperatorResult                                # models
build_base_layout                                       # scenario
compute_net_rate, evaluate, evaluate_base, remaining_mood_after,
remaining_work_hours, time_to_mood, work_rest_ratio      # rules
consume_ledger, recovery_ledger, mood_ledger             # rules（流水账，可解释）
apply_entry_events                                       # rules（进驻事件，M15a；**就地改心情**）
Bucket, Contribution, MoodLedger                         # ledger（记录层）
VariableLedger, collect_variables, basis_count, mood_drop # variables（变量账本 + 可数基准）
FacilityType, unit 查询：of_type/count_of_type/all_dormitories/
  working_operators/base_operators/validate              # models（布局）
simulate                                                # simulator
# JSON 输出（不在 mood_soc 顶层，需显式导入子模块）：
from mood_soc.output import mood_result_to_dict, base_result_to_dict, dump_json
```

最小使用示例（入参建议传字符串或 `Decimal`；传 float 也会被 `to_decimal` 经字符串安全转换）：

```python
from decimal import Decimal
from mood_soc import build_base_layout, evaluate, evaluate_base, simulate
from mood_soc.output import mood_result_to_dict, base_result_to_dict

world = build_base_layout({"facilities": [
    {"type": "控制中枢", "level": 1, "operators": ["路人1","路人2","路人3","路人4","路人5"]},
    {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
]})
r = evaluate(world, "泡泡", period_hours=Decimal("12"))
# r.net_rate=Decimal('0.3'), r.remaining_mood=Decimal('20.4'), r.sustain_hours=Decimal('68')
#   （sustain_hours = 时段结束后剩余心情 20.4 / 0.3 = 68，而不是 24 / 0.3 = 80）
mood_result_to_dict(r, Decimal("12"))   # -> dict（JSON，数字按 6 位小数舍入）

b = evaluate_base(world)        # base：布局可维持时长
# b.layout_sustain_hours, b.bottleneck, b.operators（每个含 name/mood/sustain_hours/mood_at_end）
base_result_to_dict(b)          # -> dict（JSON；Infinity -> null）

traj = simulate(world, "泡泡", Decimal("12"), step=Decimal("0.1"))   # 深拷贝 world，返回轨迹
```

---

## 10. 快速核对表（改完代码自查）

- [ ] 数值常量是否都进了 `config.py`（没有在业务代码里硬编码魔法数字）？
- [ ] 技能数值是否来自 `resources/*.txt`（经 `generate_skills_data.py` 生成，未手写进 `skills.py`）？
- [ ] 新增技能 value 符号语义是否正确（消耗类负=减耗/回复类恒正），且 `unlock_elite/enhanced/replaces` 已设置？
- [ ] 是否遵守了叠加规则（同种取最高 vs 跨种相加 vs 定向求和 vs β替换α）？
- [ ] 精英化判断是否正确：解锁（elite/level 门槛）与提升（enhanced 替换 α）是否都覆盖？
- [ ] 心情是否在所有路径都钳位到 [0,24]？
- [ ] 红脸（mood≤0）技能失效是否处理？
- [ ] 依赖方向是否仍单向向下（无循环 import；skills.py 末尾才 re-export 数据）？
- [ ] 是否补了对应**黑盒测试**（`tests/test_api_blackbox` / `tests/test_cli_blackbox`，只断言输入→输出）并跑通全量 unittest？
- [ ] **是否已 `git commit`？**（每次改动一次提交；提交信息 1–15 字简要描述）
- [ ] 新增技能是否在 `moods_skills.txt` 填了 `template_id` + `params`（模板见 `resources/skill_taxonomy.md`）？
- [ ] `scripts/classify_skills.py --check` 是否通过（模板全命中 + 台账行数 == 上游 buff 总数 + 无 `tier=??`）？
- [ ] 叠加规则是否写进了轴 F（`max_group` / `spread` / `partial_mode`），而不是硬编码进 `rules.py`？
- [ ] 是否动了干员↔阵营？→ 只能改上游或 `resources/factions_supplement.txt` + 重跑 `generate_factions.py`，
      并确认 `check_faction_refs()` 通过（它会挡住对不上的阵营名）。
- [ ] 新增机制是否做成了「模板 + handler / 轴 F 参数」，而不是往 `rules.py` 塞 `max()` 或短路？
      （判据：`mood_soc/ledger.py` 的 `Stacking` 是否够用；不够才加分支）
- [ ] 新技能是否在**流水账**里能看到来源？→ `mood_ledger(world, "<干员>").explain()` 应列出
      owner / skill / template / 值 / 叠加规则。
- [ ] 动了 `enhanced` 列？→ 跑 `classify_skills.py --agd`，`audit_enhanced()` 会对照上游**槽位结构**
      校验提升链（同槽位多阶段 = 提升链；除最低阶段外都应 `enhanced=1`）。
- [ ] 新技能依赖变量？→ ①在 `resources/variable_producers.txt` 补产出端（注明上游出处）；
      ②在 `moods_skills.txt` 的 `params` 填 `var=<变量>;var_per=<N>` 或 `var=<变量>;var_min=<N>`；
      ③确认 `mood_ledger(...).explain()` 里能看到「变量」段与该条折算说明。
- [ ] 技能带「每有 N 个什么」的可数条件？→ 在 `params` 填 `basis=<基准名>`
      （可用基准见 `variables.BASIS_DOC`；新增基准要同时改 `basis_count`），并在测试里断言计数效果。
- [ ] 是否给技能挂了条件（`condition`）？→ **确认它所在的贡献循环有求值**：
      消耗侧（self / facility / room_others / cc_reduce / 消除）与回复侧（M07b 自身 / cc_recover /
      cc_dorm / dorm_self / dorm_group / dorm_single / dorm_targeted）都应有
      `if skill.condition is not None and not skill.condition(ctx): continue`。
      P4b 实测抓到 3 处循环漏求值（DORM_GROUP / DORM_SELF / 消除类）。
- [ ] 改了 `moods_skills.txt` 的 params 后，重跑 `classify_skills.py --agd` 是否仍保持不变
      （管道幂等）？参数变化应为 0 行。
- [ ] 是否新增了「基础 + 每有 N 额外」结构的技能？→ 确认两个分句的 `group` 相同
      （`SAME_KIND_MAX` 会按 skill_id 先求和再取最高）；若额外部分应**独立求和**而非参与取最高，
      则用不同的 `group`。
- [ ] 是否给技能挂了**按目标**的定向加成（「如果目标是 X」）？→ ①阵营/标签用
      `_cond_target_in_faction("…", …)`（可传多个 = 并集），点名干员用 `_cond_target_is("…")`；
      ②在 `CLAUSE_COND` 里按 `(skill_id, clause)` 挂上；③名字要能过 `check_faction_refs()`
      （阵营名对 `factions.txt`，干员名对 `operators.txt` 全量）；④**若挂的是 `dorm_single`，
      确认取值走 `MoodLedger.same_kind_winner` 而不是单条分句 `max`**（否则基础分句会把加成吃掉）。
- [ ] 新增技能是**元修正**（改他人的效果）？→ ①family=`dorm_meta`（→ `SkillKind.DORM_META`）、
      `template_id=M17`；②params 填 `boost_provider=<被强化的持有者>` 与 `boost_group=<被强化的组>`；
      ③目标筛选走 `CLAUSE_COND`（`_cond_target_in_faction` / `_cond_target_is`）；
      ④实现处必须把增量记成**同组同 skill_id 的额外贡献**（并入被强化技能的小计），
      **不能**记成独立技能——否则会被"同种取最高"当成竞争者丢掉。
- [ ] 新增的是**进驻事件**（"进驻那一刻"的一次性跳变，而不是每小时速率）？
      → ①`template_id=M15a`、`partial_mode=apply`；②条件挂 `CLAUSE_COND`；
      ③在 `rules.apply_entry_events` 里按模板调度、记 `Bucket.EVENT`；
      ④**不要**把它塞进 `consume_ledger`/`recovery_ledger`（那不是速率），
      也不要在 `evaluate` 里偷偷改世界——它是显式开关（`--entry-events`）。
- [ ] 新技能属于**尚未建模的设施**？→ ①在 `generate_skills_data.FACILITY_BY_PREFIX` 加
      `"<skill_id 前缀>": "<FacilityType>"`；②在 `classify_skills.MODELED_ROOMS` 里加上该 roomType
      （否则台账会继续标"设施未建模"）；③确认 `config.base_consumption` 有该设施的口径，
      **并且有出处**（docx 段落 / 上游字段），不要按"增量语义"猜。
- [ ] 是否手工往 `moods_skills.txt` **补过分句**（上游一个 buff 描述里有多个效果、本地 CSV 没拆）？
      → `classify_skills.py` 是**原地重写**、不重建行，故补的行会保留；但仍要跑一次 `--agd`
      确认参数变化 0 行，并在 `CLAUSE_COND` / 模板注释里写清上游原文出处。

---

## 11. 数据查找策略（**优先查上游仓库**）

> **策略（强制）**：遇到任何"不知道的数据"——技能原文 / 数值 / 解锁精英化与等级 /
> 阵营成员名单 / 设施集合定义 / 全局常量 / 机制术语——**先去
> [Kengxxiao/ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData) 查证**，
> 不要凭印象写、不要猜、不要从二手资料誊抄。
> 本项目 `resources/*.txt` 与 `mood_soc/skills_data.py` 都只是**上游的派生物**；
> 派生物与上游不一致时，**以上游为准**并修正派生物。

上游佐证过本项目多个关键规则（`cc.c.skill` 玛恩纳扩散白名单 15 条、`cc.c.sui2_1` room2 取最高、
`cc.c.room1~3` 设施集合、`cc.g.*`/`cc.tag.*` 阵营名册、`controlData.basicCostBuff = -5`
每人 -0.05、`dormData.phases[].manpowerRecover` 160~200）——**这些都是查出来的，不是推出来的**。

### 11.1 去哪儿查（按问题类型）

| 想知道什么 | 查哪个文件（相对 `zh_CN/gamedata/excel/`） |
|---|---|
| 干员有哪些基建技能、技能原文、数值、房间类型、解锁阶段 | `building_data.json` → `buffs`（755 条定义）+ `chars[].buffChar[].buffData[]`（`buffId` + `cond{phase,level}`） |
| 干员 id ↔ 中文名 | `character_table.json`（`charId` → `name`） |
| 术语含义、阵营/标签成员名单、设施集合、抽象变量、全局机制 | `gamedata_const.json` → `termDescriptionDict`（`cc.c.skill`、`cc.c.sui2_1`、`cc.c.room1~3`、`cc.g.*`、`cc.tag.*`、`cc.bd*`、`cc.bd.costdrop`…） |
| 中枢减免 / 宿舍基础回复 / 制造贸易人数修正 | `building_data.json` 顶层：`controlData.basicCostBuff`、`dormData.phases[].manpowerRecover`、`dormData.phases[].decorationLimit`、`manufactStationBuff`、`manufactManpowerCostByNum`、`tradingManpowerCostByNum` |
| 战斗技能 / 模组数值 | `skill_table.json` / `uniequip_table.json`（⚠️ 与**基建心情无关**，别混；模组只有剧情文案会提"心情"） |

### 11.2 怎么拉（**不要整仓 clone**，几百 MB）

```powershell
# 1) 无 blob 稀疏克隆：只取目录树，秒级完成
git clone --filter=blob:none --depth 1 --no-checkout https://github.com/Kengxxiao/ArknightsGameData <目标目录>
# 2) 只检出 excel 表（按需惰性下载）
git -C <目标目录> sparse-checkout init --cone
git -C <目标目录> sparse-checkout set zh_CN/gamedata/excel
git -C <目标目录> checkout
# 3) 记录版本（结论要可追溯）
git -C <目标目录> rev-parse HEAD
```

上游只保留 `zh_CN` 一个服（无 en/ja/ko）；`zh_CN/gamedata/excel/` 下 59 个 `.json`。
建议把克隆目录放在**本仓库之外**（如 `%TEMP%` 或项目同级），不要提交进本仓库。

### 11.3 必须知道的四个坑

1. **这是纯数据 dump，没有公式实现**。`zh_CN/gamedata/[uc]lua/` 只有 `hotfixes/*.lua` 片段，
   拿不到完整客户端逻辑。所以"心情消耗/回复/工休怎么算"仍以
   `resources/心情消耗回复和工休时间.docx` 为准；上游只提供
   **技能文本 / 数值 / 解锁 / 名册 / 集合 / 术语**。
2. **buffId 的中括号**：上游是 `control_mp_cost[008]`，本项目 CSV 是 `control_mp_cost_008`
   （`[` → `_`、`]` → 删）。已核对：**209/209 一一对应，无歧义**。
3. **β 替换 α 上游不标**：上游把 α、β 当作两个独立 buff，**没有 `replaces` 字段**。
   本项目 `operators.txt` 的 `enhanced` 列是自行推断的结果，别指望从上游直接读到。
4. **描述带富文本标记**：`<@cc.kw>12</>`（关键字）、`<$cc.c.room1>部分设施</>`（术语引用）、
   `<@cc.vup>+0.1</>`（增益）。清洗正则：
   `re.sub(r'</?(?:@cc\.\w+|\$cc\.\w+)>', '', s)`。

### 11.4 查完之后必须做的三件事

1. 修正 `resources/moods_skills.txt` / `operators.txt`（或对应的上游派生物）
   → 重跑 `scripts/classify_skills.py --agd <目标目录>`（新 buff 会落进覆盖台账）
   → 重跑 `scripts/generate_skills_data.py`
   → 跑全量 unittest（`.venv/Scripts/python.exe -m unittest discover -s tests`）。
2. 若该数据**推翻了既有假设**，同步更新 §8「建模假设/近似」或
   `resources/skill_taxonomy.md` §5「决策记录」，避免下次又被当成 bug 或又被猜一遍。
3. 结论要**可追溯**：注明取自哪个文件 + 字段名（+ 必要时 commit hash），
   而不是只写一个数值。
