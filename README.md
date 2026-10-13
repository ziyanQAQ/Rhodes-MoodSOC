# Rhodes_MoodSOC

基于多班排班模拟干员心情变化，并在班次执行点结算换心情与闲置入宿，向 Web UI 和程序 API 提供曲线、布局快照、事件与原因解释。

> **当前状态：框架施工阶段。** 已建立 TypeScript/Vitest 工程配置，并完成 data 层目录快照与本地上游目录生成两条垂直切片；domain、application 用例、Adapter 和 UI 仍待实现。下列接口和模块只有明确标注为已实现的部分才代表源码能力。

## 核心行为

```text
排班基线 + 干员/设施目录 + 模拟设置
  → 按周期、班次和内部换班点推进
  → 重建原始布局并继承实时心情
  → 换心情 → 闲置入宿 → 重算速率
  → 生成不可变轨迹、布局快照和事件
  → UI/API 查询并展示
```

手动入宿修改并保存排班基线；换心情和闲置入宿只作用于模拟副本。Web UI 规划提供排班编辑、手动入宿、换心情设置、闲置入宿设置、时间轴回放、干员曲线和速率账本。

## 架构

目标依赖层为 `data → domain → application → presentation`，`infrastructure` 实现外部适配。应用层编排用例；领域层拥有规则；Web Worker 与程序 API 调用同一应用层。完整职责与依赖约束见 [架构设计](documents/架构设计.md) 和 [功能模块](documents/功能模块.md)。

## 文档

- [架构设计](documents/架构设计.md)：五层、MVC、领域模块、执行流程和验收标准
- [功能模块](documents/功能模块.md)：用例、状态写权限、UI 工作区和测试矩阵
- [协议格式](documents/协议格式.md)：输入、模拟、曲线、快照、明细、事件、Worker 和程序 API
- [入宿逻辑](documents/入宿逻辑.md)：闲置入宿候选、优先级、时序、锁定和不变式真源
- [干员信息](documents/干员信息.md)：稳定身份、名册和数据来源
- [技能分类](documents/技能分类.md)：分类轴、模板、技能目录和扩展规则
- [设施信息](documents/设施信息.md)：设施类型、容量、位次和布局约束
- [心情计算与技能结算](documents/心情计算与技能结算.md)：心情积分、速率账本、技能子句和待核实数据边界
- [施工总览](documents/施工/00-施工总览.md)：从 data 到 presentation 的实施顺序、接口和阶段门禁
- [源码框架](src/README.md)：当前源码目录、依赖方向和实现状态
- [AGENTS.md](AGENTS.md)：修改前方案确认、文档真源和阶段提醒约束

## 参考项目

`E:\code_h\python\Rhodes-MoodSOC` 提供心情和入宿规则参考；`E:\code_h\RIIC-Web\RIIC-Web` 提供浏览器 Worker 心情模拟及 UI 集成参考。它们不代表当前项目实现。数据和代码复用前须确认来源、版本与授权。

## data 目录生成

已接入本地 `ArknightsGameData` 归档的三份输入：`building_data.json`、`character_table.json` 和 `gamedata_const.json`。使用固定时间生成可复现目录：

```text
npm run data:build -- --source <ArknightsGameData>/zh_CN/gamedata/excel --output data/generated --generated-at <ISO-8601>
```

当前生成物及来源指纹见 [`data/generated/manifest.json`](data/generated/manifest.json)。原始上游文件不纳入本仓库；更新数据时必须重新运行黑盒测试和类型检查。

## 当前限制

- 已有 TypeScript/Vitest 工程配置和 data 层目录快照实现；domain、application 用例、infrastructure Adapter 和 presentation UI 尚未实现。
- 已接入真实上游目录文件的加载、规范化和生成；Worker、程序 API 或 Web UI 运行入口仍未实现。
- 当前验证命令：`npm test`、`npm run typecheck`。
- README 后续应随各层实现状态、运行方式和验证命令更新。
