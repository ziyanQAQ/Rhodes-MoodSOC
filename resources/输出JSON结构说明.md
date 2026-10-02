# 输出 JSON 结构说明（面向 AI 消费者）

> 文档角色：current-reference
> 生命周期状态：current
> 领域键：v3.output.json
> 当前真源：`src/serve.rs`、`src/plan_compute.rs`、`src/export/maa.rs`、`src/training_advice/`、`src/box_profile/`
> 摘要：v3 全部对外 JSON 出口的字段、类型、单位、可选性与消费规则；供其他 AI/下游系统直接消费
> 核对方式：本页字段与示例由 `target/release/arknights-infra-v3`（提交 `eb01f99`）实跑产出核对，非代码推断；复现命令见 §11

## 1. 最短结论

**“哪个干员在哪个设施”只出现在 `result.rotation.shifts[].assignment.rooms[]` 里，用 `room_id` 与设施对齐，用 `operators[].name` 标识干员。**

```text
响应信封 { id, ok, elapsed_ms, solver, result }
  result
    ├─ rotation          轮换排班（唯一含“干员→设施”的地方）
    │    ├─ daily        全班 24h 归一加权效率与日产出
    │    └─ shifts[]     每个班：时长、在班队伍、assignment（房间与成员）、efficiencies（分房效率）
    ├─ maa               MAA 自定义基建换班文件（按设施类型分组的干员名单）
    ├─ training_advice   玩家练卡推荐（schema 2）
    └─ profile           对固定满练基准的差距报告（schema 4）
```

三条硬规则，先记住再读细节：

1. **干员只由中文名标识**。输出没有 `OperatorId`、没有干员 `id`、没有技能槽；重名不做消歧。
2. **房间靠 `room_id` 对齐输入布局**（输入 `layout.rooms[].id` 的字符串），输出里没有设施类型字段；设施类型只能回到输入 `layout.rooms[].kind` 按 `room_id` 反查。
3. **数值分三种口径**，混用会错：效率类是千分比的小数（`1.93` = +193%）、办公室/会客室是整数百分点、日产出是 24h 实数。

## 2. 出口清单

| 出口 | 触发方式 | 顶层 JSON |
|---|---|---|
| 协议信封 | `arknights-infra-v3 serve` 的 stdin/stdout 每行一条 | `{id, ok, elapsed_ms, solver, result}` |
| 排班求解 | `serve` 请求 `method=plan.compute` | 信封 + `result{schema_version, rotation, maa, training_advice, profile}`（成功时四段齐全） |
| 协议诊断 | `serve` 请求 `method=ping`，或一次性命令 `arknights-infra-v3 ping` | 信封 + `result{...pong 身份与新版本声明...}` |
| MAA 排班文件 | `plan --maa-out <path>` 或 `result.maa` 落盘 | `{title, description, planTimes, plans[]}` |
| 单次评估 | `arknights-infra-v3 eval ... --json` | `{trade_efficiency, manufacture_efficiency, power_efficiency, office_speed_total, meeting_speed_total, rooms[]}` |
| 练卡推荐 | `arknights-infra-v3 advice --player --operbox <box.json> [--layout <layout.json>] [--pretty]` | 与 `result.training_advice` 同构（`schema_version: 2`） |

`plan` 与 `eval` 的默认输出是文本表格（stdout），只有 `eval --json` 与 `advice --player` 打印 JSON；所有日志、错误与就绪提示只写 stderr。

## 3. 协议信封（serve / ping）

请求（每行一个 JSON，最大 8 MiB）：

```json
{"id": 1, "method": "plan.compute", "params": { ... }}
```

响应：

```json
{
  "id": 1,
  "ok": true,
  "elapsed_ms": 11,
  "solver": {
    "git_commit": "eb01f99cc1cecebdd77446d37ef06a262a0f4f9b",
    "git_committed_at": "2026-09-07T00:30:54+08:00",
    "built_at": null
  },
  "result": { ... }
}
```

- `id` 原样回显请求的 `id`（缺省为 `null`）；解析失败时为 `null`。
- `ok` 是 `result` 层是否成功，**不等于**“求解成功”：`plan.compute` 的求解失败也会给出 `ok:false`，但同时带 `result`。
- `elapsed_ms` 为整毫秒；`solver` 为构建身份（`built_at` 可为 `null`）。
- 失败时同时给 `error{code, stage, message}`；`code`/`stage` 只在协议层错误时出现（`PLAN_FAILED`/`FRAME_TOO_LARGE`/`INVALID_UTF8`），JSON 解析失败只有 `message`。

### 3.1 错误与失败的实际形态（实跑）

协议层错误（无 `result`）：

```json
{"id":"m","ok":false,"elapsed_ms":0,"solver":{...},"error":{"message":"unknown serve method \"nope\""}}
```

求解失败（`result` 保留部分字段）：

```json
{"id":3,"ok":false,"elapsed_ms":1,"solver":{...},
 "error":{"code":"PLAN_FAILED","stage":"plan.compute","message":"trade room trade_1 requires product.trade"},
 "result":{"schema_version":4,"rotation":null,
           "error":{"code":"PLAN_FAILED","stage":"plan.compute","message":"trade room trade_1 requires product.trade"}}}
```

消费规则：

- `ok:false` 时先读 `error.message`，再看 `result`；
- `result.rotation` 为 `null` 表示没有排班，**此时不要读 `result.maa`/`training_advice`/`profile`，它们不存在**；
- `options.assert_invariants=true` 且失败时，`result` 整体被移除，只剩协议层 `error`；
- 未知 `method` 只报 `message`，没有 `code`。

### 3.2 ping 的额外字段

`ping` 的 `result` 除了 `pong` 之外还有：`protocol_version`（1）、`plan_schema_version`（1）、`supported_plan_schema_versions`（`[1,2,3,4]`）、`plan_contract_sha256`、`plan_contract_sha256_by_version`、`solver_executable_sha256`、`solver_git_commit`、`solver_git_committed_at`、`solver_built_at`。它只证明协议与契约身份，不证明该版本支持哪些轮换。

## 4. `plan.compute` 结果结构

请求参数（v4 契约）：`schema_version`（1–4）、`layout`、`operbox`、可选 `labels{layout, operbox}`、可选 `options{rotation, top, system_preferences, maa_title, fiammetta_enable, assert_invariants}`。

v3 当前实际支持的轮换：`abc_12_6_6`（3 班 12/6/6，默认）、`abc_12_12_12`（3 班各 12h）、`main_backup_12_12`（2 班各 12h）。其余三档（`fiammetta_witch_12_12`/`fiammetta_8_8_4_4`/`abyssal_7_5_7_5`）与非空 `system_preferences` 一律 `PLAN_FAILED`。

```json
{
  "schema_version": 4,
  "rotation": {
    "profile": "abc_12_6_6",
    "daily": {
      "trade": 5.253, "manufacture": 9.253, "power": 4.673,
      "production": {"lmd": 53912.648, "pure_gold": 45900.0, "battle_records": 37300.0,
                     "orundum": 0.0, "originium_shards": 0.0}
    },
    "shifts": [ /* 见 4.2 */ ]
  },
  "maa": { /* 见 5 */ },
  "training_advice": { /* 见 6 */ },
  "profile": { /* 见 7 */ }
}
```

`schema_version` 直接回显请求值，但它只标注入参契约版本，**不描述输出结构**；输出的结构版本应看 `profile.schema_version`（当前固定 4）与 `training_advice.schema_version`（当前固定 2）。

### 4.1 `rotation.daily`

| 字段 | 类型 | 含义 |
|---|---|---|
| `trade` | number | 全班按班次时长加权的贸易最终效率（含订单倍率的裸和口径），`5.253` = +525.3% |
| `manufacture` | number | 全班加权的制造总效率 |
| `power` | number | 全班加权的发电充能加成 |
| `production.lmd` / `pure_gold` / `battle_records` / `orundum` / `originium_shards` | number | 24h 归一后的日产出实数（订单或配方单位），与 24h 班次无关 |

### 4.2 `rotation.shifts[]`

| 字段 | 类型 | 含义 |
|---|---|---|
| `index` | integer | 班次下标，从 0 开始，按时间顺序 |
| `duration_hours` | number | 该班时长（12.0 / 6.0） |
| `active_teams` | string[] | 在班队伍名；三班为 `alpha/beta`、`beta/gamma`、`gamma/alpha`，二班为 `alpha`、`beta` |
| `resting_team` | string | 轮休队伍名 |
| `weighted_trade` / `weighted_manufacture` / `weighted_power` | number | 该班效率按 `本班时长 / 总周期时长` 缩放后的贡献（三位小数）；三者按班相加等于 `daily` 的前三项（允许末位舍入差） |
| `assignment` | object | **本班“干员→设施”名单**，见 4.3 |
| `efficiencies` | object | 本班分房效率，见 4.4 |

`active_teams`/`resting_team` 只是队伍标签，**没有任何字段把队伍映射到具体干员**；要判断某干员属于哪队，只能比较各班 `assignment.rooms[].operators[]` 的名字集合。

### 4.3 `assignment`（干员与设施的对应关系）

```json
"assignment": {
  "rooms": [
    {"room_id": "trade_1",
     "operators": [{"name": "巫恋", "elite": 2, "level": 80, "rarity": 5}]},
    {"room_id": "manu_3",
     "operators": [{"name": "维伊", "elite": 2, "level": 90, "rarity": 6}]}
  ],
  "training_assist": {"name": "帕拉斯", "elite": 2, "level": 90, "rarity": 6}
}
```

读取规则：

- `rooms` 只包含**该班有成员的房间**，按输入 `layout.rooms` 的原始顺序排列；没有成员的房间（含空宿舍、空训练室）**不出现**。某一班可能出现 `"rooms": []`（例如人数不足或该班空缺）。
- 每个房间的成员按落位顺序排列，`operators` 可能少于房间容量（允许部分排班）。
- 干员对象只有四个字段：`name`（中文名，唯一标识）、`elite`（0–2）、`level`、`rarity`（1–6）。**没有干员 id、潜能、技能槽、心情、所在设施类型**。
- 训练室：主干员以普通成员身份出现在该房间的 `operators` 里；训练挂件作为 `assignment.training_assist`（单个对象）单独给出，**它不算普通岗位**。当前 `abc_*` 轮换路径不产生训练分配，因此实际响应里通常看不到 `training_assist`，不要把它当必填字段。
- 同一干员在同一个 `assignment` 内不会重复出现；跨班次重复是正常的（轮换）。
- 同名干员按名字对齐即可，不要试图用 `elite/level/rarity` 反推身份。

### 4.4 `efficiencies`

```json
"efficiencies": {
  "trade_efficiency": 4.651, "manufacture_efficiency": 8.26, "power_efficiency": 4.55,
  "room_lines": [ { ... } ]
}
```

三个标量是该班各类设施裸和（贸易为含订单倍率的最终和）。`room_lines` 是**逐房行**：

| 字段 | 出现条件 | 含义 |
|---|---|---|
| `room_id` | 总是 | 与输入布局对齐的房间 id |
| `operator_count` | 总是 | 本班该房成员数（训练挂件不计） |
| `order_multiplier` | 总是 | 贸易订单倍率（1.0 表示无倍率）；非贸易房恒为 1.0 |
| `operator_efficiency` | 有分项时 | 干员技能贡献 |
| `base_efficiency` | 有分项时 | `1 + 设施基础加成`（2 人/3 人制造站的 1.03 等） |
| `station_efficiency` | 有分项时 | `设施基础 + 全基建` 之和 |
| `global_efficiency` | 非 0 时 | 跨设施/全基建加成 |
| `total_efficiency` | 有分项时 | 该房总效率（发电房为 `1 + 加成`） |
| `equivalent_efficiency` | 有分项时 | `总效率 − 基础 − 设施`，即净技能当量 |
| `trade_efficiency` / `trade_skill_efficiency` / `trade_output_per_day` | 贸易房 | 最终效率（含订单倍率）、技能效率、24h 日产出 |
| `manufacture_efficiency` / `manufacture_skill_efficiency` / `manufacture_output_per_day` / `manufacture_unit_output_per_day` | 制造房 | 总效率、技能效率、24h 日产出、单位(100%)日产出（赤金 10000、经验 8000、源石碎片 24） |
| `power_efficiency` | 发电房 | 充能加成倍率（`2.2` = +220%） |

顺序与数量：

- `room_lines` **只包含贸易、制造、发电**三类房间，办公室、会客室、中枢、宿舍、训练室、加工站不出现；
- 顺序是**先按设施类型分组**（中枢→贸易→制造→发电→宿舍→办公室→会客室→训练室→加工站的类型序号），组内保持输入布局顺序，**不是**纯布局顺序；
- **值为 0 的字段会被整体省略**（例如 `global_efficiency`、`order_multiplier` 之外的 0）。消费方必须按“缺失即 0”处理，不能要求字段存在；
- `equivalent_efficiency` 允许为**负数**（技能效率低于设施基准时）。

### 4.5 设施类型的确定

输出里没有 `kind`。要判断某 `room_id` 是贸易站、制造站还是发电站：

1. 用 `room_id` 回查请求里的 `layout.rooms[].kind`（权威，`trade_post`/`factory`/`power_plant`/`dormitory`/`control_center`/`office`/`meeting_room`/`training_room`/`workshop`）；
2. 或直接看 `room_lines` 里出现了哪一组字段（`trade_*` / `manufacture_*` / `power_*`）——这是机器可判的替代判据；
3. **不要**按房间命名（`trade_1`、`manu_1`）做硬编码路由，那只是模板习惯，不是契约。

MAA 段另有一套设施名分组，与 `layout.kind` 的对应关系见 5。

## 5. `maa` / MAA 排班文件

`result.maa` 与 `plan --maa-out <path>` 落盘的内容同构，可直接交给 MAA「自定义基建换班」导入。

```json
{
  "title": "243_2gold_trade 基建排班",
  "description": "由 ArknightsInfraCalc-v3 生成；可导入 MAA 自定义基建换班。",
  "planTimes": "3班",
  "plans": [
    {
      "name": "Shift 1 · 12h",
      "description": "本班 12 小时；由 ArknightsInfraCalc-v3 生成，可导入 MAA 自定义基建换班。",
      "Fiammetta": {"enable": false, "target": "", "order": "pre"},
      "drones": {"enable": false, "room": "manufacture", "index": 1, "order": "pre"},
      "rooms": {
        "trading":     [{"skip": false, "product": "LMD",          "operators": ["巫恋","龙舌兰"], "sort": false, "autofill": false}],
        "manufacture": [{"skip": false, "product": "Battle Record","operators": ["机械师"],     "sort": false, "autofill": false}],
        "power":       [{"skip": false, "operators": ["雷蛇"], "sort": false, "autofill": false}],
        "dormitory":   [{"skip": true,  "operators": [],       "sort": false, "autofill": false}],
        "control":     [...], "meeting": [...], "hire": [...], "processing": [...]
      }
    }
  ]
}
```

读取规则：

- `plans[]` 与 `rotation.shifts[]` **按顺序一一对应**（`plans[i]` 就是第 i 班），`plans[].name = "Shift {i+1} · {时长}h"`；`planTimes` 为 `"3班"` / `"2班"`。
- `rooms` 按 MAA 的设施名分组，组内数组顺序 = 输入布局中该类型房间的出现顺序，**与 `assignment.rooms` 的顺序依据不同**，不要用下标跨段对齐，只能用 `room_id`→布局→类型→组内第 N 个反查。
- 对应关系：`trade_post→trading`、`factory→manufacture`、`power_plant→power`、`dormitory→dormitory`、`control_center→control`、`meeting_room→meeting`、`office→hire`、`workshop→processing`；**训练室不出现在 MAA 结构里**。
- 空组（该类型没有房间）整个键不序列化；`product` 只在贸易/制造出现（`"LMD"`/`"Orundum"`、`"Pure Gold"`/`"Battle Record"`/`"Originium Shard"`）。
- `skip` 只在“空宿舍”时为 `true`；`sort`/`autofill` 恒为 `false`；`Fiammetta.enable` 与 `drones.enable` v3 当前恒为 `false`（菲亚梅塔回岗与无人机使用尚未实现）。
- `operators` 是中文名字符串数组，顺序与 `assignment` 一致。

## 6. `training_advice`（玩家练卡推荐，schema 2）

```json
{
  "schema_version": 2,
  "context": {
    "has_originium_shard_factory": false, "meeting_room_max_level": 3,
    "dormitory_level_sum": 20, "engineering_robot_count": 64,
    "trade_average_efficiency_percent": 31.67, "manufacturing_average_efficiency_percent": 31.72
  },
  "newbie_section_status": "shown | complete | skipped_by_efficiency",
  "incomplete_newbie": [ {"operator":"…","product":"trade","action":"acquire|train",
                          "current":{"elite":2,"level":80}?,"target":{"kind":"explicit|no_requirement|needs_review",
                          "elite":2,"level":null},"acquisition":{...}?} ],
  "combinations": [ {"id":"tequila_group","name":"…","product":"trade","consumer_products":[],
                     "tier":"high_efficiency","scale":"small","facilities":["trade_station"],
                     "state":"complete|needs_training|missing_core|missing_important|needs_review",
                     "completed_slots":3,"total_slots":3,"completion_percent":100,
                     "missing_core":[],"untrained_core":[],"missing_important":[],"untrained_important":[],
                     "selected_alternative":0?,
                     "members":[{"operator":"…","role":"core|important","progress":"ready|missing|owned_needs_training|needs_review",
                                 "owned":true,"target_met":true,"current":{...}?,"target":{...},
                                 "counts_toward_completion":true}]} ],
  "recommendations": [ {"operator":"…","action":"acquire|train","current":{...}?,"target":{...},
                        "priority":"…","priority_rank":50,"reason":"newbie_required|combination_core|combination_important|standalone",
                        "product":"gold","combination_id":"automation","combination_name":"自动化",
                        "efficiency":{...}?,"conditions":[{"condition":{...},"status":"satisfied|unsatisfied|unknown"}]?,
                        "acquisition":{...}?} ]
}
```

要点：

- 三块分别是“未完成新手清单 / 固定 17 组体系完成度 / 去重后最多 10 条行动建议”；`combinations` 顺序固定，`recommendations` 已按 `priority_rank` 排序并去重。`recommendations` 可以为空数组（当前 Box 无缺口时实跑即 `[]`）。
- 枚举取值集合：`product` ∈ `trade`/`gold`/`experience`/`general_manufacturing`/`originium_shards`；`role` ∈ `core`/`important`/`secondary`/`hanger`；`tier` ∈ `high_efficiency`/`low_efficiency`；`scale` ∈ `small`/`system`；`target.kind` ∈ `explicit`/`no_requirement`/`derive_from_skill_binding`/`needs_review`（后者的输出形式已归一为 `explicit` + `elite`/`level`，`level` 可为 `null`）；`priority` 见下。
- `priority` 取值集合（同序权值 `priority_rank` 见括号）：`newbie_four_star_elite_one`(10)、`flagship_newbie`(20)、`owned_tailor`(40)、`automation_must_train`(45)、`small_high_efficiency_core`(50)、`small_high_efficiency_important`(55)、`system_single_core_gap`(60)、`other_important`(70)、`lower_priority_core`(75)、`other_newbie`(75)、`high_efficiency_standalone`(80)。消费方按 `priority_rank` 排序即可，不必硬编码枚举顺序。
- `acquisition` 只在“未拥有”时出现，形如 `{"kind":"shop|public_recruitment|event|redeem_code|integrated_strategy","detail":"…"}`；`efficiency` 只在独立高效干员建议上出现，形如 `{"value":30.0,"unit":"order_percent|production_percent","note":"…"?}`。
- `*_average_efficiency_percent` 是“每站技能效率 ÷ 当站人数”的**不按时长加权**均值，单位是百分点，仅用于判断是否跳过新手段落（阈值 >30 / >25），**不等于** `rotation.daily` 的效率口径。
- `knowledge` 与基准已编译进二进制，不读运行时资料文件。
- 该段不参与排班，只描述“该练谁”；`advice --player` 命令输出与本节同构。

## 7. `profile`（对固定满练基准的差距报告，schema 4）

```json
{
  "schema_version": 4,
  "rotation_profile": "abc_12_6_6",
  "layout_label": "243 probing", "operbox_label": "curated",
  "baseline_label": "data/fixtures/243/schedule_export.json (full_e2)",
  "summary": {"owned": 429, "tier_up_owned": 429, "trade_pool_ready": 77, "manufacture_pool_ready": 93},
  "domains": [
    {"id":"trade_gold","label":"贸易·赤金线",
     "current":{"operators":["巫恋","龙舌兰","卡夫卡"],"final_efficiency":2.08},
     "baseline":{"operators":["…"],"final_efficiency":2.84},
     "gap_ratio":-0.27,"severity":"critical|warn|ok"}
  ],
  "rotation": {"daily_trade_efficiency":5.253,"daily_manufacture_efficiency":9.253,"daily_power_efficiency":4.673},
  "baseline_rotation": {...},
  "actions": [{"priority":"P0","kind":"acquire|promote_tier_up","operator":"…","domain_id":"trade_originium",
               "message":"…","current_elite":0?,"tier_up_requirement":"精2"?}],
  "flags": ["manufacture_total_critical","manufacture_bottleneck","trade_gold_ok"],
  "narration_hints": ["…中文提示…"]
}
```

要点：

- `domains` 只在两侧都有数据时出现，`id` 现为 `trade_gold`/`trade_originium`/`manufacture_total`/`manufacture_gold`/`manufacture_battle_record`/`rotation_trade`/`rotation_manufacture`；`severity` 由 `gap_ratio` 阈值决定（≤ −20% 为 `critical`，≤ −8% 为 `warn`，否则 `ok`）。
- `current`/`baseline` 是**参考编制**的名字与效率快照（基准为固定满练目录 Box），`baseline` 的干员是“应有”的，不代表玩家拥有。
- `actions`/`flags`/`narration_hints` 是给 UI/解说用的中文建议串；`message` 可直接展示。
- 基准 Box 与基准编制已编译进二进制；`baseline_label` 是来源标注，不是可读文件路径承诺。
- 该段失败（例如基准不适用）时：`assert_invariants=false` 会在 `result` 里塞一个 `error` 对象而其余段仍完整；`assert_invariants=true` 则整体失败。

## 8. `eval --json`

```json
{
  "trade_efficiency": 1.93, "manufacture_efficiency": 1.02, "power_efficiency": 2.2,
  "office_speed_total": 0, "meeting_speed_total": 0,
  "rooms": [
    {"room_id":"trade_1","kind":"Trade","level":3,"total_efficiency":1.93,"final_efficiency":2.395},
    {"room_id":"meeting","kind":"Meeting","level":3,"total_efficiency":0,"final_efficiency":0}
  ]
}
```

- `rooms` 覆盖**全部**输入房间（含宿舍、训练室、加工站），顺序即布局顺序；`kind` 用 Rust 枚举名（`Control`/`Trade`/`Manufacture`/`Power`/`Dorm`/`Office`/`Meeting`/`Training`/`Workshop`）。
- 贸易/制造/发电的 `total_efficiency`、`final_efficiency` 是三位小数倍率；**办公室/会客室例外，为整数百分点**（例如 `30` 表示 +30%）。
- 该出口的输入是显式 `BaseAssignment`（含顶层 `training_assist`），与 `plan.compute` 的求解路径不同；同一干员在评估编制里重复出现会直接报错。`--shift-hours` 默认 24。

## 9. 给其他 AI 的消费规则清单

1. **对齐主键**：设施用 `room_id`（回查输入的 `kind`），干员用 `name`；输出里没有任何整数 id 可直接复用。
2. **不要把 `active_teams`/`resting_team` 与具体干员绑定**：队伍只是标签。
3. **单位三分**：效率倍率（÷1000 后的三位小数，`1.93`=+193%）、办公室/会客室百分点、日产出实数。
4. **缺字段即 0**：`room_lines` 与多个可选字段遵循“非 0 才出现”，用 `?? 0` 读取；不要因为字段缺失判定失败。
5. **空班合法**：`shifts[].assignment.rooms` 可以为空数组，`ok:true` 仍成立；不要把它当成解析错误。
6. **`ok:false` 先看 `error.message`**，且只有 `result.rotation != null` 时才继续解析其余段。
7. **不要用命名/字符串规则推断设施或干员角色**：设施类型回查布局，干员角色只来自 `training_assist` 与 `training_advice`。
8. **不要在输出层做二次机制结算**：效率、产出、订单倍率已结算完毕，重复乘算会双计。
9. **版本判断**：协议版本看 `ping.result.protocol_version`；输出结构版本看 `profile.schema_version` 与 `training_advice.schema_version`；请求契约版本看 `result.schema_version`。
10. **复现身份**：需要可追溯时记录 `solver.git_commit` 与 `ping.result.solver_executable_sha256`。

## 10. 已知边界（不要误判为输出缺字段）

- 菲亚梅塔与无人机：MAA 段 `Fiammetta.enable`、`drones.enable` 恒 `false`，回岗与无人机使用尚未投影。
- 训练室：不进入 MAA 结构；在 `assignment` 里以“主干员进普通成员数组 + 可选 `training_assist`”表示，当前 `abc_*` 路径通常不产生训练分配。
- 心情、跨设施资源、无人机库存、`AssignmentExtra` 内部状态**不出现在任何 JSON 出口**；需要这些信息必须走内部 API，不是输出契约。
- `system_preferences` 非空、三个特殊轮换、以及未实现机制对应的参数一律 `PLAN_FAILED`，不要降级猜测。
- 输出不保证满编：允许部分生产岗位，人数不足时按可落位结果交付。

## 11. 实跑核对方式

```sh
cargo build --release
printf '%s\n' '{"id":1,"method":"plan.compute","params":{"schema_version":4,"layout":<layout>,"operbox":<box>,"options":{"rotation":"abc_12_6_6"}}}' \
  | target/release/arknights-infra-v3 serve
```

本页字段即由该方式在 `data/fixtures/rotation_243.json`（**v3 仓库**，本仓库外） + 429 条目满练 Box 上产生（三档轮换：3/3/2 班，`maa.plans` 与 `shifts` 数量一致）。

注意：**debug 构建在 `plan.compute` 路径上会栈溢出**（stderr 打印 `thread 'main' has overflowed its stack`，无任何 stdout 输出；与 Box 规模无关，最小 1 名干员亦可复现）。核对输出请使用 `--release` 构建；`eval` 等其它命令的 debug 行为未在本页范围内验证。该现象与 JSON 结构无关，但排查时容易被误认为“没有输出”。
