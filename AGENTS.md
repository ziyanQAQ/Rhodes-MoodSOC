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

**为谁服务（定位）**：`ArknightsInfraCalc-v3`（Rust，`E:\code_h\ArknightsInfraCalc-v3`）
算"高效率的基建布局"，本项目**匹配那套布局**并生成它的**完整心情变化周期**；
图形界面只负责让人直观地看，**真正的交付面是 `api/`**：v3 把求解结果内联喂进来
（`load_json`），配好「换干员 / 闲置入宿 / 周期」三组口径，然后问
「某人 + 时间节点 → 心情」（`operator_detail`）或「时间节点 → 整座基地的布局 + 心情」（`layout_at`）。
**默认口径已定**：换干员**默认关**（手动配置）、闲置入宿**默认开**（本项目四级优先级）、
周期**默认取文件里的各班时长**。见 `documents/12-v3接入.md`。

---

## 📚 文档索引（按门类）

```
documents/
├── README.md              ← 文档索引 + §编号约定 + 维护约定（先看这个）
├── 01-架构.md              分层 / 目录结构 / 数据流 / 解析解vs数值解 / 公共 API
├── 02-数值规则.md          心情⇔电池 / I 的完整构成 / 设施表 / 宿舍回复 / 工休比
├── 03-技能系统.md          Skill / SkillEquip / SkillKind / 精英化 / 阵营联动 / 模板入口
├── 04-特殊机制.md          ★ 35 条特殊情况 + 12 条建模假设（改代码前必读）
├── 05-技能分类大纲.md       六轴 + 模板字典 M01~M17 / X01~X11 + 决策记录
├── 06-数据来源.md          ★ 数据查找策略（强制）+ 上游缺失清单
├── 07-设计史.md            架构诊断 + P1~P8 重构决策记录
├── 08-上游数据源分析.md     上游仓库结构分析（首次摸底留档）
├── 09-开发指南.md          运行与测试 / 公共 API 速查 / 改完代码自查清单
├── 10-图形界面.md          图形界面 ui/：导入多班排班 / 时间滑动 / 对点曲线
├── 11-程序接口.md          ★ api/：NDJSON 常驻服务 + 一次性 CLI + 求解器适配面
└── 12-v3接入.md            ★ v3（Rust）侧接入：调用序列 / 三组配置 / 默认口径 / 踩坑
```

**分层（依赖严格单向向下，`tests/test_layers.py` 静态扫描盯着）**：
`ui/`（tkinter 视图）与 `api/`（程序接口，JSON 进 JSON 出）→ `store/`（状态与 IO：
排班/轨迹/解析/序列化/**会话**）→ `mood_soc/`（纯计算）→ `data/`（数据 + 领域基元 + 路径出口）。
`data/paths.py` 是**资源路径的唯一出口**；`data/` 与 `mood_soc/` 不许 import 上层；
`ui/` 与 `api/` 互不 import；`api/` `store/` `data/` 的代码里不许出现 `tkinter`。
**数据分两条线**：`data/` = 要 import 的表（技能库 / 台账 / 阵营 / 生成物）；
`resources/` = 仓库根的项目级数据（样例 JSON、数据字典说明、核对报告、需求文档 docx）。
`documents/` = **文档**；`scenarios/` = 示例场景。

> **§编号约定**：`§4.16` / `§8.5` / `§11` 之类引用沿用原 `AGENTS.md` 的**稳定章节号**，
> 换算表见 `documents/README.md`。代码注释里也会出现这些引用。

---

## 🔎 数据查找策略（**强制**）

> 遇到任何**不知道的数据**——技能原文 / 数值 / 解锁精英化与等级 / 阵营成员名单 /
> 设施集合定义 / 全局常量 / 机制术语——**先去上游仓库查证**：
> [**Kengxxiao/ArknightsGameData**](https://github.com/Kengxxiao/ArknightsGameData)
> （`zh_CN/gamedata/excel/`）。**不要凭印象写、不要猜、不要从二手资料誊抄。**
> 本项目 `data/*.txt` 与 `data/skills_data.py` 都只是**上游的派生物**，
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
8. **模板挂在 clause 上**：`data/moods_skills.txt` 是 clause 级、每行带 `template_id` + `params`；
   `data/skills_registry.txt` 是 buff 级 755 行覆盖台账。
9. **数值一律 `decimal.Decimal`**，外部输入走 `to_decimal()`（经字符串，禁止 `Decimal(float)`）。
10. **技能数值不要手写进 `skills.py`**：改 `data/*.txt` → 重跑生成脚本（生成物 `data/*_data.py` 勿手改）。
11. **改完跑全量黑盒测试** `.venv/Scripts/python.exe -m unittest discover -s tests`（当前 454 个全绿），
    并 `scripts/classify_skills.py --check`（模板全命中 + 台账行数 == 上游 buff 数）。
12. **改了技能数据就跑技能全量核对** `scripts/verify_skills.py --check`（**四层**：
    L1 模板自洽 / L2 250 条 clause 逐条造场景核对 / L3 上游 755 条台账双向核对 + 描述数字对照 /
    **L4 178 名干员真名生效 + 48 条组合技能双向对照**；`--report` 重写 `resources/skill_verify_report.md`）。
    ⚠️ L2 用的是**合成干员 + 手工注入 `skill_id`**，L4 才走"真名 → 技能槽 → 进流水账"；
    ⚠️ 验组合技能（共事/定向/阵营/变量/元修正）时，**"撤掉搭档"要改成"改名/摘阵营、人留在原房间"**
    —— 控制中枢有「N 人每人 −0.05」的全局减免，搬走人会污染差值（曾误判成"引擎多算一倍"）。
    详见 `documents/05-技能分类大纲.md` §5.11。
13. **「导入排班」认 4 种 JSON**（本工具场景 / MAA / **v3 求解输出** / **v4 蓝图+干员池**），
    识别与转换只有一处：`store/sources.py`（别再在 `ui/` 里写第二份格式判断）。
    合同与字段对照见 `documents/10-图形界面.md` §2。
    **按文件（`load_file`/`load_files`）与内联（`load_json` / `Session.load_data`）走同一条装配路**
    （`store.schedule.load_schedule_from_imports`）——同一份 JSON 两条入口必须逐位相同；
    `load_json` 是**给 v3 的适配入口**，默认口径见 `documents/12-v3接入.md`（换干员不继承文件开关、
    闲置入宿默认开）。
14. **「干员与心情」的心情列＝"指定时刻那一刻的实际心情"**：改格子写的是**心情指定事件**
    （`store.schedule.MoodSetEvent`，只对指定周期生效、同刻跳变），只有**第 1 周期 0:00** 那一格
    写 `initial_moods`。面板的 `_collect_moods` **只收"和刚写进去的值不同"的格子**
    （否则"改个房间等级"会把从轨迹读出来的值当成手动设定重复落一遍）。
    ⚠️ **`StringVar.trace_add("write")` 对程序化 `var.set()` 也触发**：刷新必须走 `_set_cell`
    （**先登记 `_shown` 再写**）→ `_on_cell_write`（与 `_shown` 相等就 return）→ `_notify`
    的结果签名去重，三条缺一条就会变成"刷新→重算→刷新"的**自激回路**（实测空闲 CPU ~75%、
    空转 2 秒重算 6 次 —— 就是"设置干员与心情卡顿严重"）。见 `documents/10-图形界面.md` §7.1 第 9 条。
15. **同刻跳变＝同一时刻两个节点**（跳变前 / 跳变后）：`_record_jump` 是**追加**而不是改写；
    对应地 `Trajectory.mood_at` **不能**用 `if t <= ts[0]: return vals[0]` 提前返回
    （`t = 0` 恰好就是"周期起点被事件改过"，会取到跳变**前**的值——修过的 bug）。
16. **`Schedule.start_clock`（初始时间点）只改显示**：所有时刻标签都带这个偏移
    （`theme.fmt_clock(..., offset=)`），**引擎数值一字不变**；别在引擎里用它做任何计算。
17. **「不在基建」的人（既不在工作设施、也不在宿舍）** = 净速率 0、心情**一条平线**，
    且**不参与任何技能计数**（不在 `facilities` 里）。两个来源：本班未排班（自动）、
    排班 JSON 顶层 `"detached": [...]` / 面板「＋ 添加干员…」（显式）。
    ⚠️ **不变式**：写进名单的人会被自动从所有班次的位置上摘掉（别让她一边在名单、一边占位）；
    ⚠️ "本班未排班" ≠ "整份排班都不在基建"——她在别的班有活，那些班照常算（面板 `—` 列写着
    `其他班：2中 3宿`）。见 `documents/10-图形界面.md` §5 第 14 条。
    **闲置入宿**（把没满的闲置干员塞进宿舍）的**四级优先级**：① 有未占满的位次就直接住进去
    → ② 全满才换「宿舍 **#4→#3→#2** 的第 **2~5** 位」里实时满心情的那位 → ③ 兜底层**只换白板**
    （不属于任何已记录阵营的干员），**＋满 24 的菲亚梅塔**（她「患难之交」只要满 24、不靠待在宿舍，见 `rules.TIER3_EXTRA_NAMES`）
    → ④ ②③ 都挑不到：**点名了（`swap_with`）就先与那位互换**（**主动换**：不限心情、不受三道门限制）；
    没点名则与宿舍里**心情最高**的**白板**互换（**不限 24**，最高 21 就跟 21 那位换）；连白板都没有才不动。同优先级内
    "**优先 4 最后 1**"；宿舍序号取自**未排序**的布局顺序，拿排序后的下标当序号会换错房间（踩过）。
    **三道门（只约束自动挑人，②③④ 都算）**：只换白板 / 不换"宿舍里能自回"的人（缪尔赛思这类）/
    **不换"挂件"** —— 用户口径"**挂件＝有阵营效果，或者她在不在宿舍会影响其他干员的技能**"：
    `rules._is_pendant(world, name, memo)` 现场判**"把她从宿舍摘掉后全基建有人净速率变差"**
    （只看变差：她走了别人反而分得更多那类池分摊**不算**），另含加工站/训练室/副手那种位置挂件；
    判据跑在浅拷贝探针上、按班次缓存（一次 ≈2.2ms / 47 人）。代价是能接纳的闲置者变少。
    **心情闸（含点名）**：任何互换都要求**目标的实时心情严格大于候选**（用户口径"换的时候比较
    此干员与目标干员的心情，如果目标干员心情大于此干员才交换"）—— 不满足就"这一班不动"并写明两边心情；
    ②③ 的目标是实时满 24、候选必定 <24 ⇒ 天然满足，**实际只在 ④ 生效**（实测示例：梅 19.5↔温蒂 12.3、
    幽灵鲨 20.1↔温蒂 12.3 被拦下 ⇒ 温蒂留下回满 24、幽灵鲨留在外面 20.1）。
    **点名只在 ④ 生效**（①②③ 能挑到人时点名不生效，但它优先于④的默认兜底）；
    界面上「换谁」列的是**宿舍里的所有人**（标签带心情如 `巫恋 23.4`，由 `store.session.idle_target_name` 剥标签）。顺序上**先换心情、再判闲置入宿**，
    判定用**实时**心情，候选还要过 `world.facility_of` 校验（跑过一轮的副本里会残留已离开宿舍的陈旧对象）。
    常量 `rules.DORM_PREFERRED_RANGE` / `DORM_PREFERRED_SLOTS` / `TIER3_EXTRA_NAMES`，门 `rules._factionless` /
    `rules._self_recovering_in_dorm`，见 `documents/04-特殊机制.md` 第 30 条。
18. **分层别搞反**：`data/`（数据）← `mood_soc/`（纯计算）← `store/`（状态与 IO）← `ui/` `api/`。
    ① 资源路径一律 `from data.paths import X`（**别自己拼 `resources/…`**，有测试扫源码）；
    ② 给界面加**状态或重算**要改 `store/session.py`，别把业务状态写回 `ui/app.py`；
    ③ 加程序接口能力 = 在 `api/ops.py` 加一个 op（步骤见 `documents/11-程序接口.md` §8）；
    ④ 老路径 `mood_soc.importer/output/scenario/maa`、`ui.schedule` 是**兼容转发壳**，别往里加逻辑。

19. **表格的滚轮 / 搜索 / 对齐 / 高度是一套**（「干员与心情」）：
    ① **列宽只有一个来源**——所有单元格 `grid` 到**共享的 `inner`**（不再是"每行一个 Frame"）；
    `TABLE_COLUMNS` 的非拉伸列用**像素**写死，长文字一律给 `wraplength`；改了行里的控件要
    三处一起想：`vs.join`（滚轮）／进 `r["cells"]`（悬停底色）／`row=r["grid_row"]`（列位）；
    ② **bindtags 链不含父控件** ⇒ 没有行 Frame 就必须**逐格 `join`**，否则"停在文字上滚不动"；
    ③ **滚动别卡**：`refresh()` 有尺寸签名缓存、滚动期间关掉悬停高亮（`VScroll.scrolling`）；
    ④ 搜索过滤只影响显示（一键动作范围走 `_visible_names()`）；`_table_height()` 量的是
    "**已建出来**的兄弟控件"，所以**表格必须最后建**、`TABLE_CHROME` 要跟着表格上方的改动重量，
    超出 `PAGE_H` 由 `_fit_table_height()` 自校正兜底（见 10-图形界面.md §5 第 16~20 条）。

20. **两层"世界"：界面显示"她在哪"一律读引擎那份（模拟副本），别读排班快照。**
    引擎每班深拷贝一份布局（`store/schedule.py: worlds`），**进驻事件的位置互换与闲置入宿的
    换人只改副本**；快照＝"你导入的排班"，导出时原样写回、**不改**。于是同一人两份"她在哪"，
    换过人后必然不一致（实测：快照"在贸易站上班" vs 引擎"不在基建、平线 0"）。
    读法：`Trajectory.world_at(t)` ← `ui/app.py: _engine_world()` / `world_at_abs()`；
    落在班次边界取**右侧**（与 `mood_at`/`rate_at` 同口径）。
    看板、全员一览的位置标记、对点查询的所在设施、「干员与心情」的位置列都走它；
    面板里两次不一致的行打 `⇄` 并在「说明」列写明原因。
    ⚠️ **编辑仍写快照**（`set_slots` / 面板选人），改完 `recompute` 再重新派生副本。
    ⚠️ 开着**闲置入宿**时，引擎**每段留一份深拷贝快照**（否则同一个副本跨周期复用，
    `world_at(t)` 会在第 1/2 周期给出第 N 周期的排布——修过的 bug）；API 的 `layout_at` 读同一份。

21. **心情跨阈值的「吸附」必须`就地`做，不能留到积分之后**（`store/schedule.py: _next_event`）。
    事件驱动积分在跨过 0/12/18/20/24 时把值精确吸附到阈值（去掉除不尽的尾巴）。但吸附表是在
    **积分之后**应用的，而那一刻 `nxt` 可能已在 `MAX_SEGMENT_HOURS`（0.25h）之外——只差 1e-26
    就跨阈值的人**已经被积分推过去了**，再吸附回去等于**把它往回拽**，白扣 `|速率|×0.25h`。
    实测：示例排班宿舍段同宿舍 **8 名干员被各扣 1.0**，曲线像"**卡在 20 不动、又没到 24**"；
    **步长调到 0.05h 就全对** —— 步长不该影响结果，所以那是 bug 不是口径。
    修法：ε 内跨阈值的人**当场吸附**并报给调用方重算速率；吸附表只留"正好在本段末尾跨过"的人，
    同刻用 `setdefault` 合并（原先会互相顶掉）。回归：`Test阈值吸附`（含**步长无关性**断言）。
    见 `documents/04-特殊机制.md` 第 35 条。

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

# 图形界面（纯标准库 tkinter；导入多班排班 / 时间滑动 / 对点曲线 / 一个「设置」中心）
.venv/Scripts/python.exe -m ui                          # 见 documents/10-图形界面.md
.venv/Scripts/python.exe ui/__main__.py                 # 等价；IDE 里直接 Run 也行

# 程序接口（给 Rust 调用；见 documents/11-程序接口.md、documents/12-v3接入.md）
.venv/Scripts/python.exe -m api.server                   # 常驻 NDJSON（推荐）
.venv/Scripts/python.exe -m api.cli --op capabilities    # 一次性调用
# v3 求解结果内联喂进来 → 查某时刻整座基地的布局 + 心情 → 闭环体检
# （load_json 的 data 是整份结果，用 `python -c` 包一层 {"data": …} 成 req.json）
.venv/Scripts/python.exe -m api.cli --op load_json --args @req.json \
  --then '{"op":"layout_at","args":{"at":8}}' --then '{"op":"closure","args":{"cycles":3}}'

# 数据管道（改完 data/*.txt 必须按顺序跑）
python scripts/classify_skills.py --agd <ArknightsGameData>   # 挂模板 + 生成 755 行台账
python scripts/generate_skills_data.py                        # 生成 data/skills_data.py
python scripts/classify_skills.py --check                     # 零遗漏校验（CI 用，不需要仓库）

# 技能全量核对（四层：模板 / clause / 上游描述 / 干员与组合技能；只读，不改数据）
python scripts/verify_skills.py --check                       # 有硬伤 → 退出码 1
python scripts/verify_skills.py --report                      # 重写 resources/skill_verify_report.md

# 测试
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

> 上游仓库**不要整仓 clone**（几百 MB），用无 blob 稀疏克隆，命令见 `documents/06-数据来源.md`。
> 克隆目录放**本仓库之外**。

---

## 🔁 一次测算怎么走（细节见 `documents/01-架构.md`）

```
用户输入（dict/JSON）
  └─ store.layout.build_base_layout(data)    # 解析层 → Operator/Facility/BaseLayout
       ├─ single：rules.evaluate(world, name, hours)   → MoodResult（含 ledger）
       └─ base：  rules.evaluate_base(world, hours)    → BaseResult
            └─ store.serialize.*_to_dict() → JSON（inf → null）
       （--trace）simulator.simulate(...)      # 数值解：每步重算速率，能表现"红脸后技能失效"联动
  └─ 多班 / 程序接口：store.session.Session（排班 + 全部设置 + 重算）← api/ 与 ui/ 共用这一份
```

技能数据管道（与测算主流程分离）：

```
Kengxxiao/ArknightsGameData  building_data.json（上游，非本仓库）
  └─ scripts/classify_skills.py --agd    # 挂模板 + 覆盖台账 + 零遗漏校验
       ├─ data/moods_skills.txt             （clause 级，+template_id/params）
       └─ data/skills_registry.txt          （buff 级 755 行台账）
            └─ scripts/generate_skills_data.py → data/skills_data.py（勿手改）
                 └─ mood_soc/skills.py re-export → rules.py 使用
       ⚠️ 生成物只 import 数据包（data.skill_model / data.conditions / data.domain），
          绝不 import mood_soc，否则「生成器无法重新生成自己」（见 07-设计史.md P6）
```
