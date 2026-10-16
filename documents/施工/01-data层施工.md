# data 层施工

## 1. 目标与边界

data 层把上游静态资料装配成版本化、只读、可索引的目录。它不计算心情、不决定候选、不保存某次模拟的实时布局，也不依赖 UI、Worker、HTTP 或存储实现。

事实来源优先级：`Kengxxiao/ArknightsGameData/zh_CN/gamedata/excel/` → 当前项目经过审阅的规范化记录 → 明确标记为待核实的占位。任何事实缺口都必须停在数据核验，不得由实现者猜测。

## 2. 推荐 Module 与 Interface

当前文件：

```text
src/data/
  catalog-types.ts
  upstream-normalizer.ts
  upstream-types.ts
  catalog-generator.ts
  validator.ts
  catalog-builder.ts
```

data 仅包含纯校验、规范化、序列化和快照构建。文件读取、写入和 SHA-256 位于
`src/infrastructure/catalog/`；该目录的 `FileCatalogReader` 实现 application 端口。

生成命令：

```text
npm run data:build -- --source <ArknightsGameData>/zh_CN/gamedata/excel --output data/generated --generated-at <ISO-8601>
```

生成目录包含 `operators.json`、`skills.json`、`facilities.json`、`factions.json`、`constants.json` 和 `manifest.json`。
`manifest.json` 记录四份输入文件的逻辑文件名、字节数、SHA-256、生成时间、目录版本、数量和产物哈希；不记录本机绝对路径。
四份输入为 `building_data.json`、`character_table.json`、`gamedata_const.json`、`handbook_team_table.json`。
当前 schema 为 `2`、generator 为 `2.0.0`；目录身份由输入指纹和这两个版本共同计算，不含生成时间。schema 1 不静默兼容，须重新生成。
原始上游 JSON 不复制到项目仓库，生成物也不得手工修改。

`CatalogReader` Interface 由 [`03-application层施工.md`](03-application层施工.md) 的 `application/ports.ts` 定义，并由 infrastructure 的目录 Adapter 实现。data 只产出不可变记录和快照，不拥有外部读取端口。规划的数据结构：

```ts
type CatalogVersion = { id: string; source: string; generatedAt: string };
interface CatalogSnapshot {
  readonly version: CatalogVersion;
  readonly constants: CatalogConstants;
  operator(id: string): OperatorRecord | undefined;
  skill(id: string): SkillRecord | undefined;
  facilityType(id: string): FacilityTypeRecord | undefined;
  faction(id: string): FactionRecord | undefined;
}
```

快照提供闭包索引与深复制/深冻结，不暴露 Map。`FileCatalogReader` 读取清单及固定逻辑文件名，校验格式版本、输入身份、字节指纹、数量、字段和引用，再调用 builder。application 将快照交给 domain 的模拟用例仍待实现；domain 不得直接打开文件、解析上游 JSON 或依赖 `CatalogReader`。

## 3. 数据对象与不变量

- `OperatorRecord`：稳定 `operatorId`、目录显示信息、阵营 ID、可用技能 ID；姓名仅为展示字段。
- `SkillRecord`：稳定 `skillId`、来源字段、分类、模板参数、适用条件；不能把干员名称特判写在规则引擎。
- `FacilityTypeRecord`：设施类型、容量模型、位次模型、可用规则引用。
- `FactionRecord`：稳定阵营 ID 和成员 ID 列表；成员关系必须能回溯来源。
- 所有数值明确单位、精度和来源；涉及模拟的十进制数采用字符串或定点表示。
- 目录内 ID 唯一；引用必须存在；未知字段可保留在原始层但不能未经审阅进入领域层。
- 目录构建完成后深冻结或使用不可变结构；运行时不能修改。

## 4. 施工步骤

1. 登记上游仓库版本；当输入是无 `.git` 的本地归档时，以所需输入文件 SHA-256、字节数和生成时间作为可复现版本依据，不伪造 commit。
2. 将原始文件映射到内部 raw 类型，不在 loader 中写业务规则。
3. normalizer 统一 ID、枚举、单位和十进制格式。
4. validator 检查重复 ID、缺失引用、非法数值、未知版本和待核实字段。
5. builder 生成索引并输出 `CatalogVersion` 与生成元数据。
6. infrastructure Adapter 使用 data builder 形成只读 `CatalogSnapshot`，并实现 application 定义的 `CatalogReader`；application 将快照交给 domain。domain 不能看到 raw 对象或目录 Adapter。

## 5. 错误与兼容

目录缺失、版本不匹配、关键字段缺失、引用不存在应分别产生稳定错误码，例如 `CATALOG_NOT_FOUND`、`CATALOG_VERSION_UNSUPPORTED`、`CATALOG_FIELD_INVALID`、`CATALOG_REFERENCE_MISSING`。不能静默使用零值或显示名兜底。新增可选字段允许向后兼容；删除或改变语义必须提升目录版本。

## 6. 扩展方式

新增干员/技能/设施优先添加目录记录、模板参数或策略引用。只有行为组合确实无法由现有模板表达时，才增加新的策略类型，并为其增加黑盒测试。禁止在通用模拟循环中追加名称分支。

## 7. 黑盒验收

实现阶段必须先写测试验证：同一原始数据生成相同版本和索引；重复/缺失引用被拒绝；未知技能不会静默变成无技能；目录对象不可被调用方修改；上游字段变更能给出明确错误。测试通过后才更新数据文档和版本记录。

## 8. 当前实现状态

已实现运行时校验、统一不可变快照、schema 2 生成与文件 `CatalogReader`。当前归档生成 432 名有基建数据的干员、763 条基建技能和 13 类设施；阵营数量以 manifest 为准。技能全部标为 `unmodeled`，常量使用 `{rawValue, sourcePath, unitStatus: "unverified"}`，尚未成为可执行公式。

字段校验允许新增未知字段；必需字段类型变化、未知解锁阶段、非有限数、非安全整数容量、缺失引用、ID 与表键不一致、宿舍两份阶段表长度不一致均拒绝。上游特殊设施的 `description: null` 与 `maxCount: -1` 保留原值，不推断哨兵含义。干员解锁保留组号，阵营保留 nation/group/team 来源；索引与细项相互校验。

技能 ID 索引必须去重，并与解锁明细中的技能 ID 集合一致；同一技能允许保留不同来源路径的解锁明细，但同一干员的解锁明细不得重复使用 `sourcePath`，否则返回 `CATALOG_FIELD_INVALID`。

阵营成员仅覆盖本目录干员的上游直接关联（`membershipScope: "catalog-direct-links"`），不代表技能成员集合。完整技能语义分类、十进制定点转换与领域模拟仍待施工。

读取失败以 `CatalogValidationError.detail` 提供 `{code,path,message}`；路径为逻辑文件或字段路径，不包含本机路径。哈希用于一致性检查，不是来源认证或数字签名。生成器先写数据、最后写清单；更新不是事务原子替换，读取中间状态时 Reader 拒绝不一致目录，调用方需待生成完成后再读。
