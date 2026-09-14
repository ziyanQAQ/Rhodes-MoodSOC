# 数据查找策略（强制）—— 优先查上游仓库

> 本文件原为 `AGENTS.md` §11。拆出来的原因很简单：`AGENTS.md` 已经顶到
> **工作区指令预算上限（65536 字节）**，再往里塞内容就会被截断，
> 而"AI 读不到"的文档等于不存在。AGENTS.md §11 现在只留策略要点 + 速查表 + 指向本文件。
>
> 本文件与 `AGENTS.md` / `README.md` 同级维护：**改了这里的规则，AGENTS.md §11 的摘要也要跟着改。**

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


---

## 附：**已确认上游没有**的数据（别再去翻、更别反推）

查证时最耗时的不是"查不到"，而是**不知道某个东西根本不在 dump 里**，于是反复翻、最后靠巧合反推。
下面这些是已经查实**上游数据 dump 里就不存在**的，记在这里免得重复劳动：

| 项 | 结论 | 证据 |
|---|---|---|
| **加工站「配方心情消耗」** | **无字段** | `building_data.json → workshopFormulas` 68 条只有 `sortId/formulaId/rarity/itemId/count/goldCost/apCost/formulaType/buffType/extraOutcomeRate/extraOutcomeGroup/costs/requireRooms/requireStages`；对整份 `building_data.json` 递归搜键名含 `mood` 的字段**零命中** |
| **训练室的「心情消耗」常量** | **无** | 顶层 `trainingData` 只有 `{basicSpeedBuff: 0.05, phases:[{specSkillLvlLimit:…}]}`——全是训练**速度**；训练室的 1.0/h 只能引 `resources/心情消耗回复和工休时间.docx` 第 4 段 |
| （以此类推）公式实现 | **无** | 仓库是纯数据 dump，`[uc]lua/` 只有 `hotfixes/*.lua` 片段——"怎么算"一律以 docx 为准 |

> ⚠️ 反面教材：`apCost` 的取值集合恰好是 `{360000, 720000, 1440000, 2880000}`，
> 除以 180000 正好得到 `{2, 4, 8, 16}`——和 buff 文本里的「心情消耗为 2/4/8 的配方」对得上，
> **看起来像**能反推出配方心情消耗。但 `apCost` 是「AP 消耗」，与 `apToLaborRatio` 同一套体系，
> 语义上并不是心情；**巧合不构成证据**，所以本项目没有采用。若将来有人拿到了权威口径
> （客户端实现 / 官方wiki 明确字段），再回来补这一段并更新本条。
