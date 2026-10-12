# presentation 层施工

## 1. 目标与边界

presentation 是 View 与 Controller。它保留排班编辑、手动入宿、换心情设置、闲置入宿设置、时间轴回放、心情曲线、干员详情和错误展示。它不持有领域真源，不实现候选算法、默认值、心情公式或入宿规则。

## 2. 推荐文件与职责

```text
src/presentation/
  controller/       # 用户动作到 application command/query
  view-model/       # DTO 到显示模型的纯转换
  settings/         # 表单状态、字段错误和草稿
  schedule/         # 排班/手动台账编辑
  playback/         # 时间轴、快照和曲线
  views/            # 框架相关 View
```

### 2.1 Controller

Controller 接收点击、拖拽、表单和播放动作，调用 application；维护草稿 revision、请求取消和过期响应判断。它只协调，不决定业务结果。手动入宿提交 `editSchedule`；换心情/闲置入宿设置修改输入草稿并重新 `simulate`。

### 2.2 View model

View model 将十进制字符串格式化为图表数值，将稳定错误码映射为本地化文案，将曲线 `before/after` 节点渲染为离散跳变。它不能改变数值、排序候选或补全缺失数据。

### 2.3 设置界面

设置 UI 从 `settingsSchema` 读取默认值、选项和限制；保留入宿设置、换心情设置、黑名单、锁定位置等能力。字段错误按 `path` 定位；不可用选项由核心能力或校验结果解释，不能通过隐藏规则改变行为。

## 3. 状态分离

- UI 状态：选中干员、播放位置、缩放、展开面板和未提交表单。
- 应用输入：排班草稿、手动台账、初始心情、换心情/闲置入宿设置。
- 核心结果：不可变 `simulationId + revision`、曲线、快照、事件和解释。

UI 可以本地持久化草稿，但不得持久化并重新解释核心结果。输入变化必须丢弃或标记旧结果；新结果返回前不得让旧响应覆盖当前 revision。

## 4. 黑盒验收

先写 UI 黑盒测试：手动入宿更新基线编辑状态；换心情和闲置设置只改变模拟输入；设置 schema 正确渲染默认值和限制；曲线显示同刻 before/after；错误按字段路径展示；播放切换快照不修改结果；过期响应不会覆盖最新结果；UI 可替换 Worker 而不改变行为。
