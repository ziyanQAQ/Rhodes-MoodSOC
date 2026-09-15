# AGENTS.md —— 给 AI 的项目入口

> **维护约定（必读）**
>
> 1. **改完东西要同步文档**：代码 / 数据 / 文档 / 结构 / 数值规则任一改动后，
>    至少更新 `README.md` 与本入口，并按门类更新 `documents/` 下对应文档。
> 2. **提交约定**：每完成一次修改就**立即 `git commit`**，提交信息 **1–15 个字**
>    （如「修复替换链」「补变量账本」），不写多段说明、不加 `feat:`/`fix:` 前缀；一次提交一件事。
> 3. **本文件保持精简**：它有 **65536 字节指令预算**，超了会被截断、尾部读不到
>    ——"读不到的文档等于不存在"。细节写进 `documents/`，这里只留指针。
>    新增内容前先自查：`(Get-Item AGENTS.md).Length`。
> 4. **先方案、后动手**（用户要求，硬约束）：任何**实施动作**——改代码 / 数据 / 文档 /
>    结构 / 数值规则——**先给具体方案，等用户抉择后再实施**，不要顺手就改。
>    方案一律写成固定小卡片，按此顺序：
>    ① **要改什么** → ② **改哪些文件** → ③ **行为 / 数值怎么变** →
>    ④ **影响与验证** → ⑤ **要你拍板的选项**（每项都给**推荐项 + 推荐理由**，
>    能用选择框就用选择框）。
>    **不在此限**：只读调查、查上游数据、解释原理、回答问题——这些直接答。
>    用户说"直接做 / 不用问"时，该次豁免此约束。
>
> **本入口只讲"是什么 + 去哪儿看"。详细规则一律在 `documents/`。**

---

## 0. 一句话概括

**纯 Python 标准库**的建模工具，把《明日方舟》基建中干员的**心情**抽象成一块**电池**，
用 **BMS-SOC 安时积分法（库仑计数）** 建模，回答两个问题：

> - **single**：输入「干员 + 基建布局 + 目标时段」→ 该干员剩余心情 + 其余干员心情无限时还能工作多久；
> - **base**：输入「基建布局」→ 每个干员的心情 + 该布局"没有一个干员红脸"可维持的最长时长。

核心公式：`SOC(t) = SOC(t0) − ∫ I(τ)dτ`，其中 **`I = 消耗速率 − 回复速率`**
（`I>0` 下降 / `I<0` 上升 / `I=0` 不变），心情全程钳位 `[0, 24]`，全程 `decimal.Decimal`。

---

## 📚 文档索引（按门类）

```
documents/
├── README.md              ← 文档索引 + §编号约定 + 维护约定（先看这个）
├── 01-架构.md              分层 / 目录结构 / 数据流 / 解析解vs数值解 / 公共 API
├── 02-数值规则.md          心情⇔电池 / I 的完整构成 / 设施表 / 宿舍回复 / 工休比
├── 03-技能系统.md          Skill / SkillEquip / SkillKind / 精英化 / 阵营联动 / 模板入口
├── 04-特殊机制.md          ★ 33 条特殊情况 + 9 条建模假设（改代码前必读）
├── 05-技能分类大纲.md       六轴 + 模板字典 M01~M17 / X01~X11 + 决策记录
├── 06-数据来源.md          ★ 数据查找策略（强制）+ 上游缺失清单
├── 07-设计史.md            架构诊断 + P1~P5 重构决策记录
├── 08-上游数据源分析.md     上游仓库结构分析（首次摸底留档）
├── 09-开发指南.md          运行与测试 / 公共 API 速查 / 改完代码自查清单
└── 10-图形界面.md          图形界面 ui/：导入多班排班 / 时间滑动 / 对点曲线
```

**目录分工**：`mood_soc/` `scripts/` `tests/` `main.py` `ui/` = **代码**；
`resources/` = **数据**（`.docx` 需求文档 / `.txt` 技能与阵营表 / `.json` 样例）；
`documents/` = **文档**；`scenarios/` = 示例场景。

> **§编号约定**：`§4.16` / `§8.5` / `§11` 之类引用沿用原 `AGENTS.md` 的**稳定章节号**，
> 换算表见 `documents/README.md`。代码注释里也会出现这些引用。

---

## 🔎 数据查找策略（**强制**）

> 遇到任何**不知道的数据**——技能原文 / 数值 / 解锁精英化与等级 / 阵营成员名单 /
> 设施集合定义 / 全局常量 / 机制术语——**先去上游仓库查证**：
> [**Kengxxiao/ArknightsGameData**](https://github.com/Kengxxiao/ArknightsGameData)
> （`zh_CN/gamedata/excel/`）。**不要凭印象写、不要猜、不要从二手资料誊抄。**
> 本项目 `resources/*.txt` 与 `mood_soc/skills_data.py` 都只是**上游的派生物**，
> 不一致时**以上游为准**并修正派生物。
>
> 完整流程 / 四个坑 / 查完之后的三件事 / **哪些数据上游根本没有** →
> **`documents/06-数据来源.md`**。

---

## 🚨 最容易踩的坑（全文见 `documents/04-特殊机制.md`）

1. **`I = 消耗 − 回复`**；消耗末尾钳位 `≥0`，"净回复"一律走回复侧表达。
2. **红脸（心情 ≤ 0）→ 该干员所有心情类技能失效**（但仍可继续工作）。
3. **加工站 / 训练室是「挂件位」，不计算心情消耗**——那两个位置只放挂件
   （挂件本身没技能，**人在基建内**（不含副手与活动室使用者）就能为别人提供效果），
   所以挂件永不红脸、效果持续生效；9 条训练室「心情每小时消耗 +1」随之不生效。
   **活动室**同理不进心情模型（上游：不视作入住在基建内）。
4. **「同种效果取最高」的比较单位是「技能」（含它的各分句），不是分句**——
   先按 `skill_id` 把分句求和，再跨技能取 max。见 §4.18。
5. **β 替换 α 的单位是 `skill_id`，不是 `skill_id#clause`**——否则基础分句会把自己的分句"替换"掉。
6. **`_single_recovery` 的目标锁定是"全局一名受益者"**（简化），加"按目标"的加成时必须
   走 `MoodLedger.same_kind_winner`，不能自己写 `max()`。
7. **条件必须被求值**：新增条件分支时，确认它所在的贡献循环里有
   `if skill.condition is not None and not skill.condition(ctx): continue`（曾漏 3 处）。
8. **模板挂在 clause 上**：`moods_skills.txt` 是 clause 级、每行带 `template_id` + `params`；
   `skills_registry.txt` 是 buff 级 755 行覆盖台账。
9. **数值一律 `decimal.Decimal`**，外部输入走 `to_decimal()`（经字符串，禁止 `Decimal(float)`）。
10. **技能数值不要手写进 `skills.py`**：改 `resources/*.txt` → 重跑生成脚本。
11. **改完跑全量黑盒测试** `.venv/Scripts/python.exe -m unittest discover -s tests`（当前 197 个全绿），
    并 `scripts/classify_skills.py --check`（模板全命中 + 台账行数 == 上游 buff 数）。

---

## ⚡ 最常用命令

```bash
# 测算（single 模式必须给 --target；--period 缺省 0）
python main.py --demo --target 泡泡                    # 内置演示，目标泡泡
python main.py --demo --target 泡泡 --period 8         # 推进 8 小时
python main.py --scenario-file scenarios/demo.json --target 泡泡 --period 8
python main.py --demo --target 泡泡 --explain           # 打印心情流水账（为什么是这个速率）
python main.py --demo --target 菲亚梅塔 --entry-events  # 先结算进驻事件（M15a 心情互换）
python main.py --mode base --demo                      # 整个布局还能维持多久
python main.py --mode base --demo --period 12          # 先推进 12h 再评估

# 图形界面（纯标准库 tkinter；导入多班排班 / 时间滑动 / 对点曲线 / 批量设置）
.venv/Scripts/python.exe -m ui                          # 见 documents/10-图形界面.md
.venv/Scripts/python.exe ui/__main__.py                 # 等价；IDE 里直接 Run 也行

# 数据管道（改完 resources/*.txt 必须按顺序跑）
python scripts/classify_skills.py --agd <ArknightsGameData>   # 挂模板 + 生成 755 行台账
python scripts/generate_skills_data.py                        # 生成 mood_soc/skills_data.py
python scripts/classify_skills.py --check                     # 零遗漏校验（CI 用，不需要仓库）

# 测试
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

> 上游仓库**不要整仓 clone**（几百 MB），用无 blob 稀疏克隆，命令见 `documents/06-数据来源.md`。
> 克隆目录放**本仓库之外**。

---

## 🔁 一次测算怎么走（细节见 `documents/01-架构.md`）

```
用户输入（dict/JSON）
  └─ scenario.build_base_layout(data)        # 解析层 → Operator/Facility/BaseLayout
       ├─ single：rules.evaluate(world, name, hours)   → MoodResult（含 ledger）
       └─ base：  rules.evaluate_base(world, hours)    → BaseResult
            └─ output.*_to_dict() → JSON（inf → null）
       （--trace）simulator.simulate(...)      # 数值解：每步重算速率，能表现"红脸后技能失效"联动
```

技能数据管道（与测算主流程分离）：

```
Kengxxiao/ArknightsGameData  building_data.json（上游，非本仓库）
  └─ scripts/classify_skills.py --agd    # 挂模板 + 覆盖台账 + 零遗漏校验
       ├─ resources/moods_skills.txt        （clause 级，+template_id/params）
       └─ resources/skills_registry.txt     （buff 级 755 行台账）
            └─ scripts/generate_skills_data.py → mood_soc/skills_data.py（勿手改）
                 └─ skills.py re-export → rules.py 使用
```
