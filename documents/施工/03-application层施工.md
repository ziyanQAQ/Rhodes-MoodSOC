# application 层施工

## 1. 目标与边界

application 是 Controller 编排层，也是程序 API 与 Web Worker 共享的用例入口。它把协议输入规范化为 domain 输入，调用领域 Module，保存/发布不可变结果并生成查询投影。它不实现心情公式、候选排序或 UI 状态。

## 2. 推荐文件与 Module

```text
src/application/
  ports.ts
  dto.ts
  use-cases/
    validate-input.ts
    edit-schedule.ts
    simulate.ts
    query-result.ts
    capabilities.ts
  projections/
  errors/
```

关键 Interface：

```ts
interface SimulationUseCases {
  validate(input: InputDto): ValidationDto;
  simulate(input: InputDto): SimulationReceiptDto;
  curve(query: CurveQueryDto): CurveDto;
  snapshot(query: SnapshotQueryDto): SnapshotDto;
  detail(query: DetailQueryDto): DetailDto;
  events(query: EventsQueryDto): EventsDto;
  entryCandidates(query: CandidatesQueryDto): CandidatesDto;
  idleCandidates(query: CandidatesQueryDto): CandidatesDto;
  settingsSchema(): SettingsSchemaDto;
}
interface SimulationResultStore {
  publish(result: SimulationRun): SimulationReceipt;
  get(id: SimulationId, revision: Revision): SimulationRun | undefined;
}
```

`SimulationResultStore` 是 application 定义的 port；内存、Worker 缓存或未来持久化实现都是 Adapter。查询 Interface 必须只读，返回 DTO 的副本或不可变值。

## 3. 用例流程

### 3.1 validateInput

按协议校验版本、字段路径、ID 引用、十进制字符串、限制和设置组合；再调用 domain 级不变量校验。返回稳定错误码与警告，不以中文消息驱动 UI。

### 3.2 editSchedule

接收 `scheduleRevision` 和手动布局命令，进行乐观并发检查；成功返回新草稿版本，冲突不覆盖较新草稿。手动入宿改变基线；不得修改已发布模拟。

### 3.3 simulate

冻结并规范化输入 → 通过 `CatalogReader` 读取并校验目录 → 形成只读 `CatalogSnapshot` → 调用 `SimulationEngine` → 发布 `simulationId + revision` → 返回结果索引和警告。输入设置变化必须产生新 revision。

### 3.4 查询用例

查询必须携带 `simulationId + revision`；找不到、版本过期或范围非法分别返回稳定查询错误。投影可提供曲线、快照、速率账本、统计、事件和候选，但不能为生成投影而改变领域结果。

## 4. DTO 与错误映射

DTO 遵守 [`../协议格式.md`](../协议格式.md)：跨进程数值为十进制字符串，时间用 `offsetHours`，稳定关联用 ID。应用层把目录/领域错误映射为 `CATALOG_*`、`VALIDATION_*`、`SIMULATION_*`、`QUERY_*`，保留 `path`、`details` 和 `retryable`。

应用层不得把 domain 对象直接序列化，也不得把 Adapter 的错误类型泄漏给调用方。取消请求必须阻止尚未发布的成功结果。

## 5. 黑盒验收

先写测试覆盖：同一输入和同一目录快照获得确定性 receipt；目录版本不匹配被拒绝；revision 与 simulationId 不混用；过期查询被拒绝；设置改变产生新结果；手动编辑有冲突保护；所有查询只读；领域错误能映射为协议错误；Worker 和程序 API 可调用同一 Interface。
