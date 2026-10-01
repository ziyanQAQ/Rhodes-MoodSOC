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
> 5. **测试相关的产物一律不入库、不推送**（用户口径，2026-09）：`tests/`（测试代码）与
>    `scenarios/`（测试排班）两个目录**整目录已在 `.gitignore` 里**，临时探查脚本 / 回归样本 /
>    一次性生成物放 `_scratch/` 或根目录 `_*.py`（同样已忽略）—— **不要 `git add` / commit / push**。
>    仓库里只保留正式代码与文档。（2026-09 已把历史遗留的 `scenarios/demo.json` 等 4 个文件
>    `git rm --cached` 撤出仓库、本地保留；文档里依赖它们的示例已改成内置 `--demo`。）
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
算"高效率的基建布局"，本项目**匹配那套布局**并在支持的设施、技能和建模假设范围内生成它的**完整心情变化周期**；
图形界面只负责让人直观地看，**真正的交付面是 `api/`**：v3 把求解结果内联喂进来
（`load_json`），配好「换干员 / 闲置入宿 / 周期」三组口径，然后问
「某人 + 时间节点 → 心情」（`operator_detail`）或「时间节点 → 整座基地的布局 + 心情」（`layout_at`）。
**默认口径已定**：换干员**默认关**（手动配置）、闲置入宿**默认开**（三层解耦：
**手动编辑 > 自动入宿 > 导入布局**；自动入宿＝竖向正序填空床，全满后取**心情最低**的候选、
换出"锁定区外、心情 **≥ 她**、心情最大"的住户；**不设班次数量门槛**，长班 >12h 在班内
12h 整数倍处还有内部换班执行点）、周期**默认取文件里的各班时长**。见 `documents/12-v3接入.md`。

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
├── 07-设计史.md            架构诊断 + P1~P9 重构决策记录
├── 08-上游数据源分析.md     上游仓库结构分析（首次摸底留档）
├── 09-开发指南.md          运行与测试 / 公共 API 速查 / 改完代码自查清单
├── 10-图形界面.md          图形界面 ui/：导入多班排班 / 时间滑动 / 对点曲线
├── 11-程序接口.md          ★ api/：NDJSON 常驻服务 + 一次性 CLI + 求解器适配面
├── 12-v3接入.md            ★ v3（Rust）侧接入：调用序列 / 三组配置 / 默认口径 / 踩坑
├── 13-版本记录.md          发布版本对照（v1.0/v1.1 差异）+ 打包口径 + 资产 sha256
├── 14-架构总览.md          ★ **要动架构 / 拆模块前先读**：现状测绘（四条线 + 横切）+ 逐条病灶清单
└── 15-架构图.md            ★ **想一眼看懂全流程**：分层大图（数据→处理→功能→API→UI）+
                            端到端数据流 + 一次重算的时序 + 界面编辑的写路径（各附 ASCII 速览）
```

> ⚠️ **要改架构 / 拆模块 / 大改界面归属**：先读 `documents/14-架构总览.md`（每条病灶带 `file:line`），
> 再回 `01-架构.md`（设计意图）与 `07-设计史.md`（历史决策）。
> 它记着几件**不读就会踩**的现状：`Session` 是 1069 行的上帝对象、
> **改布局有 9 条写入口而只有 2 条维护手动台账**、房间等级**两条写路径口径不同**、
> 增量重算的指纹**不含** `slots`/`manual`/`atmosphere`。

**分层（依赖严格单向向下，`tests/test_layers.py` 静态扫描盯着）**：
`ui/`（tkinter 视图）与 `api/`（程序接口，JSON 进 JSON 出）→ `store/`（状态与 IO：
排班/轨迹/解析/序列化/**会话**）→ `mood_soc/`（纯计算）→ `data/`（数据 + 领域基元 + 路径出口）。
`data/paths.py` 是**资源路径的唯一出口**；`data/` 与 `mood_soc/` 不许 import 上层；
`ui/` 与 `api/` 互不 import；`api/` `store/` `data/` 的代码里不许出现 `tkinter`。
**数据分两条线**：`data/` = 要 import 的表（技能库 / 台账 / 阵营 / 生成物）；
`resources/` = 仓库根的项目级数据（样例 JSON、数据字典说明、核对报告；**需求文档 docx 不入库**、只在本地与历史里）。
`documents/` = **文档**；`scenarios/` = 场景目录（**本地、不入库**）；`.agents/` 与 `skills-lock.json` = 本机 AI 技能（**不入库**）。

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
11. **改完跑全量黑盒测试**：优先使用 `.venv/Scripts/python.exe -m unittest discover -s tests`；环境缺少该解释器时使用 `py -3 -m unittest discover -s tests`。文档不固定测试总数；核心测试与 GUI 验收分别记录，GUI 不能用跳过代替真实 Windows 验收。
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
    闲置入宿默认开）。**闲置入宿的"默认开"是全项目统一口径**（用户裁决"闲置入宿默认是开启的"）：
    界面 / `load_file` / `simulate_schedule` / `apply_idle_to_dorm` 都默认结算，只有显式关
    （JSON `false` / 取消勾选 / `idle_to_dorm=False` / `--no-idle-to-dorm`）才不动布局。
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
    **闲置入宿**（把没满的闲置干员塞进宿舍；**默认开**：文件里没写这个键就结算、显式 `false` 才关）
    —— **2026-10 三层解耦（手动编辑 > 自动入宿 > 导入布局）**，更早的四级优先级 / 挂件门 /
    阵营门 / 菲亚梅塔例外 / 手动指定位置 / 手动点名 / 竖向反序自动交换 / 追加队尾 **全部作废**。
    一句话口径：
    · **① 手动编辑（最高）**：看板 / 「干员与心情」把某人放进某个位次 —— 写的是**班次布局**
      与**手动台账**（`models.ManualLedger`，`{slots, names}` 挂在设施上、随班次走、进 JSON）。
      被钉住的**位次**与**人**自动入宿绝不碰（不占、不换）；手动清空的位次**保持空着**。
      **导入不打标**；位次**不左移**（`facilities[].slots` 留 `null`，`models.set_seat` 是唯一写入口）。
    · **② 自动入宿**：**候选**＝该班**完全没出现在任何设施**（未排班 /「不在基建」名单）+
      心情 < 24 + 不在黑名单；⚠️ **"是否候选"看该班原始布局（`pristine[idx]`）、"心情"取执行点
      实时值（`moods`）**。两相：**竖向正序填空床**（含中间空洞）→ 全满则按 `(心情↑, 名字↑)`
      逐个取候选，**换出"锁定区外、心情 ≥ 她、且心情最大"的住户**（并列取竖向正序靠前）；
      **被换出者不在本执行点再入队**（下一个执行点重新评估）。
      定点＝宿舍（锁定区外）里最低的那位也 ≥ 外面剩下的人；终止性由"心情总和严格下降"保证。
      ⚠️ 心情闸是 **≥** 不是"严格大于"：取"合格住户里最大的那位"＝把最少余量让出去，
      剩下的住户才够后面的候选换（取最小会把余量吃光，后到的高心情候选永远换不进来 —— 实测踩到）。
    · **③ 全局配置**：总开关 / **锁定位置** `protected_slots`（默认 5＝竖向正序前 N 个**逻辑位次**，
      里面的人自动不换、**空位照样能入住**）/ **黑名单**（不能**通过闲置入宿进宿舍**，但可被换出）/
      逐人 `per_operator` **只剩"参不参与"**（旧写法 `target`/`dorm`/`slot`/`swap_with` 读得进来
      但**被忽略**，API 回一条 `notes`）。
    · **不设班次数量门槛**：1 个班次也执行 —— 引擎签名里**没有**班次数参数；
    · **换班执行点**：每个真实班次**班初** ＋ 长班（>12h）班内每个**严格小于班末**的 12h 整数倍
      （`store.schedule.execution_offsets`：12h→[0]、18h→[0,12]、24h→[0,12]、25h→[0,12,24]；
      常量 `INTERNAL_SWAP_HOURS = 12`、`execution_points(schedule, cycles)`）。
      每个执行点**从该班原始布局重建位置**、重跑进驻事件与闲置入宿、并记一条 `internal` 标记
      （**即使没事发生也记**）；内部换班**不增加班次数、不改 `shift_index`**，同周期同班次的所有
      执行点**共用一份逐人设置**（`scope` 仍是 `(周期, 班次)`）；"等待回满再换"不跨执行点。
      ✅ API `layout_at` / `world_at(t)` 落在执行点上取**换班后**（`_segment` 用 `bisect_right`）。
      ⚠️ **看板也必须跟着换**：内部换班点**不改 `shift_index`**，所以"要不要重画看板"不能只判
      "班次号变了"（`ui/app.py: set_time`）—— 判据是"**这一刻那份引擎世界还是不是上一次画的那一份**"。
      只判班次号会让看板一直画着换人**之前**的排布（用户报过；回归 `Test内部换班看板`）。
    ⚠️ **加工站/训练室入驻者、副手、所有上班与在宿舍的人都不是候选**。
    ⚠️ **面板那张逐次表只剩「参与」可改** + 一列**只读**的"引擎安排了什么"
    （`Trajectory.idle_note_at`；`idle_state_at` 仍给逐位候选的宿舍态）；**手动入宿不在表里**，
    它在看板 / 「干员与心情」。顺序上**先换心情、再判闲置入宿**，判定用**实时**心情。
    ⚠️ **`_seat_verdict` 是三层唯一的裁决点**（顺序：手动 → 锁定区 → 空位 → 其余）；
    它读的锁定位置键是 `rules._seat_key(world, fac)`＝`(类型, 实例名)` 或 `(类型, 世界下标)` ——
    **不能用 `id(facility)`**（排班层先在 pristine 上算、再在每段深拷贝上跑 ⇒ 锁定区会静默失效），
    也不能只用 `display_name`（同名宿舍会互相顶掉）。
    改这一块**必须跑 `scripts/verify_idle.py`** + 全量 `unittest`，见 `documents/04-特殊机制.md` 第 30 条。
18. **分层别搞反**：`data/`（数据）← `mood_soc/`（纯计算）← `store/`（状态与 IO）← `ui/` `api/`。
    ① 资源路径一律 `from data.paths import X`（**别自己拼 `resources/…`**，有测试扫源码）。
    ⚠️ **打包成 exe 之后 `resources/` 整目录不存在**（exe 里只带 `data/operators.txt`）：
    `data/paths.py: project_root()` 冻结时指向 `sys._MEIPASS`（回归 `Test打包路径`），
    所以**凡是读 `RES/…` 的代码都要容错"文件不存在"**（现有唯一一处＝界面冷启动的
    `if SAMPLE.exists()`）。构建见 `scripts/build_exe.py` 与 09-开发指南 §9.6；
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
    引擎**每个换班执行点**都从一份"未动过的计划副本"重建当前布局（`store/schedule.py: pristine` +
    执行点开头 `copy.deepcopy`），**进驻事件的位置互换与闲置入宿的换人只改这份副本**；
    快照＝"你导入的排班"，导出时原样写回、**不改**。于是同一人两份"她在哪"，换过人后必然不一致
    （实测：快照"在贸易站上班" vs 引擎"不在基建、平线 0"）。
    读法：`Trajectory.world_at(t)` ← `ui/app.py: _engine_world()` / `world_at_abs()`；
    落在执行点/班次边界取**右侧**（与 `mood_at`/`rate_at` 同口径）。
    看板、全员一览的位置标记、对点查询的所在设施、「干员与心情」的位置列都走它；
    面板里两次不一致的行打 `⇄` 并在「说明」列写明原因。
    ⚠️ **编辑仍写快照**（`set_slots` / 面板选人），改完 `recompute` 再重新派生副本。
    ⚠️ 开着**闲置入宿**时，引擎**每个执行点留一份深拷贝快照**（否则同一个副本跨周期复用，
    `world_at(t)` 会在第 1/2 周期给出第 N 周期的排布——修过的 bug）；API 的 `layout_at` 读同一份。
    ⚠️ **每个执行点（含内部换班点、含每个周期）都要从"未动过的计划副本"重建**：
    副本会被就地改（闲置入宿换人 / `restore_back=False` 的位置也互换），**跨班次跨周期复用它**
    会让"第 1 周期把她换出去"继承到后面所有周期——那一班开局就没有她、**既不重判心情也没有事件**
    （用户报过"菲亚梅塔 20.4 却显示被闲置入宿换出"）。**位置**每个执行点复位，**心情**连续继承
    （在 `moods` 里）；代价 = 每个执行点一次深拷贝（≈0.35ms；>12h 的长班会多几个执行点）。

21. **「周期数」上限是 `store.session.MAX_CYCLES = 7`（唯一口径）**：界面两处下拉、`set_cycles`、
    `closure`、`idle_groups`、API `set_timeline`/`closure` 全部**夹到 1~7**（越界取边界、不报错；
    界面还会把夹过的值**回写下拉**）。成本≈线性：示例排班实测一次重算 1 周期 **0.12s** → 3 周期 **0.44s**
    → **7 周期 1.14s**（2026-09 做完"记忆化三件套 + 探针缩小 + 增量重算 + 变量快照共享"之后的数；
    优化前是 0.32 / 1.16 / 2.84s，逐项见坑 23）。**编辑类重算常常远低于全量**（增量只算尾巴，见坑 23 ⑤）。
    改这个上限时别只改下拉——写死 3 的地方当初散在两处，已经收成一个常量。
22. **心情跨阈值的「吸附」必须`就地`做，不能留到积分之后**（`store/schedule.py: _next_event`）。
    事件驱动积分在跨过 0/12/18/20/24 时把值精确吸附到阈值（去掉除不尽的尾巴）。但吸附表是在
    **积分之后**应用的，而那一刻 `nxt` 可能已在 `MAX_SEGMENT_HOURS`（0.25h）之外——只差 1e-26
    就跨阈值的人**已经被积分推过去了**，再吸附回去等于**把它往回拽**，白扣 `|速率|×0.25h`。
    实测：示例排班宿舍段同宿舍 **8 名干员被各扣 1.0**，曲线像"**卡在 20 不动、又没到 24**"；
    **步长调到 0.05h 就全对** —— 步长不该影响结果，所以那是 bug 不是口径。
    修法：ε 内跨阈值的人**当场吸附**并报给调用方重算速率；吸附表只留"正好在本段末尾跨过"的人，
    同刻用 `setdefault` 合并（原先会互相顶掉）。回归：`Test阈值吸附`（含**步长无关性**断言）。
    见 `documents/04-特殊机制.md` 第 35 条。
23. **性能靠这几处"记忆化 / 共享"撑着，动它们要连回归一起动**（2026-09，示例排班 7 周期 3.41s → **1.14s**；原为五处，③④ 挂件判据那两处已随"挂件门"删除）：
    ① `rules._active_skill_ids` / `rules._skills_of` 把结果**缓存在干员实例上**
    （键 `(elite, level, len(skill_ids), id(skill_ids))`）—— 它们只依赖技能槽 + 练度，与原心情/位置无关；
    实测一次重算里被调 **112 万次**。换 `skill_ids` 的**列表对象**或改长度会自动失效，
    所以 `verify_skills.py` L2 那种"构造完再注入 skill_id"的场景照旧正确。
    ⚠️ **别再往这里加缓存**：2026-09 试过把 `_active(op)` + `_skills_of(op, kind)` 合成一个
    "带红脸态"的合并缓存（改 12 处调用点），同机 A/B **1.01×（＝白做）** —— profiler 里那 112 万次调用
    大半是**采样开销**，真实成本很小；已回退（负结果留档，别再试）；
    ② `models.BaseLayout._name_index()` 是 `facility_of` / `get_operator` 的**懒建名字索引**
    （原来是逐房间扫 + genexpr，13 万次调用 / 4.66M 次迭代）。⚠️ **设施成员一变就必须
    `world.invalidate_index()`** —— 现有调用点全在 `rules.py`：`_leave_previous_facility`、
    `_swap_positions`（进驻事件的位置互换）、闲置入宿的 `_place` / `_swap` 两条改人路径。
    漏调 = 查询给出"她还在原来那间"的陈旧答案；
    ③④ **挂件判据那两处优化（`_all_rates` 共享速率表 / `_pendant_probe_names` 窄探针）已随
    "挂件门"一起删除**（2026-09 闲置入宿按《闲置入宿完整逻辑》重写：自动交换只看心情，
    不再判"她走了别人会不会变差"）。副作用是**性能变好**：原来挂件判据占示例排班一次重算的
    ~27%，现在这段成本归零 —— 所以别再把 `_is_pendant` / `_all_rates` / `_dependent_holders`
    当成"性能支柱"去找回来；要恢复那套口径就是恢复旧行为，得先问用户；
    ⑤ **重算只算"第一个变了的段"之后的尾巴**（P6a 周期数 → P6b 通用段级）：
    `store.schedule.simulate_schedule(continue_from=(上一份轨迹, 它**已算完的段数**))`，
    其中"段"＝**一个换班执行点**（班初 / 内部换班点 / 班次边界），见坑 17 与
    `store.schedule.execution_points`。界面上最常见的重算是改周期数与改某班设置，
    而"同一个班次每周期都重复" ⇒ 前面那些段整段复用。
    ⚠️ **切点由逐段输入指纹算出来**（`Session._segment_signatures()`：每段一条
    `(全局设置, 那一班的布局, 那一段的闲置入宿逐次设置, 落在那一段的心情锚点)`），
    取"第一个与上一份轨迹不同的段"；相等的前缀整段复用，`cut == 0` 就整条重算。
    实测（示例排班 7 周期 21 段，全量 ≈1.16 s）：周期 7→6 **7.6 ms**（只截断）、
    6→7 **182 ms**（切点 18）、改第 7 周期第 1 班锚点 **187 ms**、改第 7 周期第 3 班逐次设置
    **72 ms**（切点 20，省 94%）。
    ⚠️ 段边界必须落在**新时间轴的段起点**上：种子取"**段边界上第一个节点**"的值＝**段首事件之前**的心情
    （段首的进驻事件会在续算时再跑一遍；用 `mood_at(t0)` 会拿到跳变**后**的值 ⇒ 进驻事件被算两遍），
    对不上就报错整条重算（时间轴变了时会发生）。
    ⚠️ **班次标记一律按 `schedule` 重新生成**（时刻＝各班起点、标签＝各班标签，是它的纯函数）——
    前缀里那份是旧时间轴的（周期数改小后还多出几段），照搬就会"第 5 班的标记还在、第 6 班的没了"；
    红脸标记同理一律按合并后的曲线重算。
    ⚠️ 用什么判"输入变了"见 `Session._world_digest()`：**按内容摘要**，不能拿 `id(schedule)` 当判据
    （`set_training` 是**就地改干员对象**的，对象没换但值变了）。
    `ui/app.py` 的异步重算要把 `_sigs` 一起传给 `Session.adopt`。
    回归 `Test增量重算`（加/减周期、改某班布局、改末周期锚点/逐次设置：**逐位等于全量** + 钉住切点段号）。
    ⚠️ 它**救不了"改班次布局"**：布局按班次存、那个班每周期都会出现 ⇒ 切点＝它在**第 1 周期**的出现处 ⇒
    7 周期下只省 ~4%（实测 926 ms）——"改布局也快"得靠引擎本体（见下条）；
    ⑥ **一批人的净速率共享一份「变量快照」**（P7，2026-09）：`mood_soc.net_rates(world, names)`
    把 `collect_variables(world)`（人间烟火/热情值/无声共鸣…，**世界级**、与"算谁"无关）**收一次**
    发给这批人 —— 原先"算一个干员的速率就重收一遍"，同机交替 A/B 示例排班 7 周期
    **1525 ms → 1159 ms（1.32×）**（3 周期 1.31×、1 周期 1.23×）。
    同一份口径也用在 `evaluate` / `evaluate_base`（挂件判据那份探针世界已随坑 23 ③④ 一起删除）。
    ⚠️ `mood_ledger` / `compute_net_rate` 都多了个**可选** `variables=`（不传＝老行为，逐人自收）；
    **快照与 `world` 的成员+心情绑定** ⇒ 只在"同一个世界快照、连续算一批人"时共享，
    世界一变（换人/改心情）必须重收。
    回归：`test_equivalence.Test变量快照共享`（① 共享快照 vs 逐人自收的速率逐位相同；
    ② 把引擎猴子补丁回"每人各收一份"后**整条轨迹逐位相同**）＋ 全量 466 绿。
    渲染层不用管：`idle_groups` 6.8ms、`_build_shift_buttons` 5.4ms、`world_at` 0ms —— **瓶颈只在引擎**。
    详见 `documents/09-开发指南.md` 的性能基线表与 `documents/07-设计史.md` P9。
24. **编辑走「异步重算」，而 `decimal` 上下文是线程局部的 —— 两者是一套，别拆开看**（2026-09，P5）：
    ① 界面上的**编辑**（改布局/心情/换心情/闲置入宿/时间轴/看板左右键）调 `ui/app.py: recompute_async()`：
    工作线程算、主线程 `after(30, _poll_recalc)` 收结果并 `Session.adopt()`；**导入/测试/程序路径**仍走
    同步的 `Session.recompute()`（返回时轨迹已是新的）。连续编辑只落地最后一版（请求**代数** `_recalc_gen`，
    过期结果丢弃）。`app.wait_recalc()` 给测试/面板用（泵事件循环直到落地）。
    ② ⚠️ **结果落地前不许刷面板**：面板的防抖（心情 500ms / 闲置入宿 250ms）还没提交时落地刷新会
    **按轨迹重写格子**，把用户刚敲的值盖掉、那次编辑整个丢失（实测：改「泡泡 8」后 `apply_batch` 收到空 mood）。
    所以 `_poll_recalc` 先看 `SettingsDialog.has_pending_edit()`（各面板实现同一个探针）——还忙就把结果
    攥在 `_recalc_ready` 里、30ms 后再看。**换排班（`load_paths`）则把在算的整代作废**（代数推高 +
    丢结果 + 停轮询 + 清 `_status_after_recalc`），否则旧排班的结果会盖掉导入摘要。
    ③ ⚠️ **`decimal` 上下文是线程局部**：`mood_soc/config.py` 那两行只设到导入它的线程，工作线程默认
    `ROUND_HALF_EVEN` ⇒ 末位差 1e-26 ⇒ 经阈值吸附放大成**事件时刻不同**（实测同一份输入主线程与
    工作线程在 **52 名干员**的轨迹上不一致）。修法：引擎入口（`store.schedule.simulate_schedule`、
    `rules.evaluate` / `evaluate_base`）都先调 `config.use_project_decimal_context()`；
    回归 `Test线程与精度`（逐位一致 + 故意改坏上下文也会被设回来）。
    ④ ⚠️ **GC 只许在主线程跑**（`gc.disable()/enable()` 是**解释器级**的，不是线程局部）：
    工作线程入口 `_gc_off_for_worker()`（登记 + 关），出口 `_gc_worker_done()` **只减计数、
    一次都不 `gc.enable()`**；恢复一律由**主线程路径** `_gc_on_when_idle()` 做
    （`_poll_recalc` / `_publish_recompute` / `refresh_view` / `wait_recalc` / `destroy`），
    只要还有工作线程活着就继续关着。理由＝⑦：析构只要有一次落在工作线程就可能**崩进程**。
    回归 `TestGC只在主线程跑`（线程里全程为假 + 出口不许自己开回来 + 落地/销毁后恢复）。
    ⑤ `apply_batch` / `apply_entry_event` / 改房间等级这类"落地后要写状态栏"的，文案放
    `app._status_after_recalc`（由 `_settle_recalc` 盖上）；面板要按新轨迹重建的就用
    `app.add_recalc_listener(绑定方法)`（`weakref.WeakMethod`，**不能传 lambda**）。
    ⑥ ⚠️ **换排班时必须连"旧工作线程的引用"一起摘掉**（`load_paths` 里 `self._recalc_thread = None`）：
    `recompute_async` 靠"线程还活着"决定**起不起新线程**，而"算完接着跑最新一代"**只由轮询任务**负责 ——
    换排班刚把轮询 `after_cancel` 掉，若旧线程还没算完，紧跟的那次编辑只会把代数推高、
    **既不新起线程也没人轮询** ⇒ 那一代**永远不落地**（界面卡在「计算中…」、曲线还是换排班前那条；
    实测 `wait_recalc` 只能靠 120s 超时放过，`Test界面冒烟` 里那条"换排班时旧线程不会卡住新结果"就是它）。
    旧线程的结果本来就会因**代数过期**被丢弃，所以摘引用不丢任何正确结果。
    ⑦ ⚠️ **关窗口时要把工作线程有界 `join` 干净**（`destroy()` → `_recalc_threads` 逐个
    `join(timeout=RECALC_JOIN_TIMEOUT)`；`_work()` 里**一次都不碰 `self`**，跨线程要用的
    queue 在启动前取成局部量）：
    只要"窗口已销毁、工作线程还在跑"，线程里的一次 GC 就会把已销毁窗口的 Tk 对象在
    **非主线程**里回收 —— 先刷屏 `Variable.__del__: main thread is not in main loop`，
    再往后 Tcl 的 async handler 被错误线程删掉，直接 `Tcl_AsyncDelete: async handler deleted
    by the wrong thread` **崩掉进程**（实测：全量测试合并跑三次崩两次、把 Tk 用例单独跑就稳；
    真实场景＝**重算过程中关窗口**）。`load_paths` 甩掉的旧线程也登记在 `_recalc_threads` 里，
    所以一样会被 join。回归 `Test重算中关窗口`（两条：在算的线程 / 被换排班甩掉的旧线程）。
    ⚠️ **`destroy()` 还要撤掉挂着的 `after_idle`**：`Tk.destroy()` 逐个销毁子控件时 Tk **仍会派发
    `<Configure>`**（实测收到一个真实宽度 1560），`RosterStrip._on_resize` 会因此**又排一个新的**
    idle 重建 ⇒ 只撤一次不够、之后必然刷 `invalid command name "..._deferred_rebuild"`。
    故 `cancel_pending()` **上闩** `_closing`（之后 `_on_resize` 直接返回），`destroy()` 里在
    `super().destroy()` **之前**调它。回归 `TestGC只在主线程跑`。
25. **计数基准 `power_count` 数的是「有效间数」**（2026-09）：上游有两条技能的**唯一效果**是
    「**仅影响设施数量**」——森蚺「我寻思能行」（**控制中枢** ∧ **Lancet-2 在发电站** ⇒ 发电站 **+2**）、
    承曦格雷伊「晨曦」（**发电站** ∧ **其他发电站内没有作业平台 `cc.tag.op`** ⇒ 发电站 **+1**）。
    它们没有心情子句 ⇒ **不进** `moods_skills.txt` / `skills_data.py`，而是登记在
    `data/facility_count.py`、由 `mood_soc/facility_count.py` 求值：
    `有效间数 = 实际间数 + Σ(条件成立)`；**布局/容量/进驻位一律不变**，只改"数出来几间"。
    唯一消费方是流明「柔和微光」的 `+0.05/间` 分句（`basis=power_count`）——
    典型算例：实际 2 间 + 森蚺 +2 = 4 间 ⇒ 室友宿舍回复 4.25 → **4.35**（`--explain` 尾巴里写明
    `实际 2 间 + 森蚺「我寻思能行」+2 = 4 间`）。台账那两行也从"非心情，登记不建模"改成
    "设施数量修正…只影响计数基准 power_count"（`scripts/classify_skills.py` 有对应分支，
    重新生成不会打回）。⚠️ **两条条件不对称（用户拍板 2026-09，别互相"对齐"）**：
    **晨曦**要"其他发电站内没有**有效的**作业平台"⇒ 红脸（`mood ≤ 0`，总口径"红脸 ⇒ 技能失效"）
    的平台**不阻断**、照样 +1；**森蚺**只看"Lancet-2 **进驻**在发电站"⇒ **不看心情**、红脸也算 +2。
    改这一块跑 `tests/test_power_count.py`（两边各有用例钉住）；口径见 `02-数值规则.md` §3.2.2、
    `04-特殊机制.md` 第 19 条、`06-数据来源.md` §11.6。
26. **Tk 的键名/事件名要挑"当前 Tk 认得"的那个**（2026-09-29 构建 v1.1 时踩到，**发布阻断级**）：
    `ui/settings.py` 原来只绑 `<ISO_Left_Tab>`（X11 的 Shift+Tab 键名），而 **Windows 的 Tk ≤ 8.6.12
    没有这个 keysym** ⇒ `TclError: bad event type or keysym "ISO_Left_Tab"`；这行在设置窗口的
    `__init__` 里 ⇒ **整个「设置…」打不开**（全量测试里 21 个 GUI 用例同时红）。v1.0 的 exe 因为用
    Python 3.14（Tk 8.6.13+）构建才一直没暴露。做法：`<Shift-Tab>` → `<Shift-Key-Tab>` →
    `<ISO_Left_Tab>` **逐个 try、绑上第一个就 break**（只绑一个：都绑在 X11 上可能触发两次）。
    ⚠️ 推论：**"测试全绿"是"在某个具体 Tk 上"成立的事实** —— 换 Python / 换 Tk / 重建 `.venv`
    之后 GUI 用例必须真跑一遍（`tests/` 无图形环境时会整套 skip，**不能用跳过代替验收**）。
    细节与回归见 `documents/10-图形界面.md` §7.1 第 21 条、`documents/13-版本记录.md`。

---

## ⚡ 最常用命令

```bash
# 测算（single 模式必须给 --target；--period 缺省 0）
python main.py --demo --target 泡泡                    # 内置演示，目标泡泡
python main.py --demo --target 泡泡 --period 8         # 推进 8 小时
python main.py --scenario-file <你的场景.json> --target 泡泡 --period 8   # 场景文件自备（scenarios/ 不入库）
python main.py --demo --target 泡泡 --explain           # 打印心情流水账（为什么是这个速率）
python main.py --demo --target 菲亚梅塔 --entry-events  # 先结算进驻事件（M15a 心情互换）
python main.py --mode base --demo                      # 整个布局还能维持多久
python main.py --mode base --demo --period 12          # 先推进 12h 再评估

# 图形界面（纯标准库 tkinter；导入多班排班 / 时间滑动 / 对点曲线 / 一个「设置」中心）
.venv/Scripts/python.exe -m ui                          # 见 documents/10-图形界面.md
.venv/Scripts/python.exe ui/__main__.py                 # 等价；IDE 里直接 Run 也行
.venv/Scripts/python.exe -m ui --smoke                   # 建窗口转一圈就退（打包自检，退出码 0＝好）

# 打包成 exe（PyInstaller onedir；**不带任何测试数据**，见 09-开发指南 §9.6）
.venv/Scripts/python.exe -m pip install pyinstaller       # 构建期工具，只需一次
.venv/Scripts/python.exe scripts/build_exe.py             # → dist/RhodesMoodSOC/RhodesMoodSOC.exe
dist/RhodesMoodSOC/RhodesMoodSOC.exe --smoke               # 自检：退出码 0 ＝ 窗口建得起来

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

# 闲置入宿自检（口径＝三层解耦版：手动编辑 / 自动入宿 / 全局配置 + 换班执行点那组）
python scripts/verify_idle.py                                 # 全绿 → 退出码 0
python scripts/verify_idle.py -v                              # 打印每一条通过项

# 模块边界自检（七模块：谁可以写谁的数据；见 documents/14-架构总览.md §9）
python scripts/verify_modules.py                              # 全绿 → 退出码 0
python scripts/verify_modules.py --list                       # 只列"现状待办"（跨模块写白名单）

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
