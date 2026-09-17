# plan_compute_v4 示例与字段注释

参考 schema: [contracts/plan-compute-v4.schema.json](../contracts/plan-compute-v4.schema.json#L1-L400)

**示例 JSON**

```json
{
  "schema_version": 4,
  "layout": {
    "template": "default_base",
    "drone_cap": 150,
    "scenario": {
      "elite_facility_count": 2,
      "sui_facility_count": 1,
      "dorm_occupant_count": 20,
      "base_workforce": ["operator_001", "operator_002"],
      "initial_global": {
        "virtual_power": 1000,
        "matatabi": 5
      }
    },
    "rooms": [
      { "id": "r1", "kind": "control_center", "level": 5 },
      { "id": "r2", "kind": "factory", "level": 3, "product": { "factory": { "recipe": "all" } } },
      { "id": "r3", "kind": "trade_post", "level": 4, "product": { "trade": { "order": "gold" } } },
      { "id": "r4", "kind": "dormitory", "level": 2, "dorm_beds": 10, "dorm_ambience_level": 3 }
    ]
  },
  "operbox": [
    { "id": "op_001", "name": "Amiya", "elite": 2, "level": 90, "own": true, "potential": 6, "rarity": 6 },
    { "id": "op_002", "name": "Exusiai", "elite": 2, "level": 90, "own": true, "potential": 6, "rarity": 6 },
    { "id": "op_003", "name": "Trainee", "elite": 0, "level": 1, "own": false, "potential": 1, "rarity": 1 }
  ],
  "labels": {
    "layout": "示例基地",
    "operbox": "示例干员池"
  },
  "options": {
    "rotation": "abc_12_6_6",
    "top": 20,
    "system_preferences": { "preferred_operator": "op_001" },
    "maa_title": null,
    "fiammetta_enable": true,
    "assert_invariants": false
  }
}
```

**字段说明**
- **`schema_version`**: : 整数，固定为 `4`，用以标记 schema 版本。

- **`layout`**: : 基地蓝图对象（`base_blueprint`），包含模板、无人机上限、场景和房间列表。
  - **`template`**: : string|null，可选，表示使用的模板名（例如 `default_base`）。
  - **`drone_cap`**: : integer，基地无人机容量上限（默认 135）。
  - **`scenario`**: : 场景参数对象（可选），用于指定初始全局资源和约束：
    - **`elite_facility_count`**: : u8|null，精英设施数量（可为 null 表示未指定）。
    - **`sui_facility_count`**: : u8|null，特殊设施计数。
    - **`dorm_occupant_count`**: : u8|null，宿舍当前住户数。
    - **`base_workforce`**: : string[]，可预置若干基础工位占用者（字符串 ID）。
    - **`initial_global`**: : object，键为 `global_resource_key` 枚举，值为数字（表示初始全局资源量），例如 `virtual_power`、`matatabi` 等。
  - **`rooms`**: : 房间数组（1..64），每个元素为 `room_blueprint`：
    - **`id`**: : string，房间唯一标识。
    - **`kind`**: : 枚举，房间种类（control_center、trade_post、factory、power_plant、dormitory、office、meeting_room、training_room、workshop）。
    - **`level`**: : u8，房间等级。
    - **`product`**: : 可选，null 或 `trade_product` 或 `factory_product`（用于 trade_post 或 factory 指定产出）。
    - **`dorm_beds`** / **`dorm_ambience_level`**: : 可选，仅适用于 `dormitory`。

- **`operbox`**: : 干员列表（1..1000），每项为 `operbox_entry`：
  - **`id`**: : string，干员唯一 ID（热路径使用整数 OperatorId，但在输入中保留可读 ID）。
  - **`name`**: : string，干员名称（仅供可读性）。
  - **`elite`**: : integer，晋升等级 0-2。
  - **`level`**: : integer，干员等级 1-90。
  - **`own`**: : boolean，是否拥有此干员。
  - **`potential`**: : integer，潜能档位 1-6。
  - **`rarity`**: : integer，稀有度 1-6。

- **`labels`**: : 可选对象，为 `layout` / `operbox` 提供人类可读的标签（string|null）。

- **`options`**: : 可选对象，控制求解器行为：
  - **`rotation`**: : 枚举，排班轮换策略（例如 `abc_12_6_6`），默认 `abc_12_6_6`。
  - **`top`**: : integer，返回结果数上限（1..100，默认 20）。
  - **`system_preferences`**: : object，键/值均为字符串，用于传递平台或系统偏好设置（例如 `preferred_operator`）。
  - **`maa_title`**: : string|null，可选标题字段。
  - **`fiammetta_enable`**: : boolean，功能开关，默认 true。
  - **`assert_invariants`**: : boolean，调试开关，默认 false。

**使用建议与适配（给其他 AI / 下游系统）**
- 人类可读文档：使用本文件 `plan_compute_example_v4_annotated.md` 作为字段字典。
- 机器友好方案：不要在热路径中使用字符串匹配或按名字路由；输入层应由预处理器将字符串 ID 映射为内部整数 ID（`OperatorId` 等）。
- 传输格式：对于需要字段描述的场景，推荐同时发送两部分：`meta`（字段描述）和 `payload`（符合 schema 的真实数据）。详见 `plan_compute_example_v4_machine.json`。
- 可变体建议：自动生成多组 `operbox`（用于压力测试）或不同 `rotation` 值用于策略对比。

**注意事项**
- 本文件给出的是示例与注释，实际生产使用时请以 [contracts/plan-compute-v4.schema.json](../contracts/plan-compute-v4.schema.json#L1-L400) 为准。
- 严格遵守 schema 的 `additionalProperties: false` 规则，避免在 payload 中插入非 schema 字段（但 machine wrapper 的 `meta` 可放在 wrapper 顶层）。
