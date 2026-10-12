# infrastructure 层施工

## 1. 目标与边界

infrastructure 实现 application 的 ports，并把外部世界转换为规范 DTO。它包含 Worker、程序 API、文件目录加载、结果缓存、日志和 RIIC-Web 映射 Adapter；不包含领域规则。

## 2. 推荐 Adapter

```text
src/infrastructure/
  catalog/          # 上游文件读取与 CatalogReader 实现
  memory/            # 首期结果存储
  riic-web/          # BaseBlueprint 等外部模型映射
  worker/            # 消息生命周期与取消
  api/               # 程序 API/NDJSON 编解码
  serialization/    # 协议 DTO 校验与编码
```

### 2.1 Worker Adapter

Worker 负责请求 ID、消息校验、调用 application、保存最近结果、取消和错误序列化。它不计算心情、不做候选排序、不修改设置。每个请求 ID 在 Worker 生命周期内唯一；取消完成后不得再发送成功响应；过期 revision 不得覆盖当前 UI 结果。

### 2.2 RIIC-Web Adapter

只在 Adapter 内解析 `BaseBlueprint`、`OperBoxEntry`、`ManualScheduleDraft` 等外部类型，转换为本项目的 layout、roster、shift、ledger DTO。外部显示名、数组顺序或对象身份不得进入 domain 作为稳定关联；缺少映射时返回导入错误，不静默丢弃。

### 2.3 程序 API Adapter

程序 API 与 Worker 调用同一 application Interface。编解码层只负责 JSON/NDJSON、请求信封、错误信封和协议版本；不得复制 use case。HTTP（若未来增加）也只能作为同类 Adapter。

## 3. 端口实现要求

- `CatalogReader`：加载后校验并返回只读目录；记录上游版本。
- `SimulationResultStore`：首期内存实现可按 `simulationId + revision` 索引，结果发布后不可变。
- `Clock`：只用于显示或日志，不参与模拟积分；模拟时间来自输入 offset。
- DTO mapper：输入和输出两侧各自校验，避免把不合法对象传入下一层。

## 4. 错误、生命周期和性能

Adapter 将解析失败、超时、取消、缓存未命中转换为协议规定的稳定错误；不把堆栈或文件路径泄漏给 UI。限制由 `capabilities` 返回，超限不得静默裁剪。Worker 的缓存淘汰不能影响正在使用的 revision；淘汰后的查询返回明确 `QUERY_RESULT_EXPIRED`。

## 5. 黑盒验收

先写 Adapter 黑盒测试：合法/非法 Worker 消息、取消竞态、过期响应丢弃、RIIC-Web 映射稳定 ID、缺失字段拒绝、程序 API 与 Worker 结果一致、序列化十进制字符串不丢精度、缓存发布后不可变。Adapter 测试不得通过内部领域实现细节断言。
