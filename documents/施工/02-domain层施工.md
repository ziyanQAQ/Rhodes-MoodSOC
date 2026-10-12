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
