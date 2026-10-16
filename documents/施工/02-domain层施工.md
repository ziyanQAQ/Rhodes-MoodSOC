# domain 层施工

## 1. 目标与边界

domain 是 Model 的规则核心，接收规范化值对象和本次模拟冻结的只读 `CatalogSnapshot`，产生新快照、轨迹和事件。它不认识 data 的加载实现，也不认识 JSON、React、Worker、HTTP、文件、数据库、时间格式化或 RIIC-Web 类型。

完整入宿行为只引用 [`../入宿逻辑.md`](../入宿逻辑.md)；执行顺序和 DTO 名称只引用 [`../功能模块.md`](../功能模块.md) 与 [`../协议格式.md`](../协议格式.md)。

## 2. 推荐 Module

```text
src/domain/
  ids/             # 稳定 ID 值对象
  timeline/        # 班次和执行点
  roster/          # 名册引用与练度
  layout/          # 基线、位次、手动台账、模拟副本
  mood/            # 速率、积分、轨迹节点
  automation/      # 换心情、闲置入宿策略
  simulation/      # 编排所需领域输入、事件和结果
```

核心 Module：

- `Timeline`：生成班初以及严格小于班末的 12h 内部执行点。
- `Layout`：维护留洞、锁定、手动台账和基线/副本隔离。
- `MoodLedger`：以十进制定点计算 consume、recover、net，并钳位 `[0,24]`。
- `AutomationPolicy`：依据设置和快照产生换心情/闲置入宿决策，不直接持久化。
- `SimulationEngine`：在唯一规定顺序中协调领域模块，输出不可变 `SimulationRun`。

当前已实现 `src/domain/errors.ts`、`mood/decimal.ts`、`timeline/timeline.ts`、`roster/index.ts` 和 `layout/index.ts`：提供稳定领域错误、六位小数定点基础运算、班初和严格位于班内的 12h 执行点，以及名册、手动布局/台账和模拟副本。心情速率/积分、自动化决策和模拟编排仍待施工。

### 2.1 当前名册与布局 Interface

公开入口为 `src/domain/index.ts`；下面是已经实现的领域接口，不是程序 API DTO：

| 入口 | 输入与结果 | 调用约束 |
|---|---|---|
| `createRoster(entries, catalog)` | 返回不可变 `Roster`；`entries()` 查询全部记录、`operator(id)` 按 ID 查询 | 记录必须显式提供 `operatorId`、`elite`、`level`；不因清空位次而删除记录 |
| `createScheduleBaseline(shifts, roster, catalog, detached?)` | 返回初始 `revision = 0` 的 `ScheduleBaseline` | 输入为已规范化班次；全局名单缺省为空 |
| `baseline.snapshot()` / `detached()` / `notices()` | 查询班次快照、全局名单与本次导入冲突说明 | 返回冻结结果，不能作为可变草稿使用 |
| `baseline.apply(command)` | 返回新基线，`revision + 1` | 支持 `place`、`clear`、`lock`、`unlock-all`、`detach`；旧基线不变 |
| `baseline.simulation(shiftId)` | 从指定班次基线建立独立 `SimulationLayout` | 每次调用重新建立，不继承上一执行点自动布局 |
| `simulation.lockedPositions(count?)` | 返回按竖向正序选出的 `{facilityId, slotIndex}` | 缺省与完整规则引用 [`../入宿逻辑.md` §9](../入宿逻辑.md#9-锁定位置)；非安全整数拒绝 |
| `simulation.apply(command, lockPositionCount?)` | `place` / `clear` 返回新的模拟副本 | 不改基线、不写台账；所有自动写入必须传入本次运行相同的显式锁区设置 |

规范化输入要求与当前校验范围：

- 名册 ID 必须存在于 Catalog 且不重复。`elite` 使用 `PHASE_` 加非负整数的阶段标识，`level` 为正安全整数；这只是格式校验，不代表该角色确有相应精英阶段或等级上限。当前目录未提供角色等级上限，暂不做上限判定，也不默认补满练度或激活技能。
- 班次 ID 非空且唯一；设施 ID 在班内非空且唯一，可跨班引用同一设施。设施类型和等级必须存在于本次 Catalog；`slots.length` 必须恰好等于该阶段 `maxStationedNum`，空位显式为 `null`。短数组补洞、名称别名和旧文件解析属于后续导入适配器。
- 同一班次内干员不得重复占位，跨班重复合法。所有占位与全局名单 ID 都必须在名册中。设施实例数上限、跨班设施定义一致性等整体输入校验留给后续规范化用例，不能把本模块当作完整请求校验器。
- `manual` 可作为已保存台账显式传入；普通导入不生成台账。台账位次不得越界，人员必须关联本房间住户；重复台账项归并为空间/人员集合，空台账省略。
- 导入名单与占位冲突时清空冲突位次、撤对应手动锁并返回 `DETACHED_OCCUPANT_REMOVED`，包含班次、设施、位次和干员 ID；不删名册。交互式 `detach` 则保留未锁占位，并拒绝任何班次中已受手动保护的人员。
- `place` 默认只锁本次目标位及新住户；同班原位置清空留洞。`lock: false` 是不新增锁且不剔全局名单的布局编辑入口；清空和替换会清理离位人员身份。`lock` 可预留空位；`unlock-all` 未给班次时同时撤全部班次的位次锁与人员锁。
- 模拟写入拒绝触碰手动保护位次/人员及停用设施占位；配置锁区中有住户时不能换出，空位可填，填后受保护。这只是写入不变量，不替代未来的候选、黑名单、心情排序和换人策略。
- 错误抛出 `DomainError`，以稳定 `code` 和 `path` 识别；不存在的 ID 使用 `DOMAIN_UNKNOWN_ID`，状态/容量/锁定约束使用 `DOMAIN_INVARIANT_VIOLATION`。应用层尚未实现错误 DTO 转换。

实现只认识规范化稳定 ID，不生成设施展示名。旧格式补名、按类型与名称映射 ID、导出往返均为后续适配器职责。设施容量直接使用本次目录阶段，不把旧项目或人工测试夹具当成游戏数据。

### 2.2 下一施工切片

后续先确定心情基础数值的上游单位、技能条款和支持范围，再实现速率账本与积分；之后实现换心情与闲置入宿决策，最后由模拟编排统一执行顺序。每个切片仍须单独分析、提交方案并取得用户同意；不能因已有安全布局写入接口而宣称完整自动入宿已完成。

## 3. Interface 草案

```ts
interface SimulationEngine {
  run(input: SimulationInput, catalog: CatalogSnapshot): SimulationRun;
}
interface Timeline {
  points(schedule: Schedule): ReadonlyArray<ExecutionPoint>;
}
interface LayoutDraft {
  apply(command: LayoutCommand): LayoutDraft;
  snapshot(): LayoutSnapshot;
}
interface MoodCalculator {
  rate(state: RateContext): RateBreakdown;
  integrate(state: MoodState, rate: RateBreakdown, hours: Decimal): MoodState;
}
interface AutomationPolicy {
  decide(context: AutomationContext): ReadonlyArray<DomainEvent>;
}
```

这些 Interface 只暴露领域行为和不可变结果。`CatalogSnapshot` 是 application 在运行开始时装配的只读数据，不是 domain 反向读取目录的端口。内部候选排序、锁区判定、事件构造和速率账本隐藏在 implementation 内；测试穿过同一 seam 验证行为。

## 4. 必须实现的状态隔离

1. `ScheduleBaseline` 保存用户班次和手动台账；手动入宿命令返回新基线版本。
2. `SimulationLayout` 是每个执行点由当前班次原始布局重建的副本。
3. 心情在执行点连续继承，自动布局不继承；自动行为不得回写基线。
4. 同一执行点顺序固定为：重建原始布局 → 继承心情 → 换心情 → 闲置入宿 → 应用同刻手动心情指定 → 重算速率 → 积分。
5. 查询执行点返回全部同刻事件之后的状态，并保留 `before/after` 跳变节点。

## 5. 不变量与错误

必须守住 `入宿逻辑.md` 的 28 条不变量，尤其是：空洞不左移、锁区空位可填、锁区外才可换出、心情相等也可换、被换出者本点不再入队、黑名单只拦进入、自动逻辑不写台账。非法领域命令返回结构化领域错误，不用异常文本让 UI 分支。

建议错误分类：`DOMAIN_INVARIANT_VIOLATION`、`DOMAIN_INVALID_TRANSITION`、`DOMAIN_UNKNOWN_ID`、`DOMAIN_DECIMAL_INVALID`。错误应包含稳定 code、涉及 ID、字段路径或执行点，但不泄漏可变内部对象。

## 6. 扩展与禁止事项

新增技能、设施或自动化通过目录记录、模板、策略和注册表扩展。禁止修改稳定模拟循环来加入干员名特判；禁止让查询 Module 写状态；禁止以数组位置作为持久化身份；禁止在 domain 中引入协议 DTO。

## 7. 黑盒验收

先写并运行领域 Interface 黑盒测试，再实现：时间边界、同刻事件、十进制积分、速率账本、手动台账、28 条入宿不变量、重复运行确定性、基线不被自动行为修改。任何依赖浏览器或真实 Worker 的测试都不属于 domain 阶段。

当前基础切片已通过 `test/domain/decimal.test.ts`、`test/domain/timeline.test.ts`、`test/domain/roster.test.ts` 与 `test/domain/layout.test.ts` 的公开 Interface 黑盒验证，共 19 项；测试目录仅用于提交前验证，不进入 Git。相关命令为 `npm test -- test/domain/decimal.test.ts test/domain/timeline.test.ts test/domain/roster.test.ts test/domain/layout.test.ts`，并通过 `npm run typecheck`。

布局验收覆盖默认/显式锁区、停用宿舍、实际容量、手动摆位/替换/清空/预留、全部解锁、名单冲突、输入错误与基线/模拟副本隔离。尚未执行积分账本、同刻事件、真实技能、自动候选排序及完整 28 条入宿不变量验收；Worker、API 和 UI 当前也没有实现可供运行测试。
