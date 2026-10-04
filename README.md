# Rhodes-MoodSOC —— 干员"心情电池"建模工具

> **最新发布 `v1.1`**（2026-09-29）· 上一个版本 `v1.0`（2026-09-20）·
> 版本之间的差异、发布包与体检记录见 **`documents/16-现状与校准记录.md`**（GitHub Releases 从它摘取）。
>
> ⚠️ **未发布（2026-10）**：闲置入宿「三层解耦」已落地（代码与文档都已改），
> 尚未出包 —— 差异与发布口径见 `documents/16-现状与校准记录.md` 的「未发布」一节。

把《明日方舟》基建里干员的**心情**当成一块**电池**：`SOC(t) = SOC(t0) − ∫ I(τ)dτ`，
其中 `I = 消耗速率 − 回复速率`（`I>0` 下降 / `I<0` 上升 / `I=0` 不变），
心情全程钳位 `[0, 24]`，全程 `decimal.Decimal`。**纯 Python 标准库**，无第三方依赖（Python 3.9+）。

它回答两个问题：

- **single**：给定「干员 + 布局 + 目标时段」→ 该干员时段后的剩余心情，以及
  "其余干员心情无限"时她还能工作多久；
- **base**：给定「布局」→ 每个干员的心情，以及"没有一个干员红脸"还能维持多久。

**为谁服务**：`ArknightsInfraCalc-v3`（Rust）负责算"高效率的基建布局"，本项目负责**匹配那套布局**
并在项目支持的设施、技能和建模假设范围内生成它的**完整心情变化周期**。图形界面只是让人直观地看，**真正的交付面是 `api/`**：
v3 把求解结果内联喂进来（`load_json`），配好「换干员 / 闲置入宿 / 周期」三组口径，然后问
「**某人 + 时间节点** → 心情」（`operator_detail`）或「**时间节点** → 整座基地的布局 + 全员心情」（`layout_at`）。

> **细节都在 `documents/`**：本文件只讲「是什么 + 怎么跑 + 去哪看」。要改代码先读 `AGENTS.md`
> （AI 入口、最易踩的坑）与 `documents/04-特殊机制.md`（35 条特殊情况）。

---

## 30 秒上手

```bash
# 环境：Python 3.9+（仓库自带 .venv，里面是 3.14），无第三方依赖
.venv/Scripts/python.exe main.py --demo --target 泡泡 --period 8   # single：推进 8 小时
.venv/Scripts/python.exe main.py --mode base --demo                # base：整个布局还能撑多久
.venv/Scripts/python.exe -m ui                                     # 图形界面：看整周期
.venv/Scripts/python.exe -m api.cli --op capabilities              # 程序接口：一次性调用
```

`--demo --target 泡泡 --period 8` 的真实输出（**stdout 永远是纯 JSON**，错误与解释走 stderr）：

```json
{"mode": "single", "operator": "泡泡", "facility": "制造站", "period_hours": 8,
 "net_rate": 0.05, "state": "工作中", "mood": 23.6, "sustain_hours": 472}
```

**每条速率都能拆开看** —— 加 `--explain` 就是"为什么是这个数"的答案（下为节选）：

```
$ python main.py --demo --target 泡泡 --period 8 --explain
干员 泡泡（制造站）
消耗  合计 0.30
          1  [BASE] 基础消耗　（制造站 Lv3）
      -0.10  [BASE] 设施基础减免　（制造站 3 人：每多 1 人 -0.05，上限 0.1）
      -0.25  [BASE] 控制中枢全局减免　（中枢 5 人，每人 -0.05）
      -0.25  [M07a] 泡泡（自身）「囤积者」
回复  合计 0.25
       0.05  [M03] 玛恩纳「独善其身」　（玛恩纳「公事公办」扩散）
净速率 = 消耗 − 回复 = 0.05（>0 下降 / <0 上升）→ 每小时 0.05 点
```

---

## 核心口径（用之前必须知道这几条）

1. **`I = 消耗 − 回复`**，消耗末尾钳位 `≥0`；"净回复"一律走回复侧表达。
2. **红脸（心情 ≤ 0）→ 该干员所有心情类技能失效**（但仍可继续工作）。
3. **加工站 / 训练室是「挂件位」**：不计算心情消耗 —— 挂件永不红脸，它"人在基建内"的计数效果
   持续生效；**宿舍**只回复、**活动室**不进模型。见 `04-特殊机制.md` 第 14/15 条。
4. **「同种效果取最高」的比较单位是"技能"（含它的各分句）**，不是分句；β 替换 α 按 `skill_id`。
   见 `04` 第 4/5 条。
5. **每个技能都挂在模板上**（`M01`~`M17` 心情类 / `X01`~`X11` 非心情），上游 755 条 buff 全有覆盖台账，
   未归类会**硬报错**；新增技能 = 认模板 + 填参数。见 `05-技能分类大纲.md`。
6. **心情跨阈值会精确吸附**到 `{0, 12, 18, 20, 24}`（去掉除不尽的尾巴），且结果**与步长无关**。
7. **数值一律 `decimal.Decimal`**；"无限"是 `mood_soc.INF`，转 JSON 时为 `null`。
8. **多班排班**：位置每个**换班执行点**复位，**心情跨执行点/跨段跨周期连续**。`闲置入宿`默认**开**
   （三层解耦：**锁定入宿 > 自动入宿 > 导入布局**。自动入宿把"这一班的**原始布局**里完全没出现在
   任何设施、心情还没满"的人按竖向正序填进空床；全满后取**心情最低**的候选，换出"锁定区外、
   心情 **≥ 她**、心情最大"的住户；**不设班次数量门槛**，长班 >12h 在班内 12h 整数倍处还有
   **内部换班执行点**。**锁定入宿（旧称手动入宿）**（把某人钉在某位）走**设置中心「入宿设置」页的
   锁定入宿矩阵**（行＝位次 × 列＝班次；该页上半块＝闲置入宿的全局配置：总开关 / 锁定位置数 /
   黑名单）或「干员与心情」的位置列 —— 写进布局快照 + 手动台账，
   自动入宿不占那些位次、也不换那些人；**钉人时先把她从本班别的设施里摘掉**（所以她本班只在一处、
   工作位次空出）；她在「不在基建」名单里 ⇒ **先剔名单再写**（不再抛错））；
   `换干员`（进驻事件）默认**关**；**周期数上限 7**、周期默认取文件里的各班时长。
   见 `04-特殊机制.md` 第 30 条、`10-图形界面.md`。

---

## 使用方法

### 命令行

| 参数 | 取值 | 默认 | 说明 |
|---|---|---|---|
| `--mode` | `single` / `base` | `single` | 算一个干员 / 算整座基地 |
| `--demo` | flag | 关 | 用 `main.py` 内置的 `DEMO_SCENARIO` |
| `--scenario-file` | 路径 | 无 | 从场景 JSON 读取（优先级低于 `--demo`） |
| `--target` | 干员名 | 无 | single 必填（`--demo` 不会替你补） |
| `--period` | 小时（小数） | `0` | 目标时段；缺省只看当前状态 |
| `--json-file` / `--out-dir` | flag / 目录 | 关 / `results` | 额外把结果写成 JSON 文件 |
| `--trace` | flag | 关 | single：附加心情轨迹（走 stderr） |
| `--entry-events` | flag | 关 | 先结算**进驻事件**（进驻那一刻换心情）再测算 |
| `--explain` | flag | 关 | 打印心情流水账（见上文示例） |

约定：**stdout 永远是纯 JSON**（便于程序解析），`--period` 缺省 0、`--target` 在 single 下必填。
`--mode base` 的输出含 `layout_sustain_hours`（还能撑多久）与 `bottleneck`（最先红脸的人），
每个干员带 `mood` / `sustain_hours` / `mood_at_end`。

### 场景 JSON

```json
{
  "facilities": [
    {"type": "控制中枢", "level": 1, "operators": ["玛恩纳", "维什戴尔", "魔王", "令", "路人"]},
    {"type": "制造站",   "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
    {"type": "办公室",   "level": 3, "operators": ["斥罪"]}
  ],
  "entry_events": {"enabled": true, "swap_with": "路人"}
}
```

- `type` 支持中文名或枚举值（`control_center` / `manufacturing` / …），全表见 `01-架构.md` §9.7；
- 干员可写名字（自动套用内置技能，默认满练 `elite=2, level=30`），也可写对象
  `{"name": "x", "mood": 20.5, "elite": 2, "level": 30, "skill_ids": [...]}` —— 练度决定技能解锁；
- 顶层可选：`entry_events`（进驻事件）、`idle_to_dorm`、`detached`（「不在基建」名单）、
  `initial_global`（变量初值）、`initial_moods` / `mood_events`（按时刻指定的心情锚点）、
  `label`（班次名）、`hours`（班次时长）。**四种导入格式**（本工具 / MAA / v3 求解输出 / v4 蓝图+干员池）
  的识别与字段对照见 `10-图形界面.md` §2；后四个键是 **2026-10 起"导出产物自带"** 的
  （`export_schedule` 把它们写进每班 `scenario` 正文）⇒ 逐班存盘再导入，**设置与班次名/时长都逐字段不变**；
  ⚠️ 老场景文件没有 `label`/`hours` ⇒ 行为**一字不变**（班次名仍取文件名、时长仍按班次均分）。

### 图形界面（`python -m ui`）

导入排班 → 看板每个位置显示干员与**实时心情** → 拖时间滑块看整周期 → 指定干员看曲线。
工具栏三组：`导入排班… 设置… 周期数▾ │ 状态摘要 │ ▶播放`；「设置…」是唯一设置入口，四个分区
（**时间轴 / 干员与心情 / 入宿设置 / 换心情**），**改完立即生效**，重算在后台线程跑（编辑不冻界面）；
关窗口会把在算的工作线程收干净（GC 只在主线程跑），不会留后台残响。
面板口径、看板标记、锁定入宿矩阵的完整说明见 `documents/10-图形界面.md`。

### 程序接口（给别的程序 / Rust 调用）

```bash
.venv/Scripts/python.exe -m api.server                 # 常驻：NDJSON over stdio（推荐）
.venv/Scripts/python.exe -m api.cli --op capabilities  # 一次性
```

```jsonc
{"id":1,"op":"capabilities","args":{"expected_protocol":5}}   // 先握手：协议版本 5 + op 表
{"id":2,"op":"load_json","args":{"data": /* v3 求解结果原样塞进来 */ }}
{"id":3,"op":"layout_at","args":{"at":8}}                        // 整座基地的布局 + 全员心情
{"id":4,"op":"operator_detail","args":{"name":"但书","at":8}}     // 某人此刻的心情
{"id":5,"op":"closure","args":{"cycles":3}}                      // 这套布局能不能长期跑
```

共 **38 个 op**（载入 / 改设置 / 出结果 / 只读），与图形界面共用同一个 `Session` ⇒ 两条路数值**逐位一致**。
时刻支持 `8.5`（绝对小时，跨周期递增）或 `"08:30"`（周期内时刻）。字段表、错误形状、加 op 的步骤见
`documents/11-程序接口.md`；给 v3 侧照抄的一页纸见 `documents/11-程序接口.md`。

### 打包成 exe

```bash
.venv/Scripts/python.exe -m pip install pyinstaller   # 构建期工具，只需一次
.venv/Scripts/python.exe scripts/build_exe.py         # → dist/RhodesMoodSOC/RhodesMoodSOC.exe
dist/RhodesMoodSOC/RhodesMoodSOC.exe --smoke          # 自检：退出码 0 ＝ 窗口建得起来
```

**exe 里不带任何附加数据**：`scripts/`、`documents/` 与 `resources/` 整目录
都不打进去（只带 Python 运行时 + tkinter + 项目代码 + `data/operators.txt`）⇒ 双击后是**空界面**，
用「导入排班…」载入你自己的文件。见 `documents/07-设计史.md` §9.6。

---

## Python API

```python
from decimal import Decimal
from mood_soc import build_base_layout, evaluate, evaluate_base, compute_net_rate

world = build_base_layout({"facilities": [
    {"type": "制造站", "level": 3, "operators": ["泡泡", "黍", "路人甲"]},
    {"type": "宿舍",   "level": 5, "operators": [{"name": "菲亚梅塔", "mood": "10"}]},
]})
r = evaluate(world, "泡泡", period_hours=Decimal("12"))     # single
print(r.net_rate, r.remaining_mood, r.sustain_hours, r.state)
b = evaluate_base(world)                                    # base
print(b.layout_sustain_hours, b.bottleneck)
```

最常用的几个：`build_base_layout` / `evaluate` / `evaluate_base` / `compute_net_rate` /
`remaining_mood_after` / `simulate` / `work_rest_ratio` / `mood_ledger`（流水账，`.explain()` 可读）/
`apply_idle_to_dorm` / `apply_entry_events`。

**完整签名表、结果 dataclass 字段、输出层 / 数据层入口、`FacilityType` 全表**见 `documents/01-架构.md` §9。

---

## 目录结构（一屏）

```
data/        要 import 的表（技能库 / 台账 / 阵营 / 生成物）+ 路径出口 data/paths.py
mood_soc/    纯计算：心情电池、技能规则、流水账、变量账本
store/       状态与 IO：排班 / 轨迹 / 解析 / 序列化 / 会话（Session，界面与 api 共用）
ui/          tkinter 图形界面           api/  程序接口（NDJSON，JSON 进 JSON 出）
resources/   仓库级数据：样例 JSON、数据字典、核对报告（exe 不带）
documents/   文档（见下表）             scripts/ 数据管道与打包
```

分层**严格单向**：`data/` ← `mood_soc/` ← `store/` ← `ui/` `api/`（`ui/` 与 `api/` 互不 import）。
完整目录树、数据流与依赖规则见 `documents/01-架构.md`。

---

## 文档导航

| 想了解 | 看哪份 |
|---|---|
| 架构 / 目录 / 数据流 / **公共 API 速查（含 `FacilityType` 全表）** | `01-架构.md` |
| 数值规则：心情⇔电池、`I` 的完整构成、设施表、宿舍回复、工休比 | `02-数值规则.md` |
| 技能系统：Skill / 精英化 / 阵营联动（§5） | `02-数值规则.md` §5 |
| 技能分类：六轴模板、M/X 模板字典、逐条决策记录 | `05-技能分类大纲.md` |
| **35 条特殊情况 + 12 条建模假设**（改代码前必读） | `04-特殊机制.md` |
| 数据从哪来（强制查上游）、上游缺哪些字段、上游仓库结构留档 | `06-数据来源.md` |
| 重构与性能优化史（P1~P9，含"试过但放弃"的负结果） | `07-设计史.md` |
| 怎么跑、性能基线、打包 exe、改完自查清单 | `07-设计史.md` 附「开发指南」 |
| 图形界面：看板 / 曲线 / 设置中心 / 锁定入宿矩阵 | `10-图形界面.md` |
| 程序接口：38 个 op、字段表、错误形状、加 op 步骤 | `11-程序接口.md` §1~§9 |
| **v3（Rust）接入**：调用序列、三组口径、踩坑 | `11-程序接口.md` §10~§18 |
| 要动架构 / 拆模块之前的现状测绘 + 病灶清单 + 四张架构图 | `14-架构总览.md` |
| **现状快照 + 已知未修项**（改代码前扫一眼）、版本记录与打包口径 | `16-现状与校准记录.md` |

## 维护约定

改完代码 / 数据 / 结构后**同步文档**（本文件 + `AGENTS.md` + 受影响的 `documents/*.md`），
并且**每完成一件事就 `git commit`**（提交信息 1–15 个字，一次提交一件事）。
**完整四条（含"根 `AGENTS.md` 保持精简"与"文档按门类分文件"）见
`documents/README.md` 的「维护约定」一节**——那是文档维护的正式落点，这里不重复。
