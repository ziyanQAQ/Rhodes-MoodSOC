# 技能核对报告（全量）

> **生成物**：由 `scripts/verify_skills.py --report` 自动生成，不要手改。
> 三层核对 = 模板级（L1）/ clause 级（L2）/ 上游描述对照（L3）；口径与模板字典见 `documents/05-技能分类大纲.md`。

## 一、总览

- 心情 clause（`skills.SKILLS`）：**250** 条
- 上游 buff 台账：**755** 条（`resources/skills_registry.txt`）——已建模 210 / 登记不建模 545
  - 轴 A 分布：A1 214 条、A2 40 条、A3 6 条、A4 495 条
  - 台账双向核对：✅ 755 条 buff 全部对上（modeled=yes 都有 clause，modeled=no 都没有）
- 模板：**32** 个（已建模 19 / 登记不建模 13）
- L1 模板自洽：✅ 通过
- L2 clause 级：✅ 242 条正常 / ⚪ 2 条按文档不可达 / ⚪ 1 条事件类 / ⚪ 3 条消除类 / ⚪ 1 条池分配 / ⚪ 1 条元修正 / ❌ 0 条不符
- L3 描述对照：✅ 246 条一致 / ⚪ 4 条无数字可对（消除/事件类）/ ⚪ 0 条已登记差异 / ❌ 0 条不符

### 需要处理的差异

没有。250 条 clause 都能在**满足它条件的场景**里按模板口径生效，上游描述里的数字与数据表一致，755 条 buff 也全部有归宿。

## 二、模板使用（L1）

| 模板 | 名称 | 轴 A | 叠加 | clause 数 |
|---|---|---|---|---|
| M01 | 中枢→宿舍 群体回复 | A1 | F2 | 6 |
| M02a | 中枢→room1 回复 | A1 | F3 | 0 |
| M02b | 中枢→room2 回复 | A1 | F3 | 4 |
| M02c | 中枢→room1 + 扩散白名单 | A1 | F3 | 1 |
| M03 | 中枢内 全体回复 | A1 | F1 | 25 |
| M04 | 中枢内 指定干员共事 | A1 | F1 | 2 |
| M05 | 同设施 全体消耗增减 | A1 | F1 | 8 |
| M06 | 同设施 其他消耗增减 | A1 | F1 | 0 |
| M07a | 自身消耗增减 | A1 | F1 | 84 |
| M07b | 自身回复（非宿舍/加工站） | A1 | F1 | 2 |
| M08 | 宿舍 群体回复 | A1 | F2 | 48 |
| M09 | 宿舍 单体回复（锁一人） | A1 | F2 | 32 |
| M10 | 宿舍 自身回复 | A1 | F2 | 29 |
| M11 | 宿舍 池分配 | A1 | F6 | 1 |
| M12 | 宿舍 定向/条件回复 | A1 | F1 | 2 |
| M13 | 消除自身消耗影响 | A2 | F7 | 3 |
| M14 | 独占回复 | A2 | F7 | 1 |
| M15a | 心情互换/顺序 | A2 | F7 | 1 |
| M15b | 触发式恢复一次心情 | A2 | F7 | 0 |
| M16 | 心情当条件（效果非心情） | A3 | F1 | 0 |
| M17 | 强化他人恢复效果（元修正） | A1 | F1 | 1 |
| X01 | 制造产出 | A4 | F1 | 0 |
| X02 | 仓库容量 | A4 | F1 | 0 |
| X03 | 贸易订单 | A4 | F1 | 0 |
| X04 | 发电与无人机 | A4 | F1 | 0 |
| X05 | 会客室线索 | A4 | F1 | 0 |
| X06 | 人力办公室招募 | A4 | F1 | 0 |
| X07 | 训练室专精 | A4 | F1 | 0 |
| X08 | 加工站配方与副产品 | A4 | F1 | 0 |
| X09 | 变量生产/消耗 | A4 | F1 | 0 |
| X10 | 跨设施/设施计数条件 | A4 | F1 | 0 |
| X11 | 特殊订单 | A4 | F7 | 0 |

## 三、逐条 clause（L2 / L3）

`状态` 列：`ok` 已核对生效 / `unreachable` 按文档不可达 / `event` 事件类 / `eliminate` 消除类 / `pool` 池分配 / `boost` 元修正 / `bad` 不符

| clause | 技能 | 类型 | 模板 | value | 状态 | 说明 | 描述对照 |
|---|---|---|---|---|---|---|---|
| `control_allCost_condChar_000#1` | 浮生得闲 | cc_recover | M03 | 0.25 | ✅ ok | 中枢回复 → 0.25（3 条实例：中枢内全体，各一条） | ✅ |
| `control_bd_spd_000#1` | 老友相聚 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `control_clue_cost&faction_990#1` | 反抗者 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `control_clue_cost_000#1` | 神经质 | facility_consume | M05 | 1.5 | ✅ ok | 同设施全体消耗 → 1.5（2 条实例：同设施全体（含自身）） | ✅ |
| `control_clue_cost_010#1` | 至察 | facility_consume | M05 | 0.5 | ✅ ok | 同设施全体消耗 → 0.5（2 条实例：同设施全体（含自身）） | ✅ |
| `control_dorm_rec2_000#1` | 羁绊相生 | cc_recover | M01 | 0.05 | ✅ ok | 中枢→宿舍回复 → 0.05（2 条实例：每间宿舍的每个成员各一条） | ✅ |
| `control_dorm_rec2_000#2` | 羁绊相生 | cc_recover | M01 | 0.1 | ✅ ok | 中枢→宿舍回复 → 0.1（2 条实例：每间宿舍的每个成员各一条） | ✅ |
| `control_dorm_rec_000#1` | 领袖 | cc_recover | M01 | 0.05 | ✅ ok | 中枢→宿舍回复 → 0.05（2 条实例：每间宿舍的每个成员各一条） | ✅ |
| `control_dorm_rec_001#1` | 战纹 | cc_recover | M01 | 0.05 | ✅ ok | 中枢→宿舍回复 → 0.05（2 条实例：每间宿舍的每个成员各一条） | ✅ |
| `control_dorm_rec_002#1` | 巡心 | cc_recover | M01 | 0.05 | ✅ ok | 中枢→宿舍回复 → 0.05（2 条实例：每间宿舍的每个成员各一条） | ✅ |
| `control_dorm_rec_tag_001#1` | 无言的慈爱 | cc_recover | M01 | 0.1 | ✅ ok | 中枢→宿舍回复 → 0.2（4 条实例：每间宿舍的每个成员各一条） | ✅ |
| `control_facCostReset_000#1` | 杯莫停 | eliminate_self | M13 | 0 | ⚪ eliminate | 归零组 self_consume | ⚪ |
| `control_meeting&mp_cost_000#1` | 英雄的骄傲·α | self_consume | M07a | 0.02 | ✅ ok | 自身消耗 → 0.02（1 条实例：只作用自身） | ✅ |
| `control_meeting&mp_cost_100#1` | 英雄的骄傲·β | self_consume | M07a | 0.02 | ✅ ok | 自身消耗 → 0.02（1 条实例：只作用自身） | ✅ |
| `control_meeting_bd_000#1` | “是，团长！” | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `control_mp&meet_spd_000#1` | 成效优先 | self_consume | M07a | 0.05 | ✅ ok | 自身消耗 → 0.05（1 条实例：只作用自身） | ✅ |
| `control_mp_aegir1_000#1` | 潮汐守望 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `control_mp_aegir1_000#2` | 潮汐守望 | dorm_self | M07b | 0.5 | ✅ unreachable | 04-特殊机制 第 23 条：含持有者自身口径下「反之」恒不成立 | ✅ |
| `control_mp_aegir1_000#3` | 潮汐守望 | dorm_self | M07b | 0.5 | ✅ unreachable | 04-特殊机制 第 23 条：依赖「反之」分支，同样不可达 | ✅ |
| `control_mp_aegir2_000#1` | 集群狩猎·α | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（2 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_aegir2_010#1` | 集群狩猎·β | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（2 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_bd&trade_000#1` | 演技的怪物 | self_consume | M07a | 0.01 | ✅ ok | 自身消耗 → 0.05（1 条实例：只作用自身） | ✅ |
| `control_mp_bd_cost_expand_000#1` | 孤光共照 | cc_recover | M02b | 0.05 | ✅ ok | 中枢回复 → 0.05（5 条实例：作用设施内的每个干员各一条） | ✅ |
| `control_mp_bd_cost_expand_000#2` | 孤光共照 | cc_recover | M02b | 0.05 | ✅ ok | 中枢回复 → 0.15（5 条实例：作用设施内的每个干员各一条） | ✅ |
| `control_mp_cost&bd1_000#1` | "不以物喜" | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（2 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost&bd2_000#1` | "不以己悲" | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `control_mp_cost&bd2_010#1` | 耐力回复 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `control_mp_cost&bd3_000#1` | 生活的重压 | self_consume | M07a | 0.05 | ✅ ok | 自身消耗 → 0.05（1 条实例：只作用自身） | ✅ |
| `control_mp_cost&bd_up_000#1` | 知我为我 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `control_mp_cost&faction2_000#1` | 坚毅随和 | cc_recover | M03 | 0.2 | ✅ ok | 中枢回复 → 0.4（4 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost&faction_000#1` | 德才兼备 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.10（4 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost&faction_020#1` | 学生会会长 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.10（4 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost&faction_030#1` | 幕后指挥 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.10（4 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost&faction_900#1` | 异格者 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.10（4 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost&faction_990#1` | 彩虹小队 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.10（4 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost_000#1` | 左膀右臂 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_001#1` | S.W.E.E.P. | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_002#1` | 零食网络 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_003#1` | 清理协议 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_004#1` | 替身 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_005#1` | 必要责任 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_006#1` | 护卫 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_007#1` | 小小的领袖 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_008#1` | 独善其身 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_009#1` | 笑靥如春 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_010#1` | 金盏花诗会 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_011#1` | 捍卫之道 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_012#1` | 博识生手 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_013#1` | 点滴关照 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_014#1` | 总工程师 | cc_recover | M03 | 0.05 | ✅ ok | 中枢回复 → 0.05（8 条实例：中枢内全体 + 被公事公办扩散到其他设施，各一条） | ✅ |
| `control_mp_cost_double_000#1` | 魔王传承 | cc_recover | M04 | 0.05 | ✅ ok | 中枢回复 → 0.05（3 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost_double_001#1` | “未完的故事” | cc_recover | M04 | 0.1 | ✅ ok | 中枢回复 → 0.1（3 条实例：中枢内全体，各一条） | ✅ |
| `control_mp_cost_reset_000#1` | 互为半身 | eliminate_self | M13 | 0 | ⚪ eliminate | 归零组 self_consume | ⚪ |
| `control_mp_expand_double_000#1` | 巴别塔之帜 | cc_recover | M02b | 0.1 | ✅ ok | 中枢回复 → 0.1（5 条实例：作用设施内的每个干员各一条） | ✅ |
| `control_mp_expand_double_000#2` | 巴别塔之帜 | cc_recover | M02b | 0.1 | ✅ ok | 中枢回复 → 0.1（5 条实例：作用设施内的每个干员各一条） | ✅ |
| `control_mp_lonely_000#1` | 公事公办 | cc_recover | M02c | 0.1 | ✅ ok | 中枢回复 → 0.1（3 条实例：作用设施内的每个干员各一条） | ✅ |
| `dorm_exchangeAp_000#1` | 患难之交 | dorm_targeted | M15a | 0 | ✅ event | 进驻事件（M15a）：走 apply_entry_events，不进速率 | ⚪ |
| `dorm_hireToRecAll_000#1` | 寻同路人 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_hireToRecAll_000#2` | 寻同路人 | dorm_group | M08 | 0.05 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_hireToRecAll_001#1` | 无瑕心·α | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_hireToRecAll_001#2` | 无瑕心·α | dorm_group | M08 | 0.05 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_hireToRecAll_021#1` | 无瑕心·β | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_hireToRecAll_021#2` | 无瑕心·β | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.3（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_powToRecAll_000#1` | 柔和微光·α | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_powToRecAll_000#2` | 柔和微光·α | dorm_group | M08 | 0.05 | ✅ ok | 宿舍群体回复 → 0.05（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_powToRecAll_010#1` | 柔和微光·β | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_powToRecAll_010#2` | 柔和微光·β | dorm_group | M08 | 0.05 | ✅ ok | 宿舍群体回复 → 0.05（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_recExcludeOther_000#1` | 自律 | dorm_self | M14 | 2 | ✅ ok | 独占回复 → 2（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&bd_000#1` | 无词颂歌 | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&bd_000#2` | 无词颂歌 | dorm_group | M08 | 0.01 | ✅ ok | 宿舍群体回复 → 0.11（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&group_000#1` | 调众口 | dorm_group | M08 | 0.06 | ✅ ok | 宿舍群体回复 → 0.06（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&lv_000#1` | 睡前必听故事 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&lv_100#1` | 死前必做清单 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&lv_100#2` | 死前必做清单 | dorm_group | M08 | 0.02 | ✅ ok | 宿舍群体回复 → 0.10（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&oneself_000#1` | 慵懒 | dorm_self | M10 | -0.1 | ✅ ok | 宿舍自身回复 → -0.1（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_000#2` | 慵懒 | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&oneself_001#1` | 嗜睡 | dorm_self | M10 | -0.1 | ✅ ok | 宿舍自身回复 → -0.1（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_001#2` | 嗜睡 | dorm_group | M08 | 0.25 | ✅ ok | 宿舍群体回复 → 0.25（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&oneself_010#1` | 超脱 | dorm_self | M10 | 0.55 | ✅ ok | 宿舍自身回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_011#1` | 挣脱 | dorm_self | M10 | 0.1 | ✅ ok | 宿舍自身回复 → 0.1（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_012#1` | 解脱 | dorm_self | M10 | 0.55 | ✅ ok | 宿舍自身回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_012#2` | 解脱 | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&oneself_021#1` | 牧歌 | dorm_self | M10 | 0.55 | ✅ ok | 宿舍自身回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_021#2` | 牧歌 | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&oneself_022#1` | 天生丽质 | dorm_self | M10 | 0.55 | ✅ ok | 宿舍自身回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_022#2` | 天生丽质 | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&oneself_042#1` | “归乡” | dorm_self | M10 | 0.55 | ✅ ok | 宿舍自身回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_all&oneself_042#2` | “归乡” | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&profession_000#1` | 火山温泉浴 | dorm_group | M08 | 0.06 | ✅ ok | 宿舍群体回复 → 0.06（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&single_000#1` | 小酌怡情 | dorm_group | M11 | 0.8 | ⚪ pool | 总额 0.8 由 1 人平分 | ✅ |
| `dorm_rec_all&tag_000#1` | 资深料理人 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&tag_000#2` | 资深料理人 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（1 条实例） | ✅ |
| `dorm_rec_all&tired_000#1` | 芬芳疗养·β | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&tired_000#2` | 芬芳疗养·β | dorm_targeted | M12 | 0.1 | ✅ ok | 宿舍定向回复 → 0.1（1 条实例） | ✅ |
| `dorm_rec_all&tired_100#1` | 净化呼吸 | dorm_targeted | M12 | 0.1 | ✅ ok | 宿舍定向回复 → 0.1（1 条实例） | ✅ |
| `dorm_rec_all&unfull_000#1` | 倾听者 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&unfull_001#1` | 倾谈者 | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all&unfull_001#2` | 倾谈者 | dorm_group | M08 | 0.01 | ✅ ok | 宿舍群体回复 → 0.01（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_000#1` | 鼓舞 | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_010#1` | 小提琴独奏 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_011#1` | 偶像 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_012#1` | 沁人心脾 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_013#1` | 领袖 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_014#1` | 大锅饭·α | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_017#1` | 静心仪式·α | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_020#1` | 冬将军 | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_021#1` | 提灯女神 | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_022#1` | 狮心王 | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_023#1` | 芬芳疗养·α | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_024#1` | 大锅饭·β | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_025#1` | 乡野笛音 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_026#1` | 利他主义 | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_027#1` | 静心仪式·β | dorm_group | M08 | 0.2 | ✅ ok | 宿舍群体回复 → 0.2（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_028#1` | 明星效应 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_029#1` | 沉静心灵 | dorm_group | M08 | 0.15 | ✅ ok | 宿舍群体回复 → 0.15（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_all_999#1` | 毛茸茸的抚慰 | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_bd_n1_n2_000#1` | 睡前故事 | dorm_group | M08 | 0.1 | ✅ ok | 宿舍群体回复 → 0.1（2 条实例：同宿舍每个成员各一条） | ✅ |
| `dorm_rec_bd_n1_n3_000#1` | 慢板行歌 | dorm_single | M09 | 0.65 | ✅ ok | 宿舍单体回复 → 0.65（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself2_000#1` | “独处” | dorm_self | M10 | 0.7 | ✅ ok | 宿舍自身回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself2_000#2` | “独处” | dorm_self | M10 | 0.05 | ✅ ok | 宿舍自身回复 → 0.05（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself2_001#1` | 赋格融汇 | dorm_self | M10 | 0.7 | ✅ ok | 宿舍自身回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself2_001#2` | 赋格融汇 | dorm_self | M10 | 0.05 | ✅ ok | 宿舍自身回复 → 0.05（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_000#1` | 独处 | dorm_self | M10 | 0.7 | ✅ ok | 宿舍自身回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_001#1` | 幼狼情性 | dorm_self | M10 | 0.7 | ✅ ok | 宿舍自身回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_002#1` | 哪里都是兔子洞 | dorm_self | M10 | 0.7 | ✅ ok | 宿舍自身回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_010#1` | 狂热 | dorm_self | M10 | 0.85 | ✅ ok | 宿舍自身回复 → 0.85（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_011#1` | 麻烦回避者 | dorm_self | M10 | 0.85 | ✅ ok | 宿舍自身回复 → 0.85（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_020#1` | 悲歌 | dorm_self | M10 | 1 | ✅ ok | 宿舍自身回复 → 1（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_030#1` | 隐形的美食家 | dorm_self | M10 | 0.75 | ✅ ok | 宿舍自身回复 → 0.75（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_oneself_040#1` | 难得休憩 | dorm_self | M10 | 0.55 | ✅ ok | 宿舍自身回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_000#1` | 活泼 | dorm_single | M09 | 0.2 | ✅ ok | 宿舍单体回复 → 0.2（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_000#2` | 活泼 | dorm_self | M10 | 0.4 | ✅ ok | 宿舍自身回复 → 0.4（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_001#1` | 探险家的热情 | dorm_single | M09 | 0.25 | ✅ ok | 宿舍单体回复 → 0.25（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_001#2` | 探险家的热情 | dorm_self | M10 | 0.5 | ✅ ok | 宿舍自身回复 → 0.5（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_010#1` | 烘焙 | dorm_single | M09 | 0.3 | ✅ ok | 宿舍单体回复 → 0.3（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_010#2` | 烘焙 | dorm_self | M10 | 0.3 | ✅ ok | 宿舍自身回复 → 0.3（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_011#1` | 烹饪 | dorm_single | M09 | 0.35 | ✅ ok | 宿舍单体回复 → 0.35（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_011#2` | 烹饪 | dorm_self | M10 | 0.35 | ✅ ok | 宿舍自身回复 → 0.35（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_012#1` | Give me five | dorm_single | M09 | 0.35 | ✅ ok | 宿舍单体回复 → 0.35（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_012#2` | Give me five | dorm_self | M10 | 0.35 | ✅ ok | 宿舍自身回复 → 0.35（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_020#1` | 和谐 | dorm_single | M09 | 0.4 | ✅ ok | 宿舍单体回复 → 0.4（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_020#2` | 和谐 | dorm_self | M10 | 0.2 | ✅ ok | 宿舍自身回复 → 0.2（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_021#1` | 喀兰圣女 | dorm_single | M09 | 0.5 | ✅ ok | 宿舍单体回复 → 0.5（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_021#2` | 喀兰圣女 | dorm_self | M10 | 0.25 | ✅ ok | 宿舍自身回复 → 0.25（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_030#1` | 使徒 | dorm_single | M09 | 0.5 | ✅ ok | 宿舍单体回复 → 0.5（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_030#2` | 使徒 | dorm_self | M10 | 0.25 | ✅ ok | 宿舍自身回复 → 0.25（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_040#1` | 甜甜圈派对 | dorm_single | M09 | 0.4 | ✅ ok | 宿舍单体回复 → 0.4（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&oneself_040#2` | 甜甜圈派对 | dorm_self | M10 | 0.4 | ✅ ok | 宿舍自身回复 → 0.4（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&tag_000#1` | 狩猎好帮手 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single&tag_000#2` | 狩猎好帮手 | dorm_single | M09 | 0.45 | ✅ ok | 宿舍单体回复 → 0.45（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_000#1` | 医疗服务 | dorm_single | M09 | 0.65 | ✅ ok | 宿舍单体回复 → 0.65（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_001#1` | 疗养 | dorm_single | M09 | 0.65 | ✅ ok | 宿舍单体回复 → 0.65（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_010#1` | 善解人意 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_020#1` | 慈悲 | dorm_single | M09 | 0.75 | ✅ ok | 宿舍单体回复 → 0.75（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_030#1` | 天启 | dorm_single | M09 | 0.7 | ✅ ok | 宿舍单体回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_031#1` | 维多利亚文学 | dorm_single | M09 | 0.7 | ✅ ok | 宿舍单体回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_032#1` | 心理疏导 | dorm_single | M09 | 0.7 | ✅ ok | 宿舍单体回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_033#1` | 恩典颂歌 | dorm_single | M09 | 0.7 | ✅ ok | 宿舍单体回复 → 0.7（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_1010#1` | 善解人意 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_P_000#1` | 沏茶 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_P_000#2` | 沏茶 | dorm_single | M09 | 0.45 | ✅ ok | 宿舍单体回复 → 0.45（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_P_001#1` | 烤肉大师 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_P_001#2` | 烤肉大师 | dorm_single | M09 | 0.45 | ✅ ok | 宿舍单体回复 → 0.45（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_P_002#1` | 毒剂师之友 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_P_002#2` | 毒剂师之友 | dorm_single | M09 | 0.45 | ✅ ok | 宿舍单体回复 → 0.45（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_power_000#1` | 降生于冰寒 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_power_000#2` | 降生于冰寒 | dorm_single | M09 | 0.45 | ✅ ok | 宿舍单体回复 → 0.45（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_power_001#1` | 圣城趣事通 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_power_001#2` | 圣城趣事通 | dorm_single | M09 | 0.45 | ✅ ok | 宿舍单体回复 → 0.45（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_single_power_100#1` | 天生乐天派 | dorm_single | M09 | 0.55 | ✅ ok | 宿舍单体回复 → 0.55（1 条实例：只落在 1 名干员账上（自身 / 锁定的那名受益人）） | ✅ |
| `dorm_rec_toone_000#1` | 头号陪练 | dorm_meta | M17 | 0.3 | ⚪ boost | 并入「推进之王」的宿舍群体回复小计（1 条增量） | ✅ |
| `hire_spd_cost&char_000#1` | 圣女声望 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost&char_001#1` | 雪境归心 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost&clue_000#1` | 救援队·保证体力 | self_consume | M07a | -0.1 | ✅ ok | 自身消耗 → -0.3（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_100#1` | 救援队·珠算 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_101#1` | 宫廷礼仪 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_110#1` | 救援队·资源清点 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_111#1` | 特殊渠道 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_112#1` | 踏坊寻味·α | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_120#1` | 威权谕使 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_121#1` | 踏坊寻味·β | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_200#1` | 准时下班 | self_consume | M07a | 2 | ✅ ok | 自身消耗 → 2（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_210#1` | 法为正典 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_220#1` | 永不停歇·α | self_consume | M07a | 1 | ✅ ok | 自身消耗 → 1（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_230#1` | 永不停歇·β | self_consume | M07a | 1 | ✅ ok | 自身消耗 → 1（1 条实例：只作用自身） | ✅ |
| `hire_spd_cost_999#1` | 独立调查 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_cost_000#1` | 春雷响，万物长 | facility_consume | M05 | -0.1 | ✅ ok | 同设施全体消耗 → -0.1（2 条实例：同设施全体（含自身）） | ✅ |
| `manu_cost_all_000#1` | 团队精神 | eliminate_self | M13 | 0 | ⚪ eliminate | 归零组 self_consume | ⚪ |
| `manu_formula_cost_000#1` | Vlog | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_formula_spd&cost_000#1` | 小奇思 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `manu_formula_spd&cost_001#1` | 净味香氛 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `manu_formula_spd&cost_bd_000#1` | 挑大梁 | self_consume | M07a | -0.15 | ✅ ok | 自身消耗 → -0.15（1 条实例：只作用自身） | ✅ |
| `manu_formula_spd&limit&cost_000#1` | 镜中影 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_formula_spd&limit&cost_010#1` | 戏中人 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_formula_spd&limit&cost_100#1` | “连轴转” | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_000#1` | 拾荒者 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_0000#1` | 拾荒者 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_001#1` | 磐蟹·阿盘 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_002#1` | “都想要” | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_003#1` | 智慧之境 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_010#1` | 囤积者 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_011#1` | 无畏豪情 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_012#1` | 午休好去处 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_020#1` | 探险者 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_021#1` | 掘进工程 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_limit&cost_1020#1` | 收纳达人 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_000#1` | 工匠精神·α | self_consume | M07a | -0.15 | ✅ ok | 自身消耗 → -0.15（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_001#1` | 工匠精神·β | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_010#1` | 麻烦制造者 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_011#1` | 特立独行 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_020#1` | “可靠”助手 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_100#1` | 量体裁衣 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_101#1` | 虔信 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `manu_prod_spd&limit&cost_110#1` | 独当一面 | self_consume | M07a | 0.25 | ✅ ok | 自身消耗 → 0.25（1 条实例：只作用自身） | ✅ |
| `meet_spd&bd_000#1` | 远见 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `meet_spd&bd_010#1` | 未来之途 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `meet_spd&cost_100#1` | 逻辑推理 | self_consume | M07a | 2 | ✅ ok | 自身消耗 → 2（1 条实例：只作用自身） | ✅ |
| `meet_spd&cost_condChar_000#1` | 双面间谍 | self_consume | M07a | 2 | ✅ ok | 自身消耗 → 2（1 条实例：只作用自身） | ✅ |
| `meet_spd&cost_condChar_001#1` | “职业操守”·α | self_consume | M07a | 1 | ✅ ok | 自身消耗 → 1（1 条实例：只作用自身） | ✅ |
| `meet_spd&cost_condChar_002#1` | 我自己的愿望 | self_consume | M07a | 2 | ✅ ok | 自身消耗 → 2（1 条实例：只作用自身） | ✅ |
| `meet_spd&cost_condChar_011#1` | “职业操守”·β | self_consume | M07a | 1 | ✅ ok | 自身消耗 → 1（1 条实例：只作用自身） | ✅ |
| `meet_spd&cost_condChar_020#1` | 专业经理·α | self_consume | M07a | 1 | ✅ ok | 自身消耗 → 1（1 条实例：只作用自身） | ✅ |
| `meet_spd&cost_condChar_021#1` | 专业经理·β | self_consume | M07a | 1 | ✅ ok | 自身消耗 → 1（1 条实例：只作用自身） | ✅ |
| `meet_spd&sami_000#1` | 冰原游弋 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `meet_spd&sami_100#1` | 人情练达 | self_consume | M07a | 0.5 | ✅ ok | 自身消耗 → 0.5（1 条实例：只作用自身） | ✅ |
| `power_rec_spd&cost_000#1` | 热情澎湃 | self_consume | M07a | -0.52 | ✅ ok | 自身消耗 → -0.52（1 条实例：只作用自身） | ✅ |
| `power_rec_spd&cost_010#1` | 外卖水果挞 | self_consume | M07a | -0.3 | ✅ ok | 自身消耗 → -0.3（1 条实例：只作用自身） | ✅ |
| `trade_cost&bd2_000#1` | 跋山涉水 | facility_consume | M05 | -0.1 | ✅ ok | 同设施全体消耗 → -0.1（2 条实例：同设施全体（含自身）） | ✅ |
| `trade_cost&bd2_000#2` | 跋山涉水 | facility_consume | M05 | -0.01 | ✅ ok | 同设施全体消耗 → -0.06（2 条实例：同设施全体（含自身）） | ✅ |
| `trade_cost&bd2_001#1` | 万里传书 | facility_consume | M05 | -0.1 | ✅ ok | 同设施全体消耗 → -0.1（2 条实例：同设施全体（含自身）） | ✅ |
| `trade_cost&bd2_001#2` | 万里传书 | facility_consume | M05 | -0.02 | ✅ ok | 同设施全体消耗 → -0.12（2 条实例：同设施全体（含自身）） | ✅ |
| `trade_cost_000#1` | 暖场 | self_consume | M07a | -0.1 | ✅ ok | 自身消耗 → -0.1（1 条实例：只作用自身） | ✅ |
| `trade_ord_limit&cost_000#1` | 谈判 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_limit&cost_P_000#1` | 醉翁之意·α | self_consume | M07a | -0.1 | ✅ ok | 自身消耗 → -0.1（1 条实例：只作用自身） | ✅ |
| `trade_ord_limit&cost_P_001#1` | 醉翁之意·β | self_consume | M07a | -0.1 | ✅ ok | 自身消耗 → -0.1（1 条实例：只作用自身） | ✅ |
| `trade_ord_limit&cost_P_010#1` | 默契 | self_consume | M07a | -0.3 | ✅ ok | 自身消耗 → -0.3（1 条实例：只作用自身） | ✅ |
| `trade_ord_limit&cost_P_020#1` | 未偿还的债务 | self_consume | M07a | -0.1 | ✅ ok | 自身消耗 → -0.1（1 条实例：只作用自身） | ✅ |
| `trade_ord_long_000#1` | 投资·α | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_long_010#1` | 投资·β | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_spd&cost_000#1` | 交际 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_spd&cost_P_000#1` | 恩怨 | self_consume | M07a | 0.3 | ✅ ok | 自身消耗 → 0.3（1 条实例：只作用自身） | ✅ |
| `trade_ord_vodfox_000#1` | 低语 | facility_consume | M05 | 0.25 | ✅ ok | 同设施全体消耗 → 0.25（2 条实例：同设施全体（含自身）） | ✅ |
| `trade_ord_wt&cost_000#1` | 裁缝·α | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_wt&cost_001#1` | 手工艺品·α | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_wt&cost_002#1` | 鉴定师的眼光 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_wt&cost_003#1` | 懂行 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_wt&cost_004#1` | 千金的眼光 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_wt&cost_010#1` | 裁缝·β | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_wt&cost_011#1` | 手工艺品·β | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |
| `trade_ord_wt&cost_012#1` | 鉴定师的手段 | self_consume | M07a | -0.25 | ✅ ok | 自身消耗 → -0.25（1 条实例：只作用自身） | ✅ |

## 四、逐条 buff（上游 755 条台账）

| buff | 名称 | 房间 | 轴 A | 模板 | 已建模 | 心情 clause | 核对 |
|---|---|---|---|---|---|---|---|
| `control_allCost_condChar[000]` | 浮生得闲 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_bd_spd[000]` | 老友相聚 | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_clue_cost&faction[990]` | 反抗者 | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_clue_cost[000]` | 神经质 | CONTROL | A1 | M05 | yes | 1 | ✅ 全部核对 |
| `control_clue_cost[010]` | 至察 | CONTROL | A1 | M05 | yes | 1 | ✅ 全部核对 |
| `control_clue_cost[011]` | 断事如神 | CONTROL | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `control_clue_faction[070]` | “一千封信” | CONTROL | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `control_costToBD[000]` | “山河远阔” | CONTROL | A3 | M16 | no | 0 | —（非心情，登记不建模） |
| `control_dorm_bd[000]` | 偶像光环 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_dorm_rec2[000]` | 羁绊相生 | CONTROL | A1 | M01 | yes | 2 | ✅ 全部核对 |
| `control_dorm_rec[000]` | 领袖 | CONTROL | A1 | M01 | yes | 1 | ✅ 全部核对 |
| `control_dorm_rec[001]` | 战纹 | CONTROL | A1 | M01 | yes | 1 | ✅ 全部核对 |
| `control_dorm_rec[002]` | 巡心 | CONTROL | A1 | M01 | yes | 1 | ✅ 全部核对 |
| `control_dorm_rec_tag[001]` | 无言的慈爱 | CONTROL | A1 | M01 | yes | 1 | ✅ 全部核对 |
| `control_facCostReset[000]` | 杯莫停 | CONTROL | A2 | M13 | yes | 1 | ✅ 全部核对 |
| `control_hire_spd&bd[000]` | 可靠伙伴 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_hire_spd[000]` | 感染力 | CONTROL | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `control_hire_spd_all[000]` | 办公室年度人物 | CONTROL | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `control_meeting&mp_cost[000]` | 英雄的骄傲·α | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_meeting&mp_cost[100]` | 英雄的骄傲·β | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_meeting&ord[000]` | 同谋·α | CONTROL | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `control_meeting&ord[001]` | 同谋·β | CONTROL | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `control_meeting_bd[000]` | “是，团长！” | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_meeting_spd&bd[000]` | 勤学苦练 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_mp&meet_spd[000]` | 成效优先 | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_mp_aegir1[000]` | 潮汐守望 | CONTROL | A1 | M07b | yes | 3 | ✅ 全部核对 |
| `control_mp_aegir2[000]` | 集群狩猎·α | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_aegir2[010]` | 集群狩猎·β | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_bd&trade[000]` | 演技的怪物 | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_mp_bd2[000]` | 团队合作 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_mp_bd[000]` | 情报储备 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_mp_bd[010]` | 乌萨斯特饮 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_mp_bd_cost_expand[000]` | 孤光共照 | CONTROL | A1 | M02b | yes | 2 | ✅ 全部核对 |
| `control_mp_cost&bd1[000]` | "不以物喜" | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&bd2[000]` | "不以己悲" | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&bd2[010]` | 耐力回复 | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&bd3[000]` | 生活的重压 | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&bd_up[000]` | 知我为我 | CONTROL | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&faction2[000]` | 坚毅随和 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&faction[000]` | 德才兼备 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&faction[020]` | 学生会会长 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&faction[030]` | 幕后指挥 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&faction[900]` | 异格者 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost&faction[990]` | 彩虹小队 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[000]` | 左膀右臂 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[001]` | S.W.E.E.P. | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[002]` | 零食网络 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[003]` | 清理协议 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[004]` | 替身 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[005]` | 必要责任 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[006]` | 护卫 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[007]` | 小小的领袖 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[008]` | 独善其身 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[009]` | 笑靥如春 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[010]` | 金盏花诗会 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[011]` | 捍卫之道 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[012]` | 博识生手 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[013]` | 点滴关照 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost[014]` | 总工程师 | CONTROL | A1 | M03 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost_double[000]` | 魔王传承 | CONTROL | A1 | M04 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost_double[001]` | “未完的故事” | CONTROL | A1 | M04 | yes | 1 | ✅ 全部核对 |
| `control_mp_cost_reset[000]` | 互为半身 | CONTROL | A2 | M13 | yes | 1 | ✅ 全部核对 |
| `control_mp_expand_double[000]` | 巴别塔之帜 | CONTROL | A1 | M02b | yes | 2 | ✅ 全部核对 |
| `control_mp_lonely[000]` | 公事公办 | CONTROL | A1 | M02c | yes | 1 | ✅ 全部核对 |
| `control_mp_psk[000]` | 红松的骑士 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_pow_bot[000]` | 我寻思能行 | CONTROL | A4 | X10 | no | 0 | —（非心情，登记不建模） |
| `control_prod_bd_spd[000]` | 丰富工作经验 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_prod_bd_spd[010]` | 丰富工作经验 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_prod_fraction[000]` | 烛骑士微光 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_prod_spd[000]` | 最高权限 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_prod_spd[1000]` | 最高权限 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_prod_spd[999]` | 领袖魅力 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_prod_tra_spd[000]` | 权变 | CONTROL | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `control_token_prod_spd2[000]` | 以身作则 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_token_prod_spd3[000]` | 共事情谊 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_token_prod_spd[000]` | 超频 | CONTROL | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `control_token_tra_spd[000]` | 秘传交涉术 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_limit&spd2[000]` | 家族认可 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_limit&spd3[000]` | 商业版图 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_limit&spd[000]` | 精密计算 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_limit&spd[010]` | 运筹好手 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_spd[000]` | 合作协议 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_spd[010]` | 大小姐 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_spd[020]` | 朝气蓬勃 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_tra_spd[030]` | 情报主脑 | CONTROL | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `control_train_spd[010]` | S.W.E.E.P.主管 | CONTROL | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `control_train_spd[011]` | 断金之交 | CONTROL | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `control_train_spd[012]` | 下城人脉 | CONTROL | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `control_upMeetingSpeed[000]` | 世事洞明 | CONTROL | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `control_upMeetingSpeed[100]` | 期冀之汇 | CONTROL | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `dorm_bd_num[000]` | 无声共鸣 | DORMITORY | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `dorm_exchangeAp[000]` | 患难之交 | DORMITORY | A2 | M15a | yes | 1 | ✅ 全部核对 |
| `dorm_hireToRecAll[000]` | 寻同路人 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_hireToRecAll[001]` | 无瑕心·α | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_hireToRecAll[021]` | 无瑕心·β | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_powToRecAll[000]` | 柔和微光·α | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_powToRecAll[010]` | 柔和微光·β | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_recExcludeOther[000]` | 自律 | DORMITORY | A2 | M14 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&bd[000]` | 无词颂歌 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&group[000]` | 调众口 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&lv[000]` | 睡前必听故事 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&lv[100]` | 死前必做清单 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&oneself[000]` | 慵懒 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&oneself[001]` | 嗜睡 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&oneself[010]` | 超脱 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&oneself[011]` | 挣脱 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&oneself[012]` | 解脱 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&oneself[021]` | 牧歌 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&oneself[022]` | 天生丽质 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&oneself[042]` | “归乡” | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&profession[000]` | 火山温泉浴 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&single[000]` | 小酌怡情 | DORMITORY | A1 | M11 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&tag[000]` | 资深料理人 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&tired[000]` | 芬芳疗养·β | DORMITORY | A1 | M12 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all&tired[100]` | 净化呼吸 | DORMITORY | A1 | M12 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&unfull[000]` | 倾听者 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all&unfull[001]` | 倾谈者 | DORMITORY | A1 | M08 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_all[000]` | 鼓舞 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[010]` | 小提琴独奏 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[011]` | 偶像 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[012]` | 沁人心脾 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[013]` | 领袖 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[014]` | 大锅饭·α | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[017]` | 静心仪式·α | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[020]` | 冬将军 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[021]` | 提灯女神 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[022]` | 狮心王 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[023]` | 芬芳疗养·α | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[024]` | 大锅饭·β | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[025]` | 乡野笛音 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[026]` | 利他主义 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[027]` | 静心仪式·β | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[028]` | 明星效应 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[029]` | 沉静心灵 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_all[999]` | 毛茸茸的抚慰 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_bd_dungeon[000]` | 森西大食堂 | DORMITORY | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `dorm_rec_bd_n1[000]` | 梦境呓语 | DORMITORY | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `dorm_rec_bd_n1[100]` | 琴键漫步 | DORMITORY | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `dorm_rec_bd_n1_n2[000]` | 睡前故事 | DORMITORY | A1 | M08 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_bd_n1_n3[000]` | 慢板行歌 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself2[000]` | “独处” | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_oneself2[001]` | 赋格融汇 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_oneself[000]` | 独处 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself[001]` | 幼狼情性 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself[002]` | 哪里都是兔子洞 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself[010]` | 狂热 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself[011]` | 麻烦回避者 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself[020]` | 悲歌 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself[030]` | 隐形的美食家 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_oneself[040]` | 难得休憩 | DORMITORY | A1 | M10 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single&oneself[000]` | 活泼 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[001]` | 探险家的热情 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[010]` | 烘焙 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[011]` | 烹饪 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[012]` | Give me five | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[020]` | 和谐 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[021]` | 喀兰圣女 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[030]` | 使徒 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&oneself[040]` | 甜甜圈派对 | DORMITORY | A1 | M10 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single&tag[000]` | 狩猎好帮手 | DORMITORY | A1 | M09 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single[000]` | 医疗服务 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[001]` | 疗养 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[010]` | 善解人意 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[020]` | 慈悲 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[030]` | 天启 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[031]` | 维多利亚文学 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[032]` | 心理疏导 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[033]` | 恩典颂歌 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single[1010]` | 善解人意 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_single_P[000]` | 沏茶 | DORMITORY | A1 | M09 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single_P[001]` | 烤肉大师 | DORMITORY | A1 | M09 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single_P[002]` | 毒剂师之友 | DORMITORY | A1 | M09 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single_power[000]` | 降生于冰寒 | DORMITORY | A1 | M09 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single_power[001]` | 圣城趣事通 | DORMITORY | A1 | M09 | yes | 2 | ✅ 全部核对 |
| `dorm_rec_single_power[100]` | 天生乐天派 | DORMITORY | A1 | M09 | yes | 1 | ✅ 全部核对 |
| `dorm_rec_toone[000]` | 头号陪练 | DORMITORY | A1 | M17 | yes | 1 | ✅ 全部核对 |
| `hire_spd&clue2[230]` | 内幕 | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue2[250]` | 人望 | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue2[260]` | 旧识新交 | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue[010]` | 天灾信使·α | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue[100]` | 洞悉人心 | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue[101]` | 好事之徒 | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue[110]` | 交游广阔 | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue[120]` | “号外！” | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd&clue[121]` | 街头法则 | HIRE | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[000]` | 人事管理·α | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[001]` | 人事管理·β | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[010]` | 天灾信使·α | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[011]` | 天灾信使·β | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[012]` | 节目邀约 | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[013]` | 前任金牌接线员 | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[020]` | 心理学 | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[030]` | WRITER | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[031]` | B-girl | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd[032]` | 不停歇的电话 | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_bd_n1[000]` | 追忆 | HIRE | A3 | M16 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_bd_n1_n1[100]` | 巡游 | HIRE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_bd_n1_n1[200]` | 救援队·灾后普查 | HIRE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_bd_n1_n1[300]` | 心声图绘 | HIRE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_blitz[000]` | 语言学 | HIRE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_cost&char[000]` | 圣女声望 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost&char[001]` | 雪境归心 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost&clue[000]` | 救援队·保证体力 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost&clue[001]` | 救援队·外界联络 | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_cost&extra[000]` | 用人唯才 | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_cost[010]` | 天灾信使·α | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_cost[100]` | 救援队·珠算 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[101]` | 宫廷礼仪 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[110]` | 救援队·资源清点 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[111]` | 特殊渠道 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[112]` | 踏坊寻味·α | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[120]` | 威权谕使 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[121]` | 踏坊寻味·β | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[200]` | 准时下班 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[210]` | 法为正典 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[220]` | 永不停歇·α | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[230]` | 永不停歇·β | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_cost[999]` | 独立调查 | HIRE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `hire_spd_dorm&lv[000]` | 梅兰德侦探·α | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_dorm&lv[010]` | 梅兰德侦探·β | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `hire_spd_tag[000]` | “泰拉的方舟” | HIRE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `manu_bd_to_bd[000]` | 古老巫术 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_constrLv[000]` | 绘图设计 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_cost[000]` | 春雷响，万物长 | MANUFACTURE | A1 | M05 | yes | 1 | ✅ 全部核对 |
| `manu_cost_all[000]` | 团队精神 | MANUFACTURE | A2 | M13 | yes | 1 | ✅ 全部核对 |
| `manu_formula_cost[000]` | Vlog | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_formula_limit[0000]` | 剪辑·α | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_limit[010]` | 剪辑·β | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_limit[020]` | 合理利用 | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd&bd[000]` | 战阵领袖 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd&bd[001]` | 情同手足 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd&cost[000]` | 小奇思 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_formula_spd&cost[001]` | 净味香氛 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_formula_spd&cost_bd[000]` | 挑大梁 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_formula_spd&cost_bd[100]` | 造价高昂 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd&dorm&lv[000]` | 齐心沙盗 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd&limit&cost[000]` | 镜中影 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_formula_spd&limit&cost[010]` | 戏中人 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_formula_spd&limit&cost[100]` | “连轴转” | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_formula_spd[000]` | 胜利之计 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[010]` | 作战指导录像 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[011]` | 作战指导录像 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[020]` | 拳术指导录像 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[022]` | 逆境荣光 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[030]` | 公证所教习·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[031]` | 公证所教习·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[100]` | 金属工艺·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[101]` | 金属工艺·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[102]` | 金属工艺·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[110]` | 金属工艺·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[200]` | 源石工艺·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[201]` | 地质学·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[210]` | 源石工艺·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[211]` | 地质学·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[212]` | 源石研究 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[213]` | 火山学家 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd[214]` | 源岩解析 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_formula_spd_P[000]` | 患难拍档 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_bd[000]` | 实干的寡言者 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_cost_min[001]` | “我睡过了” | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_limit&cost[0000]` | 拾荒者 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[000]` | 拾荒者 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[001]` | 磐蟹·阿盘 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[002]` | “都想要” | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[003]` | 智慧之境 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[010]` | 囤积者 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[011]` | 无畏豪情 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[012]` | 午休好去处 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[020]` | 探险者 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[021]` | 掘进工程 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_limit&cost[1020]` | 收纳达人 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&bd[000]` | 社群的意义 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&fraction[000]` | 重聚时光 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&limit&bd[000]` | 可靠的随从们 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&limit&cost[000]` | 工匠精神·α | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[001]` | 工匠精神·β | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[010]` | 麻烦制造者 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[011]` | 特立独行 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[020]` | “可靠”助手 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[100]` | 量体裁衣 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[101]` | 虔信 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[110]` | 独当一面 | MANUFACTURE | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `manu_prod_spd&limit&cost[200]` | 得心应手 | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&limit&cost[300]` | 行动派 | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&limit[000]` | 仓库整备·α | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&limit[001]` | 仓库整备·β | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&manu[000]` | 科学改造 | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&manu[100]` | 流程优化 | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&power[000]` | 自动化·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&power[010]` | 自动化·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&power[020]` | 仿生海龙 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&trade[000]` | 再生能源 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd&trade[1000]` | 原质塑金副产物 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[000]` | 标准化·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[001]` | 莱茵科技·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[002]` | 红松骑士团·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[003]` | 磐蟹·豆豆 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[004]` | 差遣使魔·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[010]` | 标准化·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[011]` | 莱茵科技·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[012]` | 红松骑士团·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[014]` | 差遣使魔·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[020]` | 咪波·制造型 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[021]` | 莱茵科技·γ | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd[1000]` | 标准化·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_addition&cost[000]` | 窗外雪啸 | MANUFACTURE | A3 | M16 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_addition[030]` | 急性子 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_addition[031]` | “等不及” | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_addition[040]` | 慢性子 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_addition[041]` | 延时摄影 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_addition[100]` | 例行清扫 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[000]` | 念力 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[010]` | 意识实体 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[100]` | 机械辅助·α | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[110]` | 机械辅助·β | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[200]` | 逐水草 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[201]` | 问枯荣 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[300]` | 稻禾厚，顺秋收 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd[400]` | 意想不到的美味 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_bd_n1[000]` | 超感 | MANUFACTURE | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_double[000]` | “搭把手！” | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_double[100]` | 盛餐的回报 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_reduce[000]` | 模糊视线 | MANUFACTURE | A3 | M16 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_train&lv[000]` | 手艺人 | MANUFACTURE | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_variable2[000]` | 配合意识 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_variable3[000]` | 大就是好！ | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_prod_spd_variable[000]` | 回收利用 | MANUFACTURE | A4 | X02 | no | 0 | —（非心情，登记不建模） |
| `manu_skill_change[000]` | 意识兼容 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_skill_limit[000]` | 勘探背包 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_skill_spd1[000]` | 意识协议 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_skill_spd1[010]` | 源石技艺理论应用 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_skill_spd1[020]` | 打工心得 | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_token_prod_spd[000]` | 机械精通·α | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `manu_token_prod_spd[010]` | 机械精通·β | MANUFACTURE | A4 | X01 | no | 0 | —（非心情，登记不建模） |
| `meet_flag[010]` | 哥伦比亚史 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_flag[040]` | 耶拉冈德 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_flag[050]` | 甄别 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_flag[060]` | 插旗 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_flag[070]` | 情报整理 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&bd[000]` | 远见 | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&bd[010]` | 未来之途 | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&bd[100]` | 杀手的假期 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&bd[999]` | 跨领域交流 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&clue[000]` | 广交义友 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&condChar_mustget[000]` | 文献学顾问 | MEETING | A3 | M16 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&condChar_mustget[100]` | 暗线 | MEETING | A3 | M16 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&cost[000]` | 线索搜集·α | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&cost[100]` | 逻辑推理 | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&cost_condChar[000]` | 双面间谍 | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&cost_condChar[001]` | “职业操守”·α | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&cost_condChar[002]` | 我自己的愿望 | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&cost_condChar[011]` | “职业操守”·β | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&cost_condChar[020]` | 专业经理·α | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&cost_condChar[021]` | 专业经理·β | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&exchange[000]` | 无辜笑脸 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&exchange[001]` | 特殊渠道顾问 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&exchange[999]` | 虎狼丸的报恩 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&sami[000]` | 冰原游弋 | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&sami[100]` | 人情练达 | MEETING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `meet_spd&sami[110]` | 情报探查 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[000]` | 线索搜集·α | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[010]` | 守望者 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[020]` | 信使·企鹅物流 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[030]` | 联络员 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[031]` | B.P.R.S. | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[040]` | 讯使 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[041]` | 雪境守望 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[050]` | 军师 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[060]` | 信使·罗德岛制药 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[070]` | 传讯者 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[071]` | 没落贵族 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[100]` | 警司 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd&team[110]` | 星象学 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[000]` | 线索搜集·α | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[0020]` | 线索搜集·β | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[010]` | 化影 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[011]` | 泡茶高手 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[020]` | 线索搜集·β | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[021]` | 好奇心 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[022]` | 社交达人 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[023]` | 坎贝尔之名 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[030]` | 占卜 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[031]` | 追踪者 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[032]` | 领袖外交 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[1000]` | 线索搜集·α | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[1010]` | 线索搜集·β | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd[1020]` | 线索搜集·β | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_Owned[000]` | 显眼的调查者 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_bd[000]` | 情报专家 | MEETING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_bd[001]` | 饱餐的干劲 | MEETING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_bd[002]` | 扶危行侠 | MEETING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_condChar[000]` | 得心应手 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_ext&P[000]` | 不泯童心 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_hast[000]` | 聚影 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned&exchange[000]` | 眼观六路 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned[000]` | 不容遗漏·α | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned[001]` | 情报分析 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned[002]` | 见微知著 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned[003]` | 萍踪浪迹 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned[004]` | 资深信使 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned[010]` | 不容遗漏·β | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_spd_notOwned_P[000]` | 隐秘专家 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_team&char[000]` | 交个朋友 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_team[020]` | 皇家探员（自称） | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_team[050]` | 秘密搜查 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_team[060]` | 通讯员 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `meet_team[070]` | 风声 | MEETING | A4 | X05 | no | 0 | —（非心情，登记不建模） |
| `power_count[000]` | 晨曦 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_prod_spd_P[000]` | “滴滴，启动！” | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_drone[000]` | 巡线框架 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_rhine[000]` | 生态科主任 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd&addition[000]` | 技术交流·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd&addition[001]` | 技术交流·β | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd&cost[000]` | 热情澎湃 | POWER | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `power_rec_spd&cost[010]` | 外卖水果挞 | POWER | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `power_rec_spd&dorm&lv[000]` | 灵河共鸣 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[000]` | 备用能源 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[0010]` | 鸡电工程 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[001]` | 热能充能·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[002]` | 光能充能·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[003]` | 电磁充能·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[004]` | 挽歌充能·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[005]` | 灵河充能·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[006]` | 充能中 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[007]` | 雷法专精 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[008]` | 澎湃紊流 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[009]` | 澎湃紊流 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[010]` | 设备维护 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[011]` | 清洁能源 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[013]` | 高热充能 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[014]` | 脉冲电弧·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[015]` | 电磁充能·β | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[016]` | 聚能 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[017]` | 灯塔供能模块 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[020]` | 静电场 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[021]` | 脉冲电弧·β | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[022]` | 热能充能·γ | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[023]` | 电荷释放 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[024]` | 合法窃电 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[025]` | 穹顶物流管理·α | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[026]` | 穹顶物流管理·β | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[027]` | 迟到前六分钟 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd[1022]` | 热能充能·γ | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd_P2[999]` | 机械工学 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd_P[000]` | “愉快的对谈” | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd_P[001]` | 咒文共鸣 | POWER | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd_ext&faction[000]` | 维护中 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `power_rec_spd_ext&tag[000]` | 鸡励机制 | POWER | A4 | X04 | no | 0 | —（非心情，登记不建模） |
| `trade_cost&bd2[000]` | 跋山涉水 | TRADING | A1 | M05 | yes | 2 | ✅ 全部核对 |
| `trade_cost&bd2[001]` | 万里传书 | TRADING | A1 | M05 | yes | 2 | ✅ 全部核对 |
| `trade_cost[000]` | 暖场 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_against[000]` | 违约索赔·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_against[010]` | 违约索赔·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_closure[000]` | 特别订单 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_law[000]` | 合同法 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_limit&cost[000]` | 谈判 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_limit&cost_P[000]` | 醉翁之意·α | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_limit&cost_P[001]` | 醉翁之意·β | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_limit&cost_P[010]` | 默契 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_limit&cost_P[020]` | 未偿还的债务 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_limit&trade&lv[000]` | 多面逢源 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_limit&trade&lv[001]` | 钱不我待 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_limit_count[000]` | 市井之道 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_limit_diff[000]` | 摊贩经济 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_line_durin[010]` | 际崖居民 | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_line_gold[000]` | 订单流可视化·α | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_line_gold[010]` | 订单流可视化·β | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_long[000]` | 投资·α | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_long[010]` | 投资·β | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_par&per[000]` | 白手起家·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_par&per[001]` | 白手起家·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_pepe[000]` | 慧眼独到 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&cost[000]` | 交际 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_spd&cost_P[000]` | 恩怨 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_spd&dorm&lv[000]` | 虔诚筹款·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&dorm&lv[010]` | 虔诚筹款·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&formula[000]` | 精准排期 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&gold[000]` | 物流规划·α | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&gold[010]` | 物流规划·β | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&gold[100]` | 销路宣发 | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit&bd[000]` | 可爱的艾露猫 | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[000]` | 订单管理·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[001]` | 订单管理·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[010]` | 供应管理 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[020]` | 喀兰贸易·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[021]` | 喀兰贸易·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[022]` | 喀兰之主 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[030]` | 企鹅物流·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[031]` | 使命必达 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[032]` | 峯驰物流 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[033]` | 少当家 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[034]` | 订单分发·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[035]` | 大巴扎管理学 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[036]` | 半身人公会代表 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[100]` | 威压 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit[101]` | 不怒自威 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&limit_tag[000]` | 队长的自觉 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&meet[000]` | 新城贸易 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&meet[010]` | 天生的顾问 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&multiPar[000]` | 订单分发·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&multiPar[100]` | 相伴 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&par[000]` | 外贸决议·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&par[001]` | 外贸决议·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&share[000]` | 代为说项 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&share[001]` | 勤俭经营·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&share[002]` | 勤俭经营·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&tag[000]` | 订单分发·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&tag[010]` | 精英小队 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&tag[020]` | “孺子可教！” | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd&wt[000]` | 天真的谈判者 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[000]` | 订单分发·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[001]` | 订单分发·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[010]` | 企鹅物流·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[011]` | 企鹅物流·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[020]` | 物流专家 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[021]` | 名流欢会 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[022]` | 气氛组 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd[1001]` | 订单分发·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_bd[000]` | 徘徊旋律 | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_bd[010]` | 怅惘和声 | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_bd[100]` | 熟悉的味道 | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_bd_n1[000]` | 乐感 | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_bd_n2[000]` | “愿者上钩” | TRADING | A4 | X09 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_ext[000]` | 对陆接洽代表·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_ext[001]` | 对陆接洽代表·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_ext[020]` | 家族经营·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_ext[021]` | 家族经营·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_par[000]` | 帮派指南针 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_par[001]` | 同城加急单 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_variable2[000]` | 天道酬勤·α | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_variable2[001]` | 天道酬勤·β | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_variable3[000]` | 冠军风采 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_spd_variable[000]` | 招商引资 | TRADING | A4 | X03 | no | 0 | —（非心情，登记不建模） |
| `trade_ord_vodfox[000]` | 低语 | TRADING | A1 | M05 | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[000]` | 裁缝·α | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[001]` | 手工艺品·α | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[002]` | 鉴定师的眼光 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[003]` | 懂行 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[004]` | 千金的眼光 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[010]` | 裁缝·β | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[011]` | 手工艺品·β | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `trade_ord_wt&cost[012]` | 鉴定师的手段 | TRADING | A1 | M07a | yes | 1 | ✅ 全部核对 |
| `train_cost&profession[140]` | 工作狂 | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_cost&profession[320]` | 过量训练 | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_cost&profession[340]` | 索然无味 | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_cost&profession[350]` | 何须解脱 | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_cost&profession[360]` | 变异 | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_cost&profession[380]` | 斗争渴望 | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_spd&level[000]` | 苦心孤诣 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[010]` | 先锋专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[020]` | 近卫专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[030]` | 重装专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[040]` | 狙击专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[050]` | 术师专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[052]` | 术师专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[060]` | 辅助专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[080]` | 特种专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[110]` | 战术研习 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[111]` | 先锋专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[112]` | 先驱者共识 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[120]` | 信影流 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[130]` | 极地生存 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[150]` | 一知半解 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[180]` | 陷阱对焦 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[220]` | 苦行 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[230]` | 阵地经验 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[240]` | 拂弦 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[250]` | 博览群书 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[270]` | 护理专精 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[320]` | 尽心尽力 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[321]` | 近卫专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[322]` | 勇气传承 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[440]` | 以身作则 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[620]` | 实战指导 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[630]` | 最终调试 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[640]` | 伺机而动 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[650]` | 死焰指引 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[660]` | 同化 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession2[680]` | 阿戈尔战术 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[130]` | 重装专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[131]` | 实战技巧：驭法铁卫 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[140]` | 实战技巧：速射 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[150]` | 近卫专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[151]` | 实战技巧：斗士 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[160]` | 近卫专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[161]` | 实战技巧：领主 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[170]` | 实战技巧：攻城手 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[180]` | 医疗专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[181]` | 实战技巧：行医 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[182]` | 医疗专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[183]` | 实战技巧：链愈师 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[184]` | 医疗专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[190]` | 辅助专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession3[191]` | 实战技巧：游击手 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[010]` | 先锋专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[011]` | 先锋专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[020]` | 近卫专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[021]` | 近卫专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[030]` | 重装专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[031]` | 重装专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[040]` | 狙击专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[041]` | 狙击专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[050]` | 术师专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[051]` | 术师专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[060]` | 辅助专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[061]` | 辅助专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[070]` | 医疗专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[071]` | 医疗专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[080]` | 特种专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[081]` | 特种专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[1020]` | 近卫专精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[1081]` | 特种专精·β | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[110]` | 入世 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[120]` | 剑术记忆 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[130]` | 般若 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[140]` | 黑矢 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[150]` | 尽在掌握 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[160]` | 共鸣 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[170]` | 精准手术 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd&profession[180]` | 假面魅影 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd[0000]` | 教官 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd[000]` | 教官 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd[001]` | 学无不精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd[002]` | 学无不精·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_bd&reduceTime[000]` | 思而后行 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_bd[000]` | 与人乐 | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf2[000]` | 破斩桎梏 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf2[001]` | 沉默为剑 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf3[000]` | 兴之所至·α | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf3[100]` | 兴之所至·β | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf3[999]` | 合理兼职 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[000]` | 剑与手炮 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[100]` | 红龙之血 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[110]` | 红龙之血 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[200]` | 女妖之力 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[201]` | “问题应当准确” | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[300]` | 恣意英杰 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[301]` | “我不懂啊” | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_doubleProf[310]` | “触类旁通” | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_power[000]` | 雪祀候补 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_power_down[000]` | “手段应当有效” | TRAINING | A1 | M07a | no | 0 | —（非心情，登记不建模） |
| `train_spd_reduceTime[000]` | 精神锻炼 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_reduceTime[001]` | 言语之义 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_tag[000]` | 骑士训练 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_tag[010]` | 崇高准则 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_tag[020]` | 战术指导·防守 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_tag[030]` | 战术指导·进攻 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_tag[040]` | 叙拉古狂欢 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_tag[1020]` | 战术指导·防守 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `train_spd_tag[1030]` | 战术指导·进攻 | TRAINING | A4 | X06 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_bonus1[000]` | 因果 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_bonus2[000]` | 业报 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost&dorm[000]` | 心相连 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost2[000]` | 热心修补匠 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost2[110]` | 大匠之心·α | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost2[111]` | 大匠之心·β | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[000]` | 放空 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[001]` | 宣传阵线 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[100]` | 一丝不苟 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[101]` | 谨慎加工 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[102]` | 长生的余裕 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[110]` | 灵感 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[111]` | 熔铸 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[112]` | 军事工程学 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[120]` | 极简实用主义 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[200]` | 便携蓄电池 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[220]` | 选矿学 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[221]` | 不倒霉的一天 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[300]` | 淡泊 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[400]` | 勤学不倦 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[500]` | 拍摄日愉快 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost3[600]` | 与历史对话 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost4[000]` | 执著 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost4[010]` | 苦修之律 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost4[011]` | 虔修之律 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost5[000]` | 熟极而流 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost5[100]` | 铁匠天赋 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost[000]` | 龙腾式无人机 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost[001]` | 自动化流水线 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_cost[010]` | 肆无忌惮 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[000]` | DIY·装置 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[020]` | DIY·异铁 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[021]` | 余料利用 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[030]` | DIY·聚酸酯 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[050]` | DIY·源岩 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[060]` | DIY·酮凝集·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[070]` | DIY·晶体 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_device[111]` | DIY·炽合金 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_dorm[000]` | “物尽其用” | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_dorm[001]` | “人善其事” | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_dorm[002]` | 意相通 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_drop[020]` | 无用的赠予 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_drop[030]` | 缓蚀技术 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_drop[040]` | 岩体锚固 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_free[000]` | 精打细算 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_frost[000]` | 机械工程 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_lolxh[000]` | 化猫 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[000]` | 技巧理论 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[010]` | 兵者诡道 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[011]` | 荒野生存 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[020]` | 登峰造极 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[021]` | 克己严修 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[030]` | 适应力 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[031]` | 獠牙的技艺 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[032]` | 灵感改装 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[100]` | 营养学 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[101]` | 高效回收 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[102]` | 气流传动 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[110]` | 药理学·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[1110]` | 过度发挥 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[111]` | 毒理学·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[112]` | 爆破材料学·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[113]` | 腐蚀科学·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[114]` | 草药学·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[115]` | 必修课程 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[116]` | 探井人·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[117]` | 熟手的尊严 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[118]` | 修旧如旧·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[119]` | 工业设计 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[120]` | 药理学·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[121]` | 毒理学·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[122]` | 爆破材料学·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[123]` | 腐蚀科学·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[124]` | 草药学·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[126]` | 探井人·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[128]` | 修旧如旧·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[129]` | 学者 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[139]` | 巫妖学识 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[160]` | 稀有金属辨识 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[200]` | 工程学 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[210]` | 斩铁裂钢 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[220]` | 结构力学 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[221]` | 突发好运 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[300]` | 特训记录 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[301]` | 被忽视的天赋 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_probability[310]` | 训练有素 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_recovery[000]` | “爆炸艺术” | WORKSHOP | A2 | M15b | no | 0 | —（非心情，登记不建模） |
| `workshop_formula_rub[000]` | 省省能更多 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_cost[000]` | 御灵之力·金 | WORKSHOP | A2 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability&ext[000]` | 工效模范·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability&ext[001]` | 工效模范·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[000]` | 专注·α | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[010]` | 能工巧匠 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[011]` | 多功能测绘仪 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[012]` | “炼金术” | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[013]` | 修修还能用 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[020]` | 专注·β | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[021]` | 老当益壮 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[030]` | 咪波·加工型 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[031]` | 舍弃的赘余 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[032]` | 天师府工艺 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[033]` | 蓝卡坞多面手 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[040]` | 未知技术 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[041]` | 技术阐明 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |
| `workshop_proc_probability[050]` | 理论革新 | WORKSHOP | A4 | X08 | no | 0 | —（非心情，登记不建模） |

## 五、已知差异与「待拍板」清单

（无）

## 六、怎么复现

```bash
.venv/Scripts/python.exe scripts/verify_skills.py --check    # 只看结论
.venv/Scripts/python.exe scripts/verify_skills.py --report   # 重写本报告
.venv/Scripts/python.exe -m unittest tests.test_skill_coverage -v   # 三层断言
```

本次 L3 用的上游：`E:\code_h\python\_agd\zh_CN\gamedata\excel\building_data.json`
